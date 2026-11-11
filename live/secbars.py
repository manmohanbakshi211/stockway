"""Seconds candles for the main chart (gun, 2.69).

Mandeep, 12 Nov 2026: "humare main chart par seconds ki timeline bhi option mein do".

The app keeps one-minute bars; seconds candles need the prices in between. This keeps today's prices of the indices
(and of any symbol an open chart streams) in memory: the engine samples them every second, an open chart's stream adds
every price it sees (~0.1 s). Candles of 5, 15 or 30 seconds are built from those prices, aligned to 09:15:00.
History starts when the app started that day (nothing older is stored). Reads the app's own prices only.
Settings: rules/chart_stream.json ("sec_syms", "sec_tfs", "sec_keep").
"""
from __future__ import annotations

import datetime as dt
from collections import deque

from .clock import IST

IST_SHIFT = 19800
SEC_TFS = {"5s": 5, "15s": 15, "30s": 30}
ALWAYS = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX")
KEEP = 30000                     # prices per symbol (a full session at ~1 a second, more for a streamed chart)


def _cfg() -> dict:
    try:
        from .stream import cfg
        return cfg()
    except Exception:
        return {}


def _t0(ts: float) -> float:
    d = dt.datetime.fromtimestamp(ts, IST).date()
    return dt.datetime.combine(d, dt.time(9, 15), IST).timestamp()


class SecBars:
    def __init__(self):
        self.buf: dict[str, deque] = {}
        self.day: dict[str, str] = {}
        self.watch: dict[str, float] = {}           # symbols an open chart streams (sym -> last seen)

    def add(self, sym: str, ts: float, p: float):
        if not sym or not p or not ts:
            return
        d = dt.datetime.fromtimestamp(ts, IST).date().isoformat()
        q = self.buf.get(sym)
        if q is None or self.day.get(sym) != d:
            q = self.buf[sym] = deque(maxlen=int(_cfg().get("sec_keep") or KEEP))
            self.day[sym] = d
        if q and (ts < q[-1][0] or (ts == q[-1][0] and p == q[-1][1])):
            return                                   # out of order or the same price again
        q.append((float(ts), float(p)))

    def sample(self, e, now: dt.datetime):
        """Every engine second: the latest price of the indices and of the symbols open charts stream."""
        uni = getattr(getattr(e, "uni", None), "underlyings", None) or {}
        cut = now.timestamp() - 120
        syms = set(_cfg().get("sec_syms") or ALWAYS) | {s for s, t in self.watch.items() if t >= cut}
        for s in syms:
            u = uni.get(s)
            i = e.state.get(u.key) if u is not None and getattr(u, "key", None) else None
            if i is None or not getattr(i, "ltp", None):
                continue
            ts = (i.ltt / 1000) if getattr(i, "ltt", None) else now.timestamp()
            self.add(s, ts, i.ltp)

    def rows(self, sym: str, sec: int) -> list:
        """[[start (epoch), open, high, low, close]] of today's candles of `sec` seconds."""
        q = list(self.buf.get(sym) or ())
        if not q:
            return []
        t0 = _t0(q[-1][0])
        out = []
        for ts, p in q:
            if ts < t0:
                continue
            k = int((ts - t0) // sec)
            start = t0 + k * sec
            if out and out[-1][0] == start:
                r = out[-1]
                r[2], r[3], r[4] = max(r[2], p), min(r[3], p), p
            else:
                out.append([start, p, p, p, p])
        return out

    def candles(self, sym: str, tf: str) -> dict:
        sec = SEC_TFS[tf]
        rs = self.rows(sym, sec)
        T = [int(r[0] + IST_SHIFT) for r in rs]
        return {"sym": sym, "tf": tf, "source": "the app's own live prices (seconds, today only)", "more": False,
                "intraday": True, "candles": [{"time": t, "open": r[1], "high": r[2], "low": r[3], "close": r[4]} for t, r in zip(T, rs)],
                "volume": [], "has_volume": False}

    def forming(self, sym: str, tf: str) -> dict | None:
        """The candle forming now (only the last prices are read: called ~10 times a second by an open chart)."""
        q = self.buf.get(sym)
        if not q:
            return None
        sec = SEC_TFS[tf]
        last = q[-1][0]
        t0 = _t0(last)
        if last < t0:
            return None
        start = t0 + int((last - t0) // sec) * sec
        ps = []
        for ts, p in reversed(q):
            if ts < start:
                break
            ps.append(p)
        ps.reverse()
        return {"time": int(start + IST_SHIFT), "open": ps[0], "high": max(ps), "low": min(ps), "close": ps[-1], "volume": 0}


SB = SecBars()
