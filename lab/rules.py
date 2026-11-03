"""Dated facts (bullets) read from the rules/ folder: events, charges, lot sizes, stock lists.

Each file says what it is for. A fact that changes over time is a list of entries with a "from" date; the entry valid
on a given day applies, so adding a new rate or lot from a date never changes results for earlier days.

The gun never breaks on a bad file: a missing or broken file falls back to the built-in values (the ones the app used
before these files existed) and the problem is reported on the System page.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

DIR = Path(__file__).resolve().parents[1] / "rules"
USER_EVENTS = Path(__file__).resolve().parents[1] / "data" / "lab_events.csv"

_FALLBACK = {
    "costs": {"entries": [{"from": "2000-01-01", "brokerage_per_order": 20.0, "stt_sell": 0.0015, "exchange": 0.0003503,
                           "sebi": 1e-06, "gst": 0.18, "stamp_buy": 3e-05}]},
    "lots": {"lots": {"NIFTY": [{"from": "2000-01-01", "lot": 65}], "BANKNIFTY": [{"from": "2000-01-01", "lot": 30}]}},
    "events": {"events": []},
    "stocks": {"liquid": [], "lab_first": [], "banks": []},
}
_cache: dict = {}
problems: dict = {}


def _load(name: str) -> dict:
    f = DIR / f"{name}.json"
    try:
        mt = f.stat().st_mtime
        hit = _cache.get(name)
        if hit and hit[0] == mt:
            return hit[1]
        data = json.loads(f.read_text())
        _cache[name] = (mt, data)
        problems.pop(name, None)
        return data
    except Exception as e:
        problems[name] = f"rules/{name}.json: {e} (using built-in values)"
        return _FALLBACK[name]


def _pick(entries: list, day) -> dict:
    d = (day or dt.date.today()).isoformat() if not isinstance(day, str) else day
    best = None
    for e in sorted(entries, key=lambda e: e.get("from", "")):
        if e.get("from", "") <= d:
            best = e
    return best or sorted(entries, key=lambda e: e.get("from", ""))[0]


def costs(day=None) -> dict:
    """Charge rates valid on `day` (date or ISO string; today if None)."""
    try:
        return _pick(_load("costs")["entries"], day)
    except Exception:
        return _FALLBACK["costs"]["entries"][0]


def lot(sym: str, day=None) -> int | None:
    try:
        ent = (_load("lots").get("lots") or {}).get(sym)
        return int(_pick(ent, day)["lot"]) if ent else None
    except Exception:
        ent = _FALLBACK["lots"]["lots"].get(sym)
        return int(ent[0]["lot"]) if ent else None


def events_named() -> list:
    """[(date, name)] from rules/events.json plus your own data/lab_events.csv (date,event)."""
    out = []
    try:
        out = [(e["date"], e.get("name") or "event") for e in _load("events").get("events", [])]
    except Exception:
        pass
    try:
        if USER_EVENTS.exists():
            with open(USER_EVENTS, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    d = (row.get("date") or "").strip()
                    if len(d) == 10:
                        out.append((d, (row.get("event") or "your event").strip()))
    except Exception as e:
        problems["user_events"] = f"data/lab_events.csv: {e}"
    return sorted(set(out))


def events() -> set:
    return {d for d, _ in events_named()}


def stock_list(name: str, fallback: list | None = None) -> list:
    try:
        v = _load("stocks").get(name)
        if isinstance(v, list) and v:
            return list(v)
    except Exception:
        pass
    return list(fallback or [])


def check() -> list:
    """For the System page and the tests: every rules file readable and sane."""
    out = []
    for name in ("costs", "lots", "events", "stocks"):
        _load(name)
        out.append({"id": f"rules_{name}", "name": f"rules/{name}.json readable", "ok": name not in problems,
                    "detail": problems.get(name) or "ok"})
    return out
