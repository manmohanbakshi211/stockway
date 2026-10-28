"""Download and organise historical option data.

Dhan gives *rolling* strikes (ATM-10 ... ATM+10 at each minute). We store them raw by month and rebuild,
for every day, the minute prices of each *fixed* strike, so a backtest can hold a position all day.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .dhan import Dhan

IST_OFFSET = 19800
N_MIN = 375                      # 09:15 .. 15:29
OFFSETS = range(-10, 11)
UNDERLYINGS = {
    "NIFTY": {"flag": "WEEK", "step": 50, "lot": 65},
    "BANKNIFTY": {"flag": "MONTH", "step": 100, "lot": 30},
}


def hist_dir(data_dir) -> Path:
    d = Path(data_dir) / "history"
    d.mkdir(parents=True, exist_ok=True)
    return d


def months(start: dt.date, end: dt.date):
    m = dt.date(start.year, start.month, 1)
    while m <= end:
        nxt = dt.date(m.year + (m.month == 12), m.month % 12 + 1, 1)
        yield m, nxt
        m = nxt


def _frame(arr: dict, off: int, side: str) -> pd.DataFrame | None:
    ts = arr.get("timestamp") or []
    if not ts:
        return None
    n = len(ts)
    cols = {"ts": np.asarray(ts, dtype="int64"), "off": np.full(n, off, dtype="int8"), "side": [side] * n}
    for f in ("open", "high", "low", "close", "iv", "volume", "oi", "strike", "spot"):
        v = arr.get(f) or [None] * n
        cols[f] = pd.to_numeric(pd.Series(v[:n] + [None] * (n - len(v))), errors="coerce").to_numpy(dtype="float64")
    return pd.DataFrame(cols)


def download(dhan: Dhan, data_dir, syms=("NIFTY", "BANKNIFTY"), start: dt.date | None = None,
             end: dt.date | None = None, log=print, should_stop=lambda: False, progress=lambda done, total: None):
    """Fetch all months (skips months already complete). About 42 requests per month per index."""
    end = end or dt.date.today()
    start = start or dt.date(end.year - 3, end.month, 1)
    hd = hist_dir(data_dir)
    jobs = []
    for sym in syms:
        for m0, m1 in months(start, end):
            f = hd / sym / f"raw_{m0:%Y-%m}.parquet"
            complete = f.exists() and m1 <= dt.date.today().replace(day=1)
            if not complete:
                jobs.append((sym, m0, m1, f))
    total = len(jobs) * 42 + len(syms) + 1
    done = 0
    _daily(dhan, hd, list(syms) + ["INDIAVIX"], start, end, log)
    done += len(syms) + 1
    progress(done, total)
    for sym, m0, m1, f in jobs:
        if should_stop():
            log("Stopped. What was downloaded is kept; run again to continue.")
            return
        f.parent.mkdir(parents=True, exist_ok=True)
        to = min(m1, end + dt.timedelta(days=1))
        parts = []
        flag = UNDERLYINGS[sym]["flag"]
        for off in OFFSETS:
            for side in ("CE", "PE"):
                if should_stop():
                    break
                try:
                    arr = dhan.rolling(sym, flag, off, side, m0.isoformat(), to.isoformat())
                except PermissionError:
                    raise
                except Exception as e:
                    log(f"  {sym} {m0:%b %Y} ATM{off:+d} {side}: {e}")
                    arr = {}
                df = _frame(arr, off, side)
                if df is not None:
                    parts.append(df)
                done += 1
                progress(done, total)
        if should_stop():
            continue
        if parts:
            pd.concat(parts, ignore_index=True).to_parquet(f, compression="zstd", index=False)
            log(f"{sym} {m0:%b %Y}: {sum(len(p) for p in parts):,} rows")
        else:
            log(f"{sym} {m0:%b %Y}: no data")
    log("Download complete.")


def _daily(dhan: Dhan, hd: Path, syms, start, end, log):
    for sym in syms:
        try:
            d = dhan.daily(sym, (start - dt.timedelta(days=60)).isoformat(), (end + dt.timedelta(days=1)).isoformat())
        except PermissionError:
            raise
        except Exception as e:
            log(f"Daily {sym}: {e}")
            continue
        ts = d.get("timestamp") or []
        if not ts:
            continue
        df = pd.DataFrame({"date": [dt.datetime.utcfromtimestamp(t + IST_OFFSET).date() for t in ts],
                           **{k: d.get(k) for k in ("open", "high", "low", "close")}})
        (hd / sym).mkdir(parents=True, exist_ok=True)
        df.to_parquet(hd / sym / "daily.parquet", index=False)


def have_data(data_dir) -> dict:
    hd = hist_dir(data_dir)
    out = {}
    for sym in UNDERLYINGS:
        fs = sorted((hd / sym).glob("raw_*.parquet"))
        out[sym] = {"months": len(fs), "first": fs[0].stem[4:] if fs else None, "last": fs[-1].stem[4:] if fs else None,
                    "mb": round(sum(f.stat().st_size for f in fs) / 1e6, 1)}
    return out


# ---------------------------------------------------------------- day books
@dataclass
class Day:
    date: dt.date
    sym: str
    step: float
    spot: np.ndarray                       # N_MIN
    px: dict = field(default_factory=dict)  # (strike, "CE"/"PE") -> {"c","h","l","iv"} arrays
    feats: dict = field(default_factory=dict)

    def atm(self, m: int) -> float:
        return round(self.spot[m] / self.step) * self.step

    def price(self, strike, side, m, kind="c"):
        a = self.px.get((float(strike), side))
        if a is None:
            return math.nan
        return a[kind][m]

    def has(self, strike, side, m) -> bool:
        return not math.isnan(self.price(strike, side, m))


def build_days(raw: pd.DataFrame, sym: str) -> list[Day]:
    if raw is None or raw.empty:
        return []
    step = UNDERLYINGS[sym]["step"]
    raw = raw.dropna(subset=["close", "strike"])
    t = pd.to_datetime(raw["ts"] + IST_OFFSET, unit="s")
    raw = raw.assign(date=t.dt.date, m=((t.dt.hour * 60 + t.dt.minute) - (9 * 60 + 15)).astype(int))
    raw = raw[(raw["m"] >= 0) & (raw["m"] < N_MIN)]
    days = []
    for d, g in raw.groupby("date"):
        spot = np.full(N_MIN, np.nan)
        sp = g.groupby("m")["spot"].median()
        spot[sp.index.to_numpy()] = sp.to_numpy()
        spot = pd.Series(spot).ffill().bfill().to_numpy()
        if np.isnan(spot).all():
            continue
        px = {}
        for (k, side), h in g.groupby(["strike", "side"]):
            h = h.drop_duplicates("m")
            arrs = {}
            for f, col in (("c", "close"), ("h", "high"), ("l", "low"), ("iv", "iv")):
                a = np.full(N_MIN, np.nan)
                a[h["m"].to_numpy()] = h[col].to_numpy()
                arrs[f] = pd.Series(a).ffill().to_numpy()
            px[(float(k), side)] = arrs
        days.append(Day(date=d, sym=sym, step=step, spot=spot, px=px))
    return days


def iter_days(data_dir, sym: str, start=None, end=None):
    hd = hist_dir(data_dir) / sym
    for f in sorted(hd.glob("raw_*.parquet")):
        ym = f.stem[4:]
        if start and ym < f"{start:%Y-%m}":
            continue
        if end and ym > f"{end:%Y-%m}":
            continue
        for d in build_days(pd.read_parquet(f), sym):
            if (not start or d.date >= start) and (not end or d.date <= end):
                yield d


# ---------------------------------------------------------------- day features (known before entry)
def day_table(data_dir, sym: str, days: list[Day] | None = None) -> pd.DataFrame:
    """One row per day: open/close, ATM straddle at open/close, expiry flag (from the straddle rolling over)."""
    rows = []
    for d in days if days is not None else iter_days(data_dir, sym):
        def straddle(m):
            k = d.atm(m)
            c, p = d.price(k, "CE", m), d.price(k, "PE", m)
            return c + p if not (math.isnan(c) or math.isnan(p)) else math.nan
        ivs = [d.price(d.atm(5), s, 5, "iv") for s in ("CE", "PE")]
        rows.append({"date": d.date, "open": d.spot[0], "close": d.spot[-1], "high": np.nanmax(d.spot),
                     "low": np.nanmin(d.spot), "spot_920": d.spot[5], "str_open": straddle(5), "str_close": straddle(N_MIN - 6),
                     "iv_open": np.nanmean(ivs) if not all(math.isnan(x) for x in ivs) else math.nan})
    t = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    if t.empty:
        return t
    t["str_open_pct"] = t["str_open"] / t["spot_920"] * 100
    nxt = t["str_open"].shift(-1)
    t["is_expiry"] = (nxt > 1.6 * t["str_close"]) & (t["str_close"] < 0.6 * t["str_open"])
    # trading days to the next expiry
    dte, n = [], None
    for e in reversed(t["is_expiry"].tolist()):
        n = 0 if e else (None if n is None else n + 1)
        dte.append(n)
    t["dte"] = list(reversed(dte))
    t["prev_close"] = t["close"].shift(1)
    t["gap_pct"] = (t["open"] / t["prev_close"] - 1) * 100
    r = np.log(t["close"] / t["close"].shift(1))
    t["rv20"] = r.rolling(20).std() * math.sqrt(252) * 100
    t["rv20"] = t["rv20"].shift(0)            # uses closes up to yesterday at entry (shifted below)
    t["rv20_prev"] = t["rv20"].shift(1)
    t["range_pct"] = (t["high"] - t["low"]) / t["open"] * 100
    return t


def vix_table(data_dir) -> pd.DataFrame:
    f = hist_dir(data_dir) / "INDIAVIX" / "daily.parquet"
    if not f.exists():
        return pd.DataFrame(columns=["date", "vix_open", "vix_prev", "vix_pct"])
    v = pd.read_parquet(f).sort_values("date").reset_index(drop=True)
    v["vix_prev"] = v["close"].shift(1)
    v["vix_pct"] = v["vix_prev"].rolling(250, min_periods=60).apply(lambda s: (s <= s.iloc[-1]).mean() * 100, raw=False)
    return v.rename(columns={"open": "vix_open"})[["date", "vix_open", "vix_prev", "vix_pct"]]


def save_json(path, obj):
    Path(path).write_text(json.dumps(obj, default=str))
