"""1-minute option history for F&O *stocks* (Upstox Plus expired-instrument API), so the Strategy Lab and the
conviction model can learn stock options, not only Nifty and Bank Nifty.

Market data only: this reads past prices. It never places, modifies or cancels an order.

Per stock and monthly expiry it saves history/<SYM>/raw_<expiry>.parquet (strikes near the money, both sides),
history/<SYM>/daily.parquet and history/<SYM>/meta.json (instrument key, strike gap, lot). Newest months are
fetched first, so a partial download is already useful; finished expiries are kept, so it resumes.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from . import upstox_hist as U
from .data import UNDERLYINGS, hist_dir

# Liquid F&O stocks with active option markets (filtered by what today's instrument list actually has).
DEFAULT = ["RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "INFY", "TCS", "AXISBANK", "KOTAKBANK", "BHARTIARTL", "ITC",
           "LT", "BAJFINANCE", "M&M", "MARUTI", "SUNPHARMA", "TATASTEEL", "ADANIENT", "HCLTECH", "HINDALCO", "JSWSTEEL",
           "BEL", "HAL", "TRENT", "DLF", "INDUSINDBK", "ONGC", "NTPC", "COALINDIA", "VEDL", "BAJAJ-AUTO"]
BAND = 3                     # strikes beyond the day's high/low (stock buyers trade ATM ± 2)
EST_PER_EXPIRY = 70          # requests per stock expiry, for the progress bar


def strike_gap(strikes, spot) -> float:
    near = sorted(sorted(set(strikes), key=lambda k: abs(k - spot))[:8])
    gaps = [b - a for a, b in zip(near, near[1:]) if b - a > 1e-9]
    return min(gaps) if gaps else 1.0


def stock_keys(data_dir, syms=None) -> dict:
    """symbol -> NSE_EQ|ISIN from today's instrument list (live/universe), cached on disk by the live app."""
    from live import universe
    uni = universe.load(str(data_dir), fetch=True, log=lambda m: None)
    want = syms or DEFAULT
    return {s: uni.underlyings[s].key for s in want if s in uni.underlyings and not uni.underlyings[s].is_index}


def plan(data_dir, keys: dict, months=12, end: dt.date | None = None) -> dict:
    """How much is missing: expiries per stock not on disk yet (no network)."""
    end = end or dt.date.today() - dt.timedelta(days=1)
    hd = hist_dir(data_dir)
    have = {s: len(list((hd / s).glob("raw_*.parquet"))) for s in keys}
    missing = sum(max(0, months - h) for h in have.values())
    return {"stocks": len(keys), "have": have, "missing_expiries": missing, "requests": missing * EST_PER_EXPIRY,
            "hours": round(missing * EST_PER_EXPIRY / 3600, 1)}


def download(token: str, data_dir, keys: dict, months=12, end: dt.date | None = None, log=print,
             should_stop=lambda: False, progress=lambda done, total: None):
    up = U.UpstoxHistory(token)
    end = end or dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=int(months * 30.5) + 5)
    hd = hist_dir(data_dir)
    U.STOCK_KEYS.update(keys)
    jobs = []
    for sym, key in keys.items():
        if should_stop():
            return
        try:
            ex = up.expiries(sym)
        except PermissionError:
            raise
        except Exception as e:
            log(f"{sym}: {e}")
            continue
        if not ex:
            log(f"{sym}: no expired contracts on Upstox")
            continue
        for e, a, b in U._windows(ex, start, end, monthly=True):
            f = hd / sym / f"raw_{e.isoformat()}.parquet"
            if not f.exists():
                jobs.append((e, sym, a, b, f))
    if not jobs:
        log("Stock option history is up to date.")
        progress(1, 1)
        return
    jobs.sort(key=lambda j: (-j[0].toordinal(), j[1]))      # newest month first, across all stocks
    total = len(jobs) * EST_PER_EXPIRY
    done = [0]

    def tick():
        done[0] += 1
        progress(min(done[0], total - 1), total)
    log(f"{len(jobs)} stock expiries to fetch across {len({j[1] for j in jobs})} stocks "
        f"(about {total:,} requests, ~{max(1, total // 3600)} h at Upstox's limit; resumable)")
    daily_done = set()
    for e, sym, a, b, f in jobs:
        if should_stop():
            log("Stopped. Finished expiries are kept; run again to continue.")
            return
        try:
            if sym not in daily_done:
                UNDERLYINGS.setdefault(sym, {"flag": "MONTH", "step": 1.0, "lot": 1, "stock": True, "key": keys[sym]})
                U._save_daily(up, hd, sym, start, end, log)
                daily_done.add(sym)
            sp = U._frame(up.index_minutes(sym, a, b), ("ts", "open", "high", "low", "close", "volume"))
            if sp.empty:
                log(f"{sym} {e}: no stock minutes")
                continue
            cons = up.contracts(sym, e.isoformat())
            opts = [c for c in cons if c.get("instrument_type") in ("CE", "PE")]
            if not opts:
                continue
            step = strike_gap([float(c["strike_price"]) for c in opts], float(sp["close"].median()))
            lot = int(opts[0].get("lot_size") or 1)
            UNDERLYINGS[sym] = {"flag": "MONTH", "step": step, "lot": lot, "stock": True, "key": keys[sym]}
            df = U.build_expiry(up, sym, e, a, b, sp, should_stop, tick, band=BAND)
        except PermissionError:
            raise
        except Exception as ex:
            log(f"  {sym} expiry {e}: {ex}")
            continue
        if df is None or df.empty:
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(f, compression="zstd", index=False)
        meta = f.parent / "meta.json"
        old = json.loads(meta.read_text()) if meta.exists() else {}
        if e.isoformat() >= old.get("expiry", ""):          # keep the newest strike gap and lot
            meta.write_text(json.dumps({"key": keys[sym], "step": step, "lot": lot, "expiry": e.isoformat()}))
        log(f"{sym} {e:%b %Y}: {df['strike'].nunique()} strikes, {len(df):,} rows")
    progress(total, total)
    log("Stock option history download complete.")
