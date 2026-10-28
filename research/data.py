"""Historical 1-minute data: download from Dhan (resumable), store as Parquet, load per trading day.

Layout (data/history/):
  meta.json                      what is downloaded, synthetic flag, lot sizes
  under/<SYM>.parquet            ts, o, h, l, c            (index, IST minute start)
  opts/<SYM>.parquet             ts, rel, side, strike, o, h, l, c, iv, oi, vol, spot  (nearest expiry, ATM-5..ATM+5)
  raw/...                        downloaded chunks (resume support)
"""
from __future__ import annotations

import datetime as dt
import json
import math
import random
from pathlib import Path

from live.clock import IST

UNDERLYINGS = {   # sym: (option instrument, expiry flag used for the rolling nearest expiry)
    "NIFTY": ("OPTIDX", "WEEK"),
    "BANKNIFTY": ("OPTIDX", "MONTH"),
    "FINNIFTY": ("OPTIDX", "MONTH"),
}
RANGE = 5          # strikes each side of ATM (wings for hedged selling need room)
RELS = [f"ATM{'+' if k > 0 else '-' if k < 0 else ''}{abs(k) if k else ''}" for k in range(-RANGE, RANGE + 1)]
REL_NUM = {r: k for r, k in zip(RELS, range(-RANGE, RANGE + 1))}
STEP = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}
DEFAULT_LOTS = {"NIFTY": 65, "BANKNIFTY": 30, "FINNIFTY": 60}


def hist_dir(data_dir) -> Path:
    d = Path(data_dir) / "history"
    (d / "under").mkdir(parents=True, exist_ok=True)
    (d / "opts").mkdir(parents=True, exist_ok=True)
    (d / "raw").mkdir(parents=True, exist_ok=True)
    return d


def read_meta(data_dir) -> dict:
    f = hist_dir(data_dir) / "meta.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            pass
    return {}


def write_meta(data_dir, meta):
    (hist_dir(data_dir) / "meta.json").write_text(json.dumps(meta, indent=1, default=str))


# ---------------------------------------------------------------- timestamps
def _to_frame(s: dict, extra=()):
    import pandas as pd
    if not s or not s.get("timestamp"):
        return pd.DataFrame()
    df = pd.DataFrame({"ts": s["timestamp"], "o": s.get("open"), "h": s.get("high"), "l": s.get("low"),
                       "c": s.get("close")})
    for k, col in extra:
        if s.get(k) is not None and len(s[k]) == len(df):
            df[col] = s[k]
    df["ts"] = df["ts"].astype("int64")
    return df


