"""Is that market open right now? (gun, 2.38)

Sessions and the ticker -> session map are bullets in rules/market_hours.json (each market in its own time zone, with
lunch breaks, overnight sessions, Sunday-to-Friday futures and currencies, 24/7 crypto, and NSE holidays from
rules/health.json). state() answers Open / Closed / Break / Holiday, and 'No update' when a market should be trading
but its price has not moved for a while (a holiday the file does not know, or a stuck feed). Information only.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

FILE = Path(__file__).resolve().parents[1] / "rules" / "market_hours.json"
_C: dict = {}


def cfg() -> dict:
    try:
        mt = FILE.stat().st_mtime
        if _C.get("mt") != mt:
            _C.update(mt=mt, cfg=json.loads(FILE.read_text()), error=None)
    except Exception as e:
        _C.update(error=str(e), cfg=_C.get("cfg") or {"sessions": {}, "tickers": {}})
    return _C["cfg"]


def _mins(hhmm: str) -> int:
    h, m = str(hhmm).split(":")
    return int(h) * 60 + int(m)


def _nse_holiday(day: dt.date) -> bool:
    try:
        from .health import cfg as hcfg
        return day.isoformat() in set((hcfg().get("holidays") or {}).get(str(day.year)) or [])
    except Exception:
        return False


def session_for(key: str | None) -> str | None:
    if not key:
        return None
    c = cfg()
    if key in c.get("sessions", {}):
        return key
    return (c.get("tickers") or {}).get(key)


def state(key: str | None, now: dt.datetime | None = None, last_ts: float | None = None) -> dict | None:
    """{"session", "open", "label"} for a ticker (or a session name); None when the market is unknown."""
    name = session_for(key)
    s = (cfg().get("sessions") or {}).get(name) if name else None
    if not s:
        return None
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    if s.get("always"):
        out = {"session": name, "open": True, "label": "Open"}
    else:
        lt = now.astimezone(ZoneInfo(s["tz"]))
        tm = lt.hour * 60 + lt.minute
        label = None
        if s.get("weekly"):
            d0, t0, d1, t1 = s["weekly"]
            wk = lt.weekday() * 1440 + tm
            a, b = int(d0) * 1440 + _mins(t0), int(d1) * 1440 + _mins(t1)
            is_open = (a <= wk < b) if a < b else (wk >= a or wk < b)
            br = s.get("daily_break")
            if is_open and br and _mins(br[0]) <= tm < _mins(br[1]):
                is_open, label = False, "Break"
        else:
            o, c = _mins(s["open"]), _mins(s["close"])
            days = set(s.get("days") or range(5))
            if c > o:
                start_day = lt.date()
                is_open = lt.weekday() in days and o <= tm < c
            else:                                   # runs past midnight: the session belongs to the day it started
                if tm >= o:
                    start_day, is_open = lt.date(), lt.weekday() in days
                elif tm < c:
                    start_day = lt.date() - dt.timedelta(days=1)
                    is_open = start_day.weekday() in days
                else:
                    start_day, is_open = lt.date(), False
            if is_open and s.get("holidays") == "health" and _nse_holiday(start_day):
                is_open, label = False, "Holiday"
            if is_open and any(_mins(f) <= tm < _mins(t) for f, t in (s.get("breaks") or [])):
                is_open, label = False, "Break"
        out = {"session": name, "open": bool(is_open), "label": label or ("Open" if is_open else "Closed")}
    if out["open"] and last_ts and now.timestamp() - float(last_ts) > float(cfg().get("stale_min") or 25) * 60:
        out.update(label="No update", stale=True)
    return out
