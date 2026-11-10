"""NSE F&O trading calendar (gun, 2.48): is there a session today, is it open now, when does the next one start?

Mandeep, 10 Nov 2026: "today is holiday still system took trades made loss" and "the system should auto start live
market ... sometimes market is open for few hours only like on Diwali but system did not start live".

Bullets (dated data): rules/market_calendar.json (regular hours, special sessions such as the Diwali Muhurat with the
exchange circular, extra holidays) and the NSE holiday list in rules/health.json. The live scanner starts and stops by
this calendar, and nothing (alert, paper trade, test-bench fill) is made outside an open session.

A quote is also checked by its own exchange time: a price whose last trade happened before today's session opened
(the snapshot Upstox sends on a reconnect, a holiday's old quote) is never used to open a trade.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .clock import IST

FILE = Path(__file__).resolve().parents[1] / "rules" / "market_calendar.json"
_C: dict = {}
DEFAULT = {"regular": {"start_scanner": "09:00", "open": "09:15", "close": "15:30", "weekdays": [0, 1, 2, 3, 4]},
           "extra_holidays": {}, "special_sessions": [], "paper_in_special": False}


def cfg() -> dict:
    try:
        mt = FILE.stat().st_mtime
        if _C.get("mt") != mt:
            _C.update(mt=mt, cfg={**DEFAULT, **json.loads(FILE.read_text())}, error=None)
    except Exception as e:
        _C.update(error=str(e), cfg=_C.get("cfg") or DEFAULT)
    return _C["cfg"]


def _t(hhmm: str) -> dt.time:
    h, m = str(hhmm).split(":")
    return dt.time(int(h), int(m))


def holidays(year: int) -> set:
    out = set()
    try:
        from .health import cfg as hcfg
        out |= set((hcfg().get("holidays") or {}).get(str(year)) or [])
    except Exception:
        pass
    out |= set((cfg().get("extra_holidays") or {}).get(str(year)) or [])
    return out


def is_holiday(day: dt.date) -> bool:
    return day.isoformat() in holidays(day.year)


def session(day: dt.date) -> dict | None:
    """Today's session {"kind": "regular" | "special", "name", "start_scanner", "open", "close"} (datetimes, IST) or
    None when NSE F&O does not trade that day. A special session wins over a holiday / weekend."""
    c = cfg()
    for s in c.get("special_sessions") or []:
        if s.get("date") == day.isoformat():
            return _mk(day, "special", s.get("name") or "Special session", s)
    r = c.get("regular") or DEFAULT["regular"]
    if day.weekday() not in (r.get("weekdays") or [0, 1, 2, 3, 4]) or is_holiday(day):
        return None
    return _mk(day, "regular", "Regular session", r)


def _mk(day, kind, name, s) -> dict:
    o = dt.datetime.combine(day, _t(s.get("open") or "09:15"), IST)
    return {"kind": kind, "name": name, "date": day.isoformat(),
            "start_scanner": dt.datetime.combine(day, _t(s.get("start_scanner") or s.get("open") or "09:00"), IST),
            "open": o, "close": dt.datetime.combine(day, _t(s.get("close") or "15:30"), IST),
            "source": s.get("source")}


def _ist(now: dt.datetime) -> dt.datetime:
    return now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)


def is_open(now: dt.datetime) -> bool:
    """True while today's session is between its open and close."""
    now = _ist(now)
    s = session(now.date())
    return bool(s and s["open"] <= now <= s["close"])


def scanner_window(now: dt.datetime) -> bool:
    """True from the scanner start (a little before the open) until the close: when the live scanner should run."""
    now = _ist(now)
    s = session(now.date())
    return bool(s and s["start_scanner"] <= now < s["close"])


def paper_ok(now: dt.datetime) -> bool:
    """Paper / bench trades: an open session, and for special sessions only if the calendar allows it."""
    now = _ist(now)
    s = session(now.date())
    if not s or not (s["open"] <= now <= s["close"]):
        return False
    return s["kind"] == "regular" or bool(cfg().get("paper_in_special"))


def next_session(now: dt.datetime, days: int = 15) -> dict | None:
    now = _ist(now)
    for k in range(days):
        s = session(now.date() + dt.timedelta(days=k))
        if s and s["close"] > now:
            return s
    return None


def quote_fresh(ltt_ms, now: dt.datetime, max_age_s: float | None = None) -> bool:
    """Was this quote's last trade in today's open session (and, with max_age_s, recent enough)? ltt_ms: the
    exchange's last-trade time in milliseconds (0 / None = unknown: then only the session check applies)."""
    now = _ist(now)
    s = session(now.date())
    if not s:
        return False
    if not ltt_ms:
        return True
    t = dt.datetime.fromtimestamp(float(ltt_ms) / 1000, IST)
    if t < s["open"] - dt.timedelta(minutes=1):
        return False
    return max_age_s is None or (now - t).total_seconds() <= max_age_s


def view(now: dt.datetime | None = None) -> dict:
    now = _ist(now or dt.datetime.now(IST))
    s = session(now.date())
    nxt = next_session(now)
    f = lambda d: d.strftime("%a %d %b %H:%M") if d else None
    return {"today": None if not s else {"kind": s["kind"], "name": s["name"], "open": f(s["open"]), "close": f(s["close"])},
            "open_now": is_open(now), "holiday": is_holiday(now.date()),
            "next": None if not nxt else {"name": nxt["name"], "kind": nxt["kind"], "start_scanner": f(nxt["start_scanner"]),
                                          "open": f(nxt["open"]), "close": f(nxt["close"])},
            "error": _C.get("error")}
