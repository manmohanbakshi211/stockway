"""Capital: read any amount the way people write it, and the account rules that fit it (rules/capital.json).

"40k", "40 K", "₹40,000", "1.5 lakh", "1.5L", "2,00,000", "1 cr", "1.2 crore", "500000" all work. Anything else gives
a clear message instead of a wrong number (the old field read "40k" as ₹40).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

FILE = Path(__file__).resolve().parents[1] / "rules" / "capital.json"
_FALLBACK = {"tiers": [{"upto": 60000, "name": "Small account", "risk_pct": 5.0, "max_lots": 1, "max_open": 1, "max_trades_day": 2,
                        "max_daily_loss_pct": 4.0, "profit_lock_pct": 3.0},
                       {"upto": None, "name": "Large account", "risk_pct": 1.0, "max_lots": 5, "max_open": 6, "max_trades_day": 12,
                        "max_daily_loss_pct": 3.0, "profit_lock_pct": 0}],
             "limits": {"min": 10000, "max": 100000000}}
UNITS = {"k": 1e3, "thousand": 1e3, "l": 1e5, "lac": 1e5, "lakh": 1e5, "lakhs": 1e5, "lacs": 1e5,
         "cr": 1e7, "crore": 1e7, "crores": 1e7, "m": 1e6, "mn": 1e6, "million": 1e6}


def _cfg() -> dict:
    try:
        return json.loads(FILE.read_text())
    except Exception:
        return _FALLBACK


def parse_amount(text) -> float:
    """Rupees from text. Raises ValueError with a message a person can act on."""
    if isinstance(text, (int, float)):
        v = float(text)
    else:
        t = re.sub(r"^(rs\.?|inr)\s*", "", str(text or "").strip().lower().replace("₹", "")).strip()
        t = t.replace(",", "").replace("_", "")
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([a-z]*)", t)
        if not m:
            raise ValueError(f"Couldn't read '{text}' as an amount. Try 40000, 40k, 1.5 lakh or 2 crore.")
        num, unit = float(m.group(1)), m.group(2)
        if unit and unit not in UNITS:
            raise ValueError(f"Unknown unit '{unit}' in '{text}'. Use k, lakh (L) or crore (cr).")
        v = num * UNITS.get(unit, 1)
    lim = _cfg().get("limits") or _FALLBACK["limits"]
    if not v == v or v < lim["min"]:
        raise ValueError(f"Capital must be at least ₹{lim['min']:,.0f} (one lot of an index option needs that much).")
    if v > lim["max"]:
        raise ValueError(f"₹{v:,.0f} looks too big: the most is ₹{lim['max']:,.0f}. Check the zeros.")
    return round(v, 2)


def tier(capital: float) -> dict:
    """The account rules for this capital (profit lock in rupees as well as %)."""
    tiers = _cfg().get("tiers") or _FALLBACK["tiers"]
    for t in tiers:
        if t.get("upto") is None or capital <= float(t["upto"]):
            out = dict(t)
            out["profit_lock"] = round(capital * float(t.get("profit_lock_pct") or 0) / 100)
            return out
    return dict(tiers[-1])


def paper_settings(capital: float) -> dict:
    """The paper account settings for this capital (keys as in live/paper.py DEFAULTS)."""
    t = tier(capital)
    return {"capital": capital, "risk_pct": t["risk_pct"], "max_lots": t["max_lots"], "max_open": t["max_open"],
            "max_trades_day": t["max_trades_day"], "max_daily_loss_pct": t["max_daily_loss_pct"], "profit_lock": t["profit_lock"]}
