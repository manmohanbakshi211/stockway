"""The chart's live stream (gun, 2.51).

Mandeep, 10 Nov 2026: "what if we connect the websocket for charting because we need TBT and price at the same time".

The app already gets every price change Upstox sends on its single live feed (Upstox Market Data Feed V3, websocket)
into MarketState. Before 2.51 the chart asked the app for its last bars every 2 seconds; now an open chart keeps a
websocket to the app (/ws/ticks) and gets each new price within ~0.1 s, with the forming candle of its timeframe.
Nothing new connects to a broker: this only reads the app's own prices. Settings: rules/chart_stream.json.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

from .clock import IST

FILE = Path(__file__).resolve().parents[1] / "rules" / "chart_stream.json"
DEFAULTS = {"every_ms": 100, "max_streams": 8, "heartbeat_s": 5, "reconcile_s": 10, "prices_ms": 200, "max_price_streams": 6,
            "quotes_ms": 200, "max_quote_streams": 6, "max_quote_keys": 300, "live_chain_age_s": 120,
            "events_ms": 200, "max_event_streams": 8}
IST_SHIFT = 19800
INTRADAY_MIN = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1H": 60}
OPEN = {"n": 0, "served": 0, "since": time.time()}
_C: dict = {}


def cfg() -> dict:
    try:
        mt = FILE.stat().st_mtime
        if _C.get("mt") != mt:
            _C.update(mt=mt, cfg={**DEFAULTS, **json.loads(FILE.read_text())})
    except Exception:
        _C.setdefault("cfg", dict(DEFAULTS))
    return _C["cfg"]


def _key(e, sym: str):
    u = (getattr(getattr(e, "uni", None), "underlyings", None) or {}).get(sym)
    return getattr(u, "key", None)


def forming(rows: list, tf: str) -> dict | None:
    """Today's 1-minute bars [t, o, h, l, c, v] -> the candle of `tf` that is forming now, in the chart's time (IST shown
    as UTC, like /api/live/candles). Aligned to 09:15 like the chart's own bars. None for daily and longer charts."""
    m = INTRADAY_MIN.get(tf)
    if not m or not rows:
        return None
    last = rows[-1]
    day = dt.datetime.fromtimestamp(last[0], IST).date()
    t0 = dt.datetime.combine(day, dt.time(9, 15), IST).timestamp()
    k = int((last[0] - t0) // (60 * m))
    rs = [r for r in rows if r[0] >= t0 and int((r[0] - t0) // (60 * m)) == k]
    if not rs:
        return None
    return {"time": int(t0 + k * 60 * m + IST_SHIFT), "open": rs[0][1], "high": max(r[2] for r in rs),
            "low": min(r[3] for r in rs), "close": rs[-1][4], "volume": sum(r[5] or 0 for r in rs)}


def frame(e, sym: str, tf: str) -> dict | None:
    """What an open chart needs now: the latest price, its exchange time, when the app got it, and the forming candle."""
    if not sym or not getattr(e, "running", False) or getattr(e, "state", None) is None:
        return None
    k = _key(e, sym)
    i = e.state.get(k) if k else None
    if i is None or not getattr(i, "ltp", None):
        return None
    out = {"sym": sym, "tf": tf, "p": i.ltp, "tt": (i.ltt / 1000) if getattr(i, "ltt", None) else None,
           "rx": getattr(i, "t", None), "chg_pct": round((i.ltp / i.cp - 1) * 100, 2) if getattr(i, "cp", None) else None}
    from . import secbars
    if tf in secbars.SEC_TFS:                    # 2.69: seconds candles from the app's own prices
        try:
            secbars.SB.watch[sym] = time.time()
            if out["tt"]:
                secbars.SB.add(sym, out["tt"], i.ltp)
            c = secbars.SB.forming(sym, tf)
        except Exception:
            c = None
        if c:
            out["candle"] = c
        return out
    try:
        rows = e.charts.today_bars(sym) if getattr(e, "charts", None) is not None else []
    except RuntimeError:                         # the engine added a bar while we read: the next frame has it
        rows = []
    c = forming(rows, tf)
    if c:
        out["candle"] = c
    return out


def prices(e, syms: list) -> dict:
    """2.69 (Mandeep: "live feed bhi chart jitna fast"): the latest price, day change and exchange time of each symbol."""
    out = {}
    if not getattr(e, "running", False) or getattr(e, "state", None) is None:
        return out
    for s in syms[:60]:
        k = _key(e, s)
        i = e.state.get(k) if k else None
        if i is None or not getattr(i, "ltp", None):
            continue
        out[s] = [i.ltp, round((i.ltp / i.cp - 1) * 100, 2) if getattr(i, "cp", None) else None,
                  (i.ltt / 1000) if getattr(i, "ltt", None) else None]
    return out


def quotes(e, keys: list) -> dict:
    """2.71 (Mandeep: paper trades and every price as fast as the market): the latest streamed quote of each instrument
    key (an option, a stock, an index): [ltp, bid, ask, oi, exchange time (s)]. Only what the live feed streams."""
    out = {}
    st = getattr(e, "state", None)
    if not getattr(e, "running", False) or st is None:
        return out
    for k in keys[: int(cfg().get("max_quote_keys") or 300)]:
        i = st.get(k)
        if i is None or not getattr(i, "ltp", None):
            continue
        out[k] = [i.ltp, getattr(i, "bid", None), getattr(i, "ask", None), getattr(i, "oi", None),
                  (i.ltt / 1000) if getattr(i, "ltt", None) else getattr(i, "t", None)]
    return out


def live_chain(chain: dict | None, state) -> dict | None:
    """2.71: the option chain snapshot (REST, refreshed every 20 s) with the live-streamed strikes' price, bid / ask, OI
    and volume on top, marked live. Strikes the feed does not stream keep the snapshot. The cached chain is not changed."""
    if not chain or state is None:
        return chain
    ref = getattr(state, "last_tick", None) or time.time()
    age = float(cfg().get("live_chain_age_s") or 120)
    rows, n = [], 0
    for r in chain.get("rows") or []:
        r2 = dict(r)
        for side in ("CE", "PE"):
            o = r.get(side)
            if not o or not o.get("key"):
                continue
            i = state.get(o["key"])
            if i is None or not getattr(i, "ltp", None) or not getattr(i, "t", None) or ref - i.t > age:
                continue
            o2 = dict(o)
            o2.update(ltp=i.ltp, live=True)
            for k in ("bid", "ask", "oi", "vol"):
                v = getattr(i, k, None)
                if v:
                    o2[k] = v
            if o2.get("cp"):
                o2["chg_pct"] = round((i.ltp / o2["cp"] - 1) * 100, 2)
            r2[side] = o2
            n += 1
        rows.append(r2)
    return {**chain, "rows": rows, "live_n": n}


def view() -> dict:
    return {"open": OPEN["n"], "served": OPEN["served"], "every_ms": cfg()["every_ms"], "max": cfg()["max_streams"]}
