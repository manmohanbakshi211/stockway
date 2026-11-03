"""Money a trade blocks per lot: the premium paid plus an estimate of the margin on sold options.

Part of the gun: it knows nothing about any strategy. The rates are dated data in rules/margin.json (bullets); a day
uses the newest entry that started on or before it, so past test trades keep the numbers they had.
It is an estimate (your broker's margin calculator is the real number) and is used only to decide which capital level
could have afforded a test-bench trade. Nothing here talks to a broker.

legs: [{"side": "CE"/"PE", "strike": float, "q": +1 bought / -1 sold, "price": per unit}]
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

FILE = Path(__file__).resolve().parents[1] / "rules" / "margin.json"
_FALLBACK = [{"from": "2000-01-01", "index": {"naked_pct": 7.0, "hedged_exposure_pct": 2.0, "expiry_day_extra_pct": 2.0},
              "stock": {"naked_pct": 18.5, "hedged_exposure_pct": 3.5, "expiry_day_extra_pct": 0.0}}]
_CACHE: dict = {}


def _all() -> list:
    try:
        mt = FILE.stat().st_mtime
        if _CACHE.get("mt") != mt:
            rows = json.loads(FILE.read_text()).get("rates") or []
            _CACHE.update(mt=mt, rows=sorted((r for r in rows if r.get("from")), key=lambda r: r["from"]) or _FALLBACK)
        return _CACHE["rows"]
    except Exception:
        return _FALLBACK


def rates(day=None, is_index: bool = True) -> dict:
    day = (day.isoformat() if isinstance(day, dt.date) else str(day or dt.date.today().isoformat()))[:10]
    rows = _all()
    pick = rows[0]
    for r in rows:
        if r["from"] <= day:
            pick = r
    return dict(pick.get("index" if is_index else "stock") or _FALLBACK[0]["index" if is_index else "stock"])


def money_needed(legs: list, lot: int, is_index: bool, day=None, expiry_day: bool = False) -> dict:
    """{"premium", "credit", "margin", "need", "max_loss"} per lot, in rupees. max_loss None = no limit (naked sale)."""
    r = rates(day, is_index)
    extra = float(r.get("expiry_day_extra_pct") or 0) if expiry_day else 0.0
    premium = sum(l["price"] * lot for l in legs if l["q"] > 0)
    credit = sum(l["price"] * lot for l in legs if l["q"] < 0)
    net_debit = premium - credit
    longs = [dict(l) for l in legs if l["q"] > 0]
    pair_loss = exposure = naked = 0.0
    uncovered = False
    for s in sorted((l for l in legs if l["q"] < 0), key=lambda l: l["strike"]):
        notional = float(s["strike"]) * lot
        same = [l for l in longs if l["side"] == s["side"]]
        if same:
            # the bought option nearest to the sold one covers it; the pair can lose at most the strike gap (or nothing
            # beyond the premiums when the bought one is further in the money, as in a debit spread)
            c = min(same, key=lambda l: abs(l["strike"] - s["strike"]))
            longs.remove(c)
            gap = (c["strike"] - s["strike"]) if s["side"] == "CE" else (s["strike"] - c["strike"])
            pair_loss += max(0.0, gap) * lot
            exposure += (float(r["hedged_exposure_pct"]) + extra) / 100 * notional
        else:
            uncovered = True
            naked += (float(r["naked_pct"]) + extra) / 100 * notional
    max_loss = None if uncovered else net_debit + pair_loss
    need = (max(max_loss, 0.0) if max_loss is not None else max(net_debit, 0.0)) + exposure + naked
    return {"premium": round(premium, 2), "credit": round(credit, 2), "margin": round(exposure + naked + pair_loss, 2),
            "need": round(need, 2), "max_loss": round(max_loss, 2) if max_loss is not None else None}
