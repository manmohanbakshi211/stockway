"""End-of-day gap check and fill. Every instrument should have one bar per session minute; minutes missing from the tape
(feed down, app closed) are filled from the broker's own 1-minute candles and marked src="broker", so nothing is
silently invented and the source of every bar is known."""
from __future__ import annotations

import datetime as dt

import pandas as pd

from ..core import log
from .bars import COLS
from .facts import Facts

_log = log.get("gaps")


def session_minutes(day: str, facts: Facts | None = None) -> list[int]:
    s = (facts or Facts()).session(day)
    if not s:
        return []
    a, b = int(s[0].timestamp()), int(s[1].timestamp())
    return list(range(a, b, 60))


def missing(bars: pd.DataFrame, keys, day: str, facts: Facts | None = None) -> dict[str, list[int]]:
    want = session_minutes(day, facts)
    have = bars.groupby("key")["t"].apply(set).to_dict() if len(bars) else {}
    out = {}
    for k in keys:
        h = have.get(k, set())
        miss = [m for m in want if m not in h]
        if miss:
            out[k] = miss
    return out


def fill(bars: pd.DataFrame, gaps: dict[str, list[int]], fetch) -> tuple[pd.DataFrame, dict]:
    """fetch(key) -> broker 1-min candles [[iso_time, o, h, l, c, volume, oi], ...]. Returns (bars, report)."""
    add, report = [], {"filled": 0, "still_missing": 0, "failed": []}
    for k, mins in gaps.items():
        try:
            candles = fetch(k)
        except Exception as e:
            report["failed"].append({"key": k, "error": str(e)[:200]})
            report["still_missing"] += len(mins)
            continue
        by_t = {int(dt.datetime.fromisoformat(c[0]).timestamp()): c for c in candles}
        for m in mins:
            c = by_t.get(m)
            if c is None:
                report["still_missing"] += 1
                continue
            add.append({"key": k, "t": m, "o": c[1], "h": c[2], "l": c[3], "c": c[4], "vol": c[5],
                        "cum_vol": None, "oi": c[6] if len(c) > 6 else None, "bid": None, "ask": None, "ticks": 0,
                        "src": "broker"})
            report["filled"] += 1
    if add:
        bars = pd.concat([bars, pd.DataFrame(add, columns=COLS)], ignore_index=True)
        bars = bars.sort_values(["key", "t"], kind="stable").reset_index(drop=True)
    _log.info("gaps_filled", **{k: v for k, v in report.items() if k != "failed"}, failed=len(report["failed"]))
    return bars, report
