"""The 5-year Lab (challenger): Dhan's expired-options archive (1-minute, from 2021) for Nifty, Bank Nifty and the
liquid F&O stocks, joined with the Upstox history already on disk, in its own folder (data/lab5y).

It never touches data/history or data/lab: the current Lab and its live rules keep running unchanged. The 5-year
Lab runs the same search and tests there, and the two are shown side by side; switching is Mandeep's call.

Market data only (Dhan Data API): no orders, no account data.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
from pathlib import Path

import pandas as pd

from .data import _frame, hist_dir, months

LAB5Y = "lab5y"
START = dt.date(2021, 1, 1)
SCRIP_MASTER = "https://images.dhan.co/api-data/api-scrip-master.csv"
INDEX_OFFSETS = range(-10, 11)
STOCK_OFFSETS = range(-3, 4)
INDEX_FLAG = {"NIFTY": "WEEK", "BANKNIFTY": "MONTH"}
DAILY_CAP = 90000          # Dhan Data APIs allow 100,000 calls a day (5 a second); 10,000 stay free for live news / movers
# Dhan returns at most ONE day per rolling-option call, so a month of an index is ~22 days × 42 calls.


def root(data_dir) -> Path:
    d = Path(data_dir) / LAB5Y
    (d / "history").mkdir(parents=True, exist_ok=True)
    return d


def stock_ids(cache_dir, syms: list, fetch=True) -> dict:
    """symbol -> {"id": Dhan security id of the stock, "lot": option lot} from Dhan's public scrip master (cached daily)."""
    f = Path(cache_dir) / f"dhan_scrips_{dt.date.today():%Y%m%d}.csv"
    if not f.exists():
        if not fetch:
            return {}
        import requests
        r = requests.get(SCRIP_MASTER, timeout=120)
        r.raise_for_status()
        f.write_bytes(r.content)
        for old in Path(cache_dir).glob("dhan_scrips_*.csv"):
            if old != f:
                old.unlink(missing_ok=True)
    want = set(syms)
    ids, lots = {}, {}
    with open(f, newline="", encoding="utf-8", errors="ignore") as fh:
        for row in csv.DictReader(fh):
            ex = row.get("SEM_EXM_EXCH_ID")
            inst = row.get("SEM_INSTRUMENT_NAME")
            sym = row.get("SEM_TRADING_SYMBOL", "")
            if ex == "NSE" and inst == "EQUITY" and sym in want and row.get("SEM_SERIES", "EQ") in ("EQ", ""):
                ids[sym] = int(float(row["SEM_SMST_SECURITY_ID"]))
            elif ex == "NSE" and inst == "OPTSTK":
                und = sym.split("-")[0]
                if und in want and und not in lots:
                    try:
                        lots[und] = int(float(row.get("SEM_LOT_UNITS") or 0))
                    except ValueError:
                        pass
    return {s: {"id": ids[s], "lot": lots.get(s) or 1} for s in syms if s in ids}


def _first_upstox(hd: Path) -> dt.date | None:
    """First day covered by the Upstox per-expiry files (raw_YYYY-MM-DD) already downloaded."""
    fs = sorted(p.stem[4:] for p in hd.glob("raw_*.parquet") if len(p.stem) > 11)
    return dt.date.fromisoformat(fs[0]) - dt.timedelta(days=35) if fs else None


def link_upstox(data_dir, syms):
    """The 5-year folder sees the Upstox files through links (no copies, nothing changed in data/history)."""
    src, dst = hist_dir(data_dir), root(data_dir) / "history"
    for sym in list(syms) + ["INDIAVIX"]:
        (dst / sym).mkdir(parents=True, exist_ok=True)
        for p in (src / sym).glob("*.parquet") if (src / sym).exists() else []:
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


def plan(data_dir, stocks: list, end: dt.date | None = None) -> list:
    """Jobs [(sym, month_start, month_end, kind)] not on disk yet: months before the Upstox history begins."""
    end = end or dt.date.today()
    jobs = []
    hd5 = root(data_dir) / "history"
    for sym in ["NIFTY", "BANKNIFTY"] + list(stocks):
        stop = _first_upstox(hist_dir(data_dir) / sym) or end
        for m0, m1 in months(START, min(stop, end)):
            f = hd5 / sym / f"raw_{m0:%Y-%m}.parquet"
            if not f.exists() and m0 < stop:
                jobs.append((sym, m0, m1, "index" if sym in INDEX_FLAG else "stock"))
    jobs.sort(key=lambda j: (-j[1].toordinal(), j[0] not in INDEX_FLAG, j[0]))   # newest first, indices first
    return jobs