def normalise_ts(df):
    """Make ts the epoch of the IST minute start, whatever offset the source used (first bar = 09:15)."""
    import pandas as pd
    if df.empty:
        return df
    t = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(IST)
    first = t.groupby(t.dt.date).min()
    mins = (first.dt.hour * 60 + first.dt.minute).mode()
    if len(mins):
        off = int(mins.iloc[0]) - (9 * 60 + 15)
        if off and off % 30 == 0 and abs(off) <= 600:
            df = df.copy()
            df["ts"] = df["ts"] - off * 60
    df["ts"] = (df["ts"] // 60) * 60
    return df.drop_duplicates(subset=[c for c in ("ts", "rel", "side") if c in df.columns]).sort_values("ts")


# ---------------------------------------------------------------- download
def download(data_dir, client_id, token, start: dt.date, syms=("NIFTY", "BANKNIFTY"), log=print, should_stop=lambda: False,
             progress=lambda done, total: None):
    import pandas as pd
    from .dhan import INDEX_IDS, Dhan, load_master, lot_sizes, series
    d = hist_dir(data_dir)
    api = Dhan(client_id, token)
    log("Checking the Dhan login…")
    api.check()
    try:
        lots = lot_sizes(load_master(d))
    except Exception:
        lots = {}
    end = dt.datetime.now(IST).date() - dt.timedelta(days=1)
    jobs = []
    for sym in list(syms) + ["INDIAVIX"]:
        a = start
        while a <= end:
            b = min(end, a + dt.timedelta(days=85))
            jobs.append(("under", sym, None, None, a, b))
            a = b + dt.timedelta(days=1)
    for sym in syms:
        a = start
        while a <= end:
            b = min(end, a + dt.timedelta(days=27))
            for rel in RELS:
                for side in ("CE", "PE"):
                    jobs.append(("opt", sym, rel, side, a, b))
            a = b + dt.timedelta(days=1)
    total = len(jobs)
    done = 0
    log(f"{total:,} downloads planned ({', '.join(syms)} from {start}). Already-downloaded parts are skipped.")
    for kind, sym, rel, side, a, b in jobs:
        if should_stop():
            log("Stopped. Run again to continue where it left off.")
            return False
        f = d / "raw" / sym / f"{kind}_{rel or 'x'}_{side or 'x'}_{a:%Y%m%d}.parquet"
        done += 1
        if f.exists():
            progress(done, total)
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        if kind == "under":
            body = api.intraday(INDEX_IDS[sym], "IDX_I", "INDEX", a, b, 1)
            df = _to_frame(series(body))
        else:
            inst, flag = UNDERLYINGS[sym]
            body = api.rolling_option(INDEX_IDS[sym], inst, flag, rel, side, a, b)
            if not series(body) and a > end - dt.timedelta(days=60):
                body = api.rolling_option(INDEX_IDS[sym], inst, flag, rel, side, a, b, expiry_code=0)
            df = _to_frame(series(body), (("iv", "iv"), ("oi", "oi"), ("volume", "vol"), ("strike", "strike"), ("spot", "spot")))
            if not df.empty:
                df["rel"] = REL_NUM[rel]
                df["side"] = side
        df.to_parquet(f, index=False)
        progress(done, total)
        if done % 200 == 0:
            log(f"  {done:,} of {total:,} parts downloaded ({api.calls:,} requests)")
    log("Combining downloaded parts…")
    compile_store(data_dir, syms)
    meta = read_meta(data_dir)
    meta.update(source="dhan", synthetic=False, syms=list(syms), start=str(start), end=str(end),
                lots={**DEFAULT_LOTS, **{k: v for k, v in lots.items() if k in DEFAULT_LOTS}},
                updated=dt.datetime.now(IST).isoformat())
    write_meta(data_dir, meta)
    log("History ready.")
    return True


def compile_store(data_dir, syms):
    import pandas as pd
    d = hist_dir(data_dir)
    for sym in list(syms) + ["INDIAVIX"]:
        parts = sorted((d / "raw" / sym).glob("under_*.parquet"))
        frames = [pd.read_parquet(p) for p in parts]
        frames = [f for f in frames if not f.empty]
        if frames:
            u = normalise_ts(pd.concat(frames, ignore_index=True))
            u.to_parquet(d / "under" / f"{sym}.parquet", index=False, compression="zstd")
        if sym == "INDIAVIX":
            continue
        parts = sorted((d / "raw" / sym).glob("opt_*.parquet"))
        frames = [pd.read_parquet(p) for p in parts]
        frames = [f for f in frames if not f.empty]
        if frames:
            o = normalise_ts(pd.concat(frames, ignore_index=True))
            o.to_parquet(d / "opts" / f"{sym}.parquet", index=False, compression="zstd")


# ---------------------------------------------------------------- loading
class History:
    """Days of one underlying, ready for replay. Load a date range only (workers load their own slice)."""

    def __init__(self, data_dir, sym, start: dt.date | None = None, end: dt.date | None = None, expiry_days=None):
        import pandas as pd
        d = hist_dir(data_dir)
        self.sym = sym
        meta = read_meta(data_dir)
        self.lot = (meta.get("lots") or {}).get(sym) or DEFAULT_LOTS.get(sym, 1)
        self.step = STEP.get(sym, 50)
        self.synthetic = bool(meta.get("synthetic"))
        flt = []
        if start:
            flt.append(("ts", ">=", int(dt.datetime.combine(start - dt.timedelta(days=7), dt.time(0), IST).timestamp())))
        if end:
            flt.append(("ts", "<", int(dt.datetime.combine(end + dt.timedelta(days=1), dt.time(0), IST).timestamp())))
        kw = {"filters": flt} if flt else {}
        u = pd.read_parquet(d / "under" / f"{sym}.parquet", **kw)
        o = pd.read_parquet(d / "opts" / f"{sym}.parquet", columns=["ts", "side", "strike", "c", "iv", "rel", "spot"], **kw)
        vf = d / "under" / "INDIAVIX.parquet"
        v = pd.read_parquet(vf, columns=["ts", "c"], **kw) if vf.exists() else None
        for df in (u, o) + ((v,) if v is not None else ()):
            df["date"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.date
        days = sorted(set(u["date"]) & set(o["date"]))
        self.all_days = days
        self.days = [x for x in days if (not start or x >= start) and (not end or x <= end)]
        self._u = {k: g.sort_values("ts") for k, g in u.groupby("date")}
        self._o = {k: g for k, g in o.groupby("date")}
        self._v = {k: g.sort_values("ts") for k, g in v.groupby("date")} if v is not None else {}
        self.expiry_days = set(expiry_days) if expiry_days is not None else infer_expiries(self._o, days)
        self._exp_sorted = sorted(self.expiry_days)

    def dte(self, day) -> int:
        """Trading days to the next expiry (0 on expiry day)."""
        nxt = next((e for e in self._exp_sorted if e >= day), None)
        if nxt is None:
            return 5
        return sum(1 for x in self.all_days if day < x <= nxt) if nxt in self.all_days else max(0, (nxt - day).days * 5 // 7)

    def day(self, day):
        u = self._u[day]
        o = self._o[day]
        bars = u[["ts", "o", "h", "l", "c"]].to_numpy(dtype=float)
        quotes = {}
        for ts, k, sd, c, iv in zip(o["ts"].to_numpy(), o["strike"].to_numpy(), o["side"].to_numpy(),
                                     o["c"].to_numpy(), o["iv"].to_numpy()):
            quotes.setdefault(int(ts), {})[(float(k), sd)] = (float(c), float(iv))
        vix = self._v.get(day)
        vmap = dict(zip(vix["ts"].astype(int), vix["c"].astype(float))) if vix is not None else {}
        return bars, quotes, vmap

    def prev(self, day):
        i = self.all_days.index(day)
        if i == 0:
            return None, None
        p = self._u.get(self.all_days[i - 1])
        if p is None:
            return None, None
        pv = self._v.get(self.all_days[i - 1])
        return (float(p["c"].iloc[-1]), float(p["h"].max()), float(p["l"].min())), (float(pv["c"].iloc[-1]) if pv is not None and len(pv) else None)


def infer_expiries(opts_by_day: dict, days) -> set:
    """Expiry day = the ATM straddle at the close has almost no time value left."""
    out = set()
    for day in days:
        o = opts_by_day.get(day)
        if o is None:
            continue
        late = o[o["rel"] == 0]
        if late.empty:
            continue
        x = late[late["ts"] == late["ts"].max()]
        ce, pe = x[x["side"] == "CE"], x[x["side"] == "PE"]
        if ce.empty or pe.empty:
            continue
        spot = float(ce["spot"].iloc[0])
        if not spot or spot != spot:
            continue
        tv = float(ce["c"].iloc[0]) + float(pe["c"].iloc[0]) - abs(spot - float(ce["strike"].iloc[0]))
        if tv / spot * 100 < 0.12:
            out.add(day)
    return out


def all_expiries(data_dir, sym) -> tuple:
    """Trading days and expiry days for the whole history (cheap: reads ATM rows only)."""
    import pandas as pd
    d = hist_dir(data_dir)
    o = pd.read_parquet(d / "opts" / f"{sym}.parquet", columns=["ts", "side", "strike", "c", "rel", "spot"],
                        filters=[("rel", "==", 0)])
    o["date"] = pd.to_datetime(o["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.date
    u = pd.read_parquet(d / "under" / f"{sym}.parquet", columns=["ts"])
    udays = set(pd.to_datetime(u["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.date)
    by = {k: g for k, g in o.groupby("date")}
    days = sorted(udays & set(by))
    return days, infer_expiries(by, days)


# ---------------------------------------------------------------- synthetic history (for testing the pipeline)
def synthesize(data_dir, days=300, seed=7, log=print):
    """Made-up history with trend, range and volatile days, priced with Black-Scholes.
    Only for checking that the lab works end to end. Results on it mean nothing."""
    import pandas as pd
    from live.sim import bs
    rng = random.Random(seed)
    d = hist_dir(data_dir)
    start = dt.date.today() - dt.timedelta(days=int(days * 1.45))
    dates = []
    x = start
    while len(dates) < days:
        if x.weekday() < 5:
            dates.append(x)
        x += dt.timedelta(days=1)
    specs = {"NIFTY": (24000.0, 0.13, "WEEK"), "BANKNIFTY": (52000.0, 0.15, "MONTH")}
    vix_rows = []
    vix = 13.0
    for di, day in enumerate(dates):
        vix = max(9, min(30, vix * math.exp(rng.gauss(0, 0.05))))
        for m in range(375):
            ts = int(dt.datetime.combine(day, dt.time(9, 15), IST).timestamp()) + m * 60
            vix_rows.append((ts, vix, vix, vix, vix))
    pd.DataFrame(vix_rows, columns=["ts", "o", "h", "l", "c"]).to_parquet(d / "under" / "INDIAVIX.parquet", index=False)
    for sym, (px, sig, flag) in specs.items():
        step = STEP[sym]
        u_rows, o_rows = [], []
        if flag == "WEEK":
            exps = [x for x in dates if x.weekday() == 1]
        else:
            exps = []
            for x in dates:
                nxt = x + dt.timedelta(days=7)
                if x.weekday() == 1 and nxt.month != x.month:
                    exps.append(x)
        for day in dates:
            exp = next((e for e in exps if e >= day), dates[-1])
            kind = rng.choices(["trend", "range", "volatile", "quiet"], [0.3, 0.4, 0.15, 0.15])[0]
            daily_sig = sig / math.sqrt(252)
            drift = {"trend": rng.choice([-1, 1]) * daily_sig * rng.uniform(0.8, 1.6), "range": 0, "volatile": 0,
                     "quiet": 0}[kind] / 375
            noise = daily_sig / math.sqrt(375) * {"trend": 0.8, "range": 0.9, "volatile": 1.6, "quiet": 0.5}[kind]
            px *= math.exp(rng.gauss(0, daily_sig * 0.4))          # gap
            close_t = dt.datetime.combine(exp, dt.time(15, 30), IST)
            mean = px
            for m in range(375):
                t0 = dt.datetime.combine(day, dt.time(9, 15), IST) + dt.timedelta(minutes=m)
                ts = int(t0.timestamp())
                o = px
                r = drift + rng.gauss(0, noise)
                if kind == "range":
                    r += (mean - px) / px * 0.02
                px *= math.exp(r)
                hi, lo = max(o, px) * (1 + abs(rng.gauss(0, noise / 3))), min(o, px) * (1 - abs(rng.gauss(0, noise / 3)))
                u_rows.append((ts, o, hi, lo, px))
                if m % 1 == 0:
                    T = max((close_t - t0).total_seconds(), 60) / (365 * 86400)
                    atm = round(px / step) * step
                    iv = sig * (1 + (0.25 if kind == "volatile" else 0))
                    for k in range(-RANGE, RANGE + 1):
                        K = atm + k * step
                        for side in ("CE", "PE"):
                            ivk = iv + 0.6 * abs(math.log(K / px))
                            p = bs(px, K, T, ivk, side)[0]
                            o_rows.append((ts, k, side, float(K), p, p * 1.004, p * 0.996, p, ivk * 100, 0.0, 0.0, px))
        pd.DataFrame(u_rows, columns=["ts", "o", "h", "l", "c"]).to_parquet(d / "under" / f"{sym}.parquet", index=False)
        pd.DataFrame(o_rows, columns=["ts", "rel", "side", "strike", "o", "h", "l", "c", "iv", "oi", "vol", "spot"]) \
            .to_parquet(d / "opts" / f"{sym}.parquet", index=False, compression="zstd")
        log(f"Synthetic {sym}: {len(dates)} days")
    write_meta(data_dir, {"source": "synthetic", "synthetic": True, "syms": list(specs), "start": str(dates[0]),
                          "end": str(dates[-1]), "lots": DEFAULT_LOTS, "updated": dt.datetime.now(IST).isoformat()})


def status(data_dir) -> dict:
    d = hist_dir(data_dir)
    meta = read_meta(data_dir)
    files = {p.stem: round(p.stat().st_size / 1e6, 1) for p in list((d / "under").glob("*.parquet")) + list((d / "opts").glob("*.parquet"))}
    raw = sum(1 for _ in (d / "raw").rglob("*.parquet"))
    return {**meta, "files_mb": files, "raw_parts": raw, "ready": bool(meta.get("syms")) and all(
        (d / "opts" / f"{s}.parquet").exists() for s in meta.get("syms", []))}
