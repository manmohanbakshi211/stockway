"""The 5-year Lab (challenger): Dhan's expired-options archive (1-minute, from 2021) for Nifty, Bank Nifty and the
F&O stocks, joined with the Upstox history already on disk, in its own folder (data/lab5y).

It never touches data/history or data/lab: the current Lab and its live rules keep running unchanged.

How the download makes sure nothing is missed (2.21):
  - Trading days come from Dhan's own Nifty daily candles (one call a year), so a holiday is never guessed and a
    failed call is never mistaken for a holiday.
  - A manifest (data/lab5y/manifest.json) records, for every symbol and month, which trading days are on disk and
    which days Dhan really has no data for. Each run fetches exactly the missing days: whole months, holes inside a
    month, and the newest days of the current month.
  - A day is saved only when every strike of it came back (data or a real "no data"); a network error leaves it
    missing, so the next run fetches it. After 3 network errors in a row the download pauses and retries the same
    day (sleep, Wi-Fi drop), instead of skipping ahead.
  - Every finished day is written to disk at once, so closing the app loses at most the day in progress.
  - One shared counter keeps all Dhan calls (download + live news / movers) under Dhan's limits (lab/dhan_budget.py).

Universe: Nifty, Bank Nifty and every NSE stock with options. The 30 most liquid first (ATM±3), then the rest by
turnover (next 50 ATM±3, the others ATM±1), newest months first. A disk guard stops at 10 GB free.

Market data only (Dhan Data API): no orders, no account data.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import shutil
import threading
import time
from pathlib import Path

import pandas as pd

from . import dhan_budget as B
from .data import _frame, hist_dir, months

LAB5Y = "lab5y"
START = dt.date(2021, 1, 1)
SCRIP_MASTER = "https://images.dhan.co/api-data/api-scrip-master.csv"
INDEX_OFFSETS = range(-10, 11)
STOCK_OFFSETS = range(-3, 4)
NARROW_OFFSETS = range(-1, 2)
INDEX_FLAG = {"NIFTY": "WEEK", "BANKNIFTY": "MONTH"}
TIER_WIDE = 80                 # this many stocks (by turnover) get ATM±3; the rest ATM±1
DAILY_CAP = B.DOWNLOAD_CAP     # calls a day for the download (Dhan allows 100,000; the rest stay for live news / movers)
MIN_FREE_GB = 10.0
EMPTY_FINAL_DAYS = 3           # a "no data" answer counts as final only when asked 3+ days after that day
PAUSES = (60, 120, 300, 300, 600)

_man_lock = threading.Lock()


def root(data_dir) -> Path:
    d = Path(data_dir) / LAB5Y
    (d / "history").mkdir(parents=True, exist_ok=True)
    return d


def _json(f: Path, default):
    try:
        return json.loads(f.read_text()) if f.exists() else default
    except Exception:
        return default


def _save_json(f: Path, obj):
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")))
    tmp.replace(f)


# ---------------------------------------------------------------- Dhan's list of instruments
def _scrips(cache_dir, fetch=True) -> Path | None:
    f = Path(cache_dir) / f"dhan_scrips_{dt.date.today():%Y%m%d}.csv"
    if not f.exists():
        old = sorted(Path(cache_dir).glob("dhan_scrips_*.csv"))
        if not fetch:
            return old[-1] if old else None
        import requests
        try:
            r = requests.get(SCRIP_MASTER, timeout=120)
            r.raise_for_status()
        except Exception:
            return old[-1] if old else None
        f.write_bytes(r.content)
        for o in old:
            o.unlink(missing_ok=True)
    return f


def stock_ids(cache_dir, syms: list, fetch=True) -> dict:
    """symbol -> {"id": Dhan security id of the stock, "lot": option lot}. With syms=None: every NSE stock with options."""
    f = _scrips(cache_dir, fetch)
    if not f:
        return {}
    want = set(syms) if syms is not None else None
    ids, lots = {}, {}
    with open(f, newline="", encoding="utf-8", errors="ignore") as fh:
        for row in csv.DictReader(fh):
            ex = row.get("SEM_EXM_EXCH_ID")
            inst = row.get("SEM_INSTRUMENT_NAME")
            sym = row.get("SEM_TRADING_SYMBOL", "")
            if ex == "NSE" and inst == "EQUITY" and row.get("SEM_SERIES", "EQ") in ("EQ", ""):
                if want is None or sym in want:
                    ids[sym] = int(float(row["SEM_SMST_SECURITY_ID"]))
            elif ex == "NSE" and inst == "OPTSTK":
                und = sym.split("-")[0]
                if (want is None or und in want) and und not in lots:
                    try:
                        lots[und] = int(float(row.get("SEM_LOT_UNITS") or 0))
                    except ValueError:
                        lots[und] = 0
    keys = syms if syms is not None else sorted(lots)
    return {s: {"id": ids[s], "lot": lots.get(s) or 1} for s in keys if s in ids and (syms is not None or s in lots)}


def universe(dhan, data_dir, stocks=None, log=print) -> list:
    """[(sym, offsets)] in download order. Given stocks: just those (ATM±3). Otherwise: the 30 most liquid, then every
    other F&O stock by turnover (Dhan daily candles, refreshed weekly)."""
    from .stock_hist import DEFAULT
    if stocks is not None:
        return [(s, STOCK_OFFSETS) for s in stocks]
    ids = stock_ids(Path(data_dir), None)
    r = root(data_dir)
    cache = _json(r / "universe.json", {})
    if not cache.get("turnover") or _age_days(cache.get("at")) >= 7:
        turn = {}
        end = dt.date.today()
        for s, v in ids.items():
            try:
                d = dhan.daily(s, (end - dt.timedelta(days=45)).isoformat(), end.isoformat(), security_id=v["id"],
                               segment="NSE_EQ", instrument="EQUITY")
                cl, vol = d.get("close") or [], d.get("volume") or []
                xs = [c * v_ for c, v_ in zip(cl[-20:], vol[-20:]) if c and v_]
                turn[s] = sum(xs) / len(xs) if xs else 0.0
            except PermissionError:
                raise
            except Exception:
                turn[s] = cache.get("turnover", {}).get(s, 0.0)
        cache = {"turnover": turn, "at": dt.date.today().isoformat()}
        _save_json(r / "universe.json", cache)
        log(f"Ranked {len(turn)} F&O stocks by turnover")
    turn = cache.get("turnover") or {}
    rest = sorted((s for s in ids if s not in DEFAULT), key=lambda s: -turn.get(s, 0.0))
    order = [s for s in DEFAULT if s in ids] + rest
    return [(s, STOCK_OFFSETS if i < TIER_WIDE else NARROW_OFFSETS) for i, s in enumerate(order)]


def _age_days(iso) -> int:
    try:
        return (dt.date.today() - dt.date.fromisoformat(iso)).days
    except Exception:
        return 999


# ---------------------------------------------------------------- trading days
class Calendar:
    """NSE trading days from Dhan's Nifty daily candles (cached per year). Falls back to weekdays when Dhan can't say."""

    def __init__(self, dhan, r: Path):
        self.d, self.f = dhan, r / "trading_days.json"
        self.c = _json(self.f, {})

    def year(self, y: int) -> set | None:
        yesterday = dt.date.today() - dt.timedelta(days=1)
        rec = self.c.get(str(y))
        last = dt.date(y, 12, 31) if y < yesterday.year else yesterday
        if rec and rec.get("to") >= last.isoformat():
            return {dt.date.fromisoformat(x) for x in rec["days"]}
        if self.d is None or not hasattr(self.d, "daily"):
            return None
        try:
            res = self.d.daily("NIFTY", dt.date(y, 1, 1).isoformat(), (last + dt.timedelta(days=1)).isoformat())
        except PermissionError:
            raise
        except Exception:
            return None
        days = sorted({(dt.datetime.utcfromtimestamp(int(t) + 19800)).date() for t in (res.get("timestamp") or [])})
        days = [x for x in days if x <= last]
        if not days:
            return None
        self.c[str(y)] = {"to": last.isoformat(), "days": [x.isoformat() for x in days]}
        _save_json(self.f, self.c)
        return set(days)

    def days(self, m0: dt.date, m1: dt.date) -> list:
        """Trading days in [m0, m1) up to yesterday (Dhan publishes a day's archive after that day)."""
        stop = min(m1, dt.date.today())
        ys = self.year(m0.year)
        out, d = [], m0
        while d < stop:
            if (d in ys) if ys is not None else d.weekday() < 5:
                out.append(d)
            d += dt.timedelta(days=1)
        return out


# ---------------------------------------------------------------- what is on disk
class Manifest:
    """'SYM|YYYY-MM' -> {"d": [days of month on disk], "e": {day: "date asked"} (Dhan has no data), "f": {day: errors}}."""

    def __init__(self, r: Path):
        self.r, self.f = r, r / "manifest.json"
        self.m = _json(self.f, {})
        self.dirty = 0

    def rec(self, sym, m0: dt.date) -> dict:
        k = f"{sym}|{m0:%Y-%m}"
        if k not in self.m:
            f = self.r / "history" / sym / f"raw_{m0:%Y-%m}.parquet"
            self.m[k] = {"d": sorted(file_days(f)), "e": {}, "f": {}}
            self.dirty += 1
        return self.m[k]

    def done(self, sym, day: dt.date) -> bool:
        rec = self.rec(sym, day.replace(day=1))
        if day.day in rec["d"]:
            return True
        asked = rec["e"].get(str(day.day))
        return bool(asked) and (dt.date.fromisoformat(asked) - day).days >= EMPTY_FINAL_DAYS

    def add(self, sym, day: dt.date, empty=False):
        rec = self.rec(sym, day.replace(day=1))
        if empty:
            rec["e"][str(day.day)] = dt.date.today().isoformat()
        elif day.day not in rec["d"]:
            rec["d"] = sorted(rec["d"] + [day.day])
        rec["f"].pop(str(day.day), None)
        self.dirty += 1

    def fail(self, sym, day: dt.date) -> int:
        rec = self.rec(sym, day.replace(day=1))
        n = rec["f"][str(day.day)] = rec["f"].get(str(day.day), 0) + 1
        self.dirty += 1
        return n

    def save(self, force=False):
        if self.dirty and (force or self.dirty >= 20):
            with _man_lock:
                _save_json(self.f, self.m)
            self.dirty = 0


def file_days(f: Path) -> set:
    """Days of the month (ints) present in a raw month file."""
    if not f.exists():
        return set()
    try:
        ts = pd.read_parquet(f, columns=["ts"])["ts"]
        if not len(ts):
            return set()
        return set(pd.to_datetime(ts.astype("int64") + 19800, unit="s").dt.day.unique().tolist())
    except Exception:
        return set()


def _write_day(f: Path, df: pd.DataFrame):
    """Merge one day into its month file, atomically (a crash leaves the old file intact)."""
    f.parent.mkdir(parents=True, exist_ok=True)
    if f.exists():
        try:
            old = pd.read_parquet(f)
            if len(old) and "off" in old.columns:
                df = pd.concat([old, df], ignore_index=True).drop_duplicates(["ts", "off", "side"], keep="last")
        except Exception:
            pass
    tmp = f.with_name(f.name + ".tmp")
    df.sort_values(["ts", "off", "side"]).to_parquet(tmp, compression="zstd", index=False)
    os.replace(tmp, f)


# ---------------------------------------------------------------- Upstox history (indices only from 2022)
def _first_upstox(hd: Path) -> dt.date | None:
    """First day covered by the Upstox per-expiry files (raw_YYYY-MM-DD) already downloaded."""
    fs = sorted(p.stem[4:] for p in hd.glob("raw_*.parquet") if len(p.stem) > 11)
    return dt.date.fromisoformat(fs[0]) - dt.timedelta(days=35) if fs else None


def link_upstox(data_dir, syms):
    """The 5-year folder sees the Upstox files through links (no copies, nothing changed in data/history)."""
    src, dst = hist_dir(data_dir), root(data_dir) / "history"
    for sym in list(syms) + ["INDIAVIX"]:
        if not (src / sym).exists():
            continue
        (dst / sym).mkdir(parents=True, exist_ok=True)
        for p in (src / sym).glob("*.parquet"):
            if p.stem.startswith("raw_") and len(p.stem) <= 11:
                continue                                   # monthly Dhan files of the old Lab: not mixed in
            q = dst / sym / p.name
            if not q.exists():
                try:
                    os.symlink(p, q)
                except OSError:
                    q.write_bytes(p.read_bytes())
        m = src / sym / "meta.json"
        if m.exists() and not (dst / sym / "meta.json").exists():
            (dst / sym / "meta.json").write_text(m.read_text())


def _months_for(data_dir, sym, end: dt.date) -> list:
    stop = (_first_upstox(hist_dir(data_dir) / sym) if sym in INDEX_FLAG else None) or end
    return [(m0, m1) for m0, m1 in months(START, min(stop, end)) if m0 < stop]


def _per_day(offs) -> int:
    return len(offs) * 2


# ---------------------------------------------------------------- the plan
def plan(data_dir, uni: list, cal: Calendar, man: Manifest, end: dt.date | None = None) -> list:
    """Jobs [(sym, offsets, kind, month_start, [missing days])]: indices first, then stocks in universe order, newest
    month first within each."""
    end = end or dt.date.today()
    jobs = []
    for sym, offs in [("NIFTY", INDEX_OFFSETS), ("BANKNIFTY", INDEX_OFFSETS)] + list(uni):
        kind = "index" if sym in INDEX_FLAG else "stock"
        for m0, m1 in reversed(_months_for(data_dir, sym, end)):
            miss = [d for d in cal.days(m0, m1) if not man.done(sym, d)]
            if miss:
                jobs.append((sym, offs, kind, m0, miss))
    return jobs


def overview(data_dir) -> dict:
    """Where the whole download stands, from the manifest only (no calls to Dhan): for the progress panel."""
    r = root(data_dir)
    man = _json(r / "manifest.json", {})
    cal = Calendar(None, r)
    uni = _json(r / "universe.json", {}).get("turnover")
    from .stock_hist import DEFAULT
    stocks = [s for s in DEFAULT] + sorted((s for s in (uni or {}) if s not in DEFAULT), key=lambda s: -(uni or {})[s])
    syms = [("NIFTY", INDEX_OFFSETS), ("BANKNIFTY", INDEX_OFFSETS)] + \
        [(s, STOCK_OFFSETS if i < TIER_WIDE else NARROW_OFFSETS) for i, s in enumerate(stocks)]
    end = dt.date.today()
    tot = complete = partial = days_miss = calls = 0
    first30 = {"total": 0, "complete": 0}
    for sym, offs in syms:
        for m0, m1 in _months_for(data_dir, sym, end):
            tot += 1
            rec = man.get(f"{sym}|{m0:%Y-%m}")
            need = cal.days(m0, m1)
            have = set(rec["d"]) if rec else set()
            empt = {int(k) for k, v in (rec or {}).get("e", {}).items()
                    if (dt.date.fromisoformat(v) - dt.date(m0.year, m0.month, int(k))).days >= EMPTY_FINAL_DAYS}
            miss = [d for d in need if d.day not in have and d.day not in empt]
            in30 = sym in INDEX_FLAG or sym in DEFAULT
            if in30:
                first30["total"] += 1
            if not miss:
                complete += 1
                first30["complete"] += in30
            else:
                partial += bool(have)
                days_miss += len(miss)
                calls += len(miss) * _per_day(offs)
    free = shutil.disk_usage(r).free / 1e9
    left_today = max(0, DAILY_CAP - B.used())
    return {"months_total": tot, "months_complete": complete, "months_partial": partial,
            "months_pending": tot - complete, "days_missing": days_miss, "calls_left": calls,
            "pct": round(complete / tot * 100, 1) if tot else 0.0,
            "first30": first30, "symbols": len(syms), "stocks_known": bool(uni),
            "calls_today": B.used(), "cap": DAILY_CAP, "dhan_limit": B.DAY_LIMIT,
            "eta_days": round(calls / DAILY_CAP, 1), "left_today": left_today,
            "hours_at_full_speed": round(calls / B.RATE / 3600, 1), "free_gb": round(free, 1)}


# ---------------------------------------------------------------- the download
class _NetDown(Exception):
    def __init__(self, msg, net=True):
        super().__init__(msg)
        self.net = net


def _fetch_day(dhan, sym, kind, ids, offs, day: dt.date, should_stop) -> pd.DataFrame | str | None:
    """All strikes of one day: a frame, 'empty' (Dhan has no data that day) or None (stopped). Network trouble raises."""
    from .dhan import DhanError
    nxt = (day + dt.timedelta(days=1)).isoformat()
    order = sorted(offs, key=lambda o: (abs(o), o))          # ATM first: if it's empty, the day is empty
    parts = []
    for i, off in enumerate(order):
        for side in ("CE", "PE"):
            if should_stop():
                return None
            try:
                if kind == "index":
                    arr = dhan.rolling(sym, INDEX_FLAG[sym], off, side, day.isoformat(), nxt)
                else:
                    arr = dhan.rolling(sym, "MONTH", off, side, day.isoformat(), nxt,
                                       security_id=ids[sym]["id"], instrument="OPTSTK")
            except PermissionError:
                raise
            except DhanError as e:                         # network trouble, or Dhan refused this one request
                m = str(e)
                raise _NetDown(m, net=m.startswith("Network error") or "too many retries" in m)
            except Exception as e:
                raise _NetDown(str(e))
            df = _frame(arr, off, side)
            if df is not None:
                parts.append(df)
        if i == 0 and not parts:
            return "empty"
    return pd.concat(parts, ignore_index=True) if parts else "empty"


def download(dhan, data_dir, stocks=None, log=print, should_stop=lambda: False, progress=lambda d, t: None) -> dict:
    """Fetch every missing trading day (whole months, holes, the newest days), within the daily call budget.
    Resumable at any point; safe to stop, close the app, lose the network or sleep the Mac."""
    r = root(data_dir)
    hd5 = r / "history"
    uni = universe(dhan, data_dir, stocks, log)
    ids = stock_ids(Path(data_dir), [s for s, _ in uni]) if uni else {}
    miss_ids = [s for s, _ in uni if s not in ids]
    if miss_ids:
        log(f"Not in Dhan's scrip master (skipped): {', '.join(miss_ids[:10])}{'…' if len(miss_ids) > 10 else ''}")
    uni = [(s, o) for s, o in uni if s in ids]
    cal = Calendar(dhan, r)
    man = Manifest(r)
    log("Checking what is already on disk…")
    jobs = plan(data_dir, uni, cal, man)
    man.save(force=True)
    total = sum(len(j[4]) * _per_day(j[1]) for j in jobs) or 1
    holes = sum(1 for j in jobs if man.rec(j[0], j[3])["d"])
    log(f"{len(jobs)} months need data ({sum(len(j[4]) for j in jobs):,} trading days, {holes} of them partly on disk): "
        f"about {total:,} calls. Dhan allows {B.DAY_LIMIT:,} a day; the download uses up to {DAILY_CAP:,} "
        f"({B.used():,} used today).")
    done, result, fails_in_row = 0, "done", 0
    for sym, offs, kind, m0, days in jobs:
        f = hd5 / sym / f"raw_{m0:%Y-%m}.parquet"
        got = 0
        di = 0
        while di < len(days):
            day = days[di]
            if should_stop():
                result = "stopped"
                break
            if B.used() + _per_day(offs) > DAILY_CAP:
                log(f"Today's Dhan budget is used ({B.used():,} calls). It continues by itself after midnight.")
                result = "budget"
                break
            if shutil.disk_usage(r).free / 1e9 < MIN_FREE_GB:
                log(f"Less than {MIN_FREE_GB:.0f} GB free on the Mac: download paused. Free some space and press Update.")
                result = "disk"
                break
            try:
                res = _fetch_day(dhan, sym, kind, ids, offs, day, should_stop)
            except _NetDown as e:
                n = man.fail(sym, day)
                if not e.net:                              # Dhan answered but refused: try twice more, then next run
                    if n % 3 == 0:
                        log(f"  {sym} {day}: Dhan refused ({str(e)[:100]}); tried again on the next run")
                        di += 1
                    continue
                fails_in_row += 1
                if fails_in_row >= 3:
                    wait = PAUSES[min(len(PAUSES) - 1, fails_in_row - 3)]
                    log(f"Network or Dhan trouble ({str(e)[:80]}): pausing {wait // 60} min, then retrying {sym} {day:%d %b %Y}")
                    for _ in range(wait):
                        if should_stop():
                            break
                        time.sleep(1)
                    continue                               # same day again: nothing is skipped
                continue
            fails_in_row = 0
            if res is None:
                result = "stopped"
                break
            if isinstance(res, str):
                man.add(sym, day, empty=True)
            else:
                _write_day(f, res)
                man.add(sym, day)
                got += 1
            man.save()
            done += _per_day(offs)
            progress(done, total)
            di += 1
        if got:
            log(f"{sym} {m0:%b %Y}: +{got} day(s)")
        if kind == "stock" and f.exists():
            meta = f.parent / "meta.json"
            if not meta.exists():
                meta.write_text(json.dumps({"key": f"DHAN|{ids[sym]['id']}", "step": 1.0, "lot": ids[sym]["lot"], "source": "dhan"}))
        if result != "done":
            break
    man.save(force=True)
    B.flush()
    link_upstox(data_dir, ["NIFTY", "BANKNIFTY"] + [s for s, _ in uni])
    if result == "done":
        log("All months are complete up to yesterday. New days are added by itself every day.")
        progress(total, total)
    return {"months": len(jobs), "stocks": [s for s, _ in uni], "result": result}


def compare(data_dir) -> dict:
    """Current Lab (2 years) vs the 5-year Lab, side by side."""
    from . import factory as F
    a, b = F.load(data_dir), F.load(root(data_dir))

    def brief(st):
        r = st.get("rules", {})
        prom = [v for v in r.values() if v.get("stage") == "promoted"]
        return {"days": st.get("n_days"), "period": st.get("period"), "tried": st.get("tried_total", 0), "promoted": len(prom),
                "strong": sum(v.get("evidence") in ("strong", "moderate") for v in prom),
                "buy_strong": sum(v.get("evidence") in ("strong", "moderate") and v.get("dir") != 0 for v in prom),
                "luck": round(st.get("tried_total", 0) * F.P_LUCK, 1), "plan40": (st.get("plan40") or {}).get("verdict"),
                "updated": st.get("updated")}
    both = []
    ra, rb = a.get("rules", {}), b.get("rules", {})
    for k in set(ra) & set(rb):                          # the same rule judged on 2 years and on 5 years
        if ra[k].get("stage") == "promoted" or rb[k].get("stage") == "promoted":
            both.append({"id": k, "rule": ra[k]["rule"], "two_year": {p: ra[k][p] for p in ("valid", "hold")}, "two_stage": ra[k]["stage"],
                         "five_year": {p: rb[k][p] for p in ("valid", "hold")}, "five_stage": rb[k]["stage"]})
    return {"current": brief(a), "five_year": brief(b), "same_rules": both[:40]}