def _budget(data_dir) -> dict:
    f = root(data_dir) / "dhan_calls.json"
    try:
        b = json.loads(f.read_text()) if f.exists() else {}
    except Exception:
        b = {}
    today = dt.date.today().isoformat()
    return {"f": f, "day": today, "used": int(b.get(today, 0))}


def _spend(bud: dict, n: int):
    bud["used"] += n
    bud["f"].write_text(json.dumps({bud["day"]: bud["used"]}))


def _days(m0: dt.date, m1: dt.date):
    d = m0
    while d < m1:
        if d.weekday() < 5:
            yield d
        d += dt.timedelta(days=1)


def download(dhan, data_dir, stocks: list, log=print, should_stop=lambda: False, progress=lambda d, t: None) -> dict:
    """Fetch every missing month (resumable), one trading day per call (Dhan's limit), within the daily call budget:
    ~22 × 42 calls per index month, ~22 × 14 per stock month. When today's budget is used, it stops and continues
    by itself the next evening."""
    ids = stock_ids(Path(data_dir), stocks) if stocks else {}
    miss = [s for s in stocks if s not in ids]
    if miss:
        log(f"Not in Dhan's scrip master (skipped): {', '.join(miss)}")
    jobs = plan(data_dir, list(ids))
    hd5 = root(data_dir) / "history"
    per = lambda kind: (len(INDEX_OFFSETS) if kind == "index" else len(STOCK_OFFSETS)) * 2
    month_calls = lambda j: per(j[3]) * sum(1 for _ in _days(j[1], min(j[2], dt.date.today())))
    total = sum(month_calls(j) for j in jobs) or 1
    bud = _budget(data_dir)
    done = 0
    log(f"5-year Lab: {len(jobs)} months to fetch from Dhan (about {total:,} calls; up to {DAILY_CAP:,} a day, "
        f"{bud['used']:,} used today, so about {max(1, -(-total // DAILY_CAP))} day(s))")
    for sym, m0, m1, kind in jobs:
        if should_stop():
            log("Stopped. Finished months are kept; run again to continue.")
            break
        need = month_calls((sym, m0, m1, kind))
        if bud["used"] + need > DAILY_CAP:
            log(f"Today's Dhan call budget is used ({bud['used']:,}). It continues by itself tomorrow evening.")
            break
        parts = []
        offs = INDEX_OFFSETS if kind == "index" else STOCK_OFFSETS
        for day in _days(m0, min(m1, dt.date.today())):
            nxt = (day + dt.timedelta(days=1)).isoformat()
            empty_day = True
            for off in offs:
                for side in ("CE", "PE"):
                    if should_stop():
                        break
                    try:
                        if kind == "index":
                            arr = dhan.rolling(sym, INDEX_FLAG[sym], off, side, day.isoformat(), nxt)
                        else:
                            arr = dhan.rolling(sym, "MONTH", off, side, day.isoformat(), nxt,
                                               security_id=ids[sym]["id"], instrument="OPTSTK")
                    except PermissionError:
                        raise
                    except Exception as e:
                        log(f"  {sym} {day} ATM{off:+d} {side}: {e}")
                        arr = {}
                    _spend(bud, 1)
                    df = _frame(arr, off, side)
                    if df is not None:
                        parts.append(df)
                        empty_day = False
                    done += 1
                    progress(done, total)
                    if empty_day and off == offs[0] and side == "PE":
                        break                          # holiday: the first strike came back empty both sides
                if empty_day and off == offs[0]:
                    break
        if should_stop():
            break
        f = hd5 / sym / f"raw_{m0:%Y-%m}.parquet"
        f.parent.mkdir(parents=True, exist_ok=True)
        if parts:
            pd.concat(parts, ignore_index=True).to_parquet(f, compression="zstd", index=False)
            log(f"{sym} {m0:%b %Y}: {sum(len(p) for p in parts):,} rows")
        else:
            pd.DataFrame(columns=["ts"]).to_parquet(f, index=False)      # an empty month is remembered, not refetched
            log(f"{sym} {m0:%b %Y}: no data")
        if kind == "stock":
            meta = f.parent / "meta.json"
            if not meta.exists():
                meta.write_text(json.dumps({"key": f"DHAN|{ids[sym]['id']}", "step": 1.0, "lot": ids[sym]["lot"], "source": "dhan"}))
    link_upstox(data_dir, ["NIFTY", "BANKNIFTY"] + list(ids))
    progress(total, total)
    return {"months": len(jobs), "stocks": list(ids)}


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
