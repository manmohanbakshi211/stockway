"""3.05: which alerts are repeats on the Alerts page (Mandeep, 24 Nov 2026: fewer alerts shown, all still recorded).

A repeat: the same symbol already had an alert shown within window_min (directional) / neutral_window_min (no
direction, e.g. IV spikes) and this one is not stronger (severity not higher) and not a Focus alert. Display only:
nothing here changes what is saved, graded, paper traded or sent to Telegram. The numbers live in
rules/alert_repeats.json (bullet); this file is the gun.
"""
from __future__ import annotations

import json
from pathlib import Path

RULES = Path(__file__).resolve().parents[1] / "rules" / "alert_repeats.json"
DEFAULTS = {"on": True, "window_min": 30, "neutral_window_min": 60, "keep_focus": True}
_C: dict = {}


def cfg() -> dict:
    try:
        mt = RULES.stat().st_mtime
        if _C.get("mt") != mt:
            _C.update(mt=mt, v={**DEFAULTS, **json.loads(RULES.read_text())}, error=None)
    except Exception as e:                       # a bad file never hides alerts: the defaults apply
        _C.update(mt=None, v=dict(DEFAULTS), error=str(e))
    return _C["v"]


def mark(alerts: list, c: dict | None = None) -> set:
    """Ids of the repeats among one day's alerts (any order)."""
    c = c or cfg()
    if not c.get("on", True):
        return set()
    win = float(c.get("window_min") or 0) * 60
    nwin = float(c.get("neutral_window_min") or 0) * 60
    last: dict = {}
    out = set()
    for a in sorted(alerts, key=lambda x: (x.get("ts") or 0, x.get("id") or 0)):
        directional = a.get("direction") in ("up", "down")
        key = (a.get("sym"), directional)
        w = win if directional else nwin
        ts, sev = float(a.get("ts") or 0), int(a.get("severity") or 0)
        focus = bool((a.get("data") or {}).get("focus")) if c.get("keep_focus", True) else False
        prev = last.get(key)
        if prev and w > 0 and ts - prev[0] < w and sev <= prev[1] and not focus:
            out.add(a.get("id"))
            continue
        last[key] = (ts, sev)
    return out
