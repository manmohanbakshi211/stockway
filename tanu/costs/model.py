"""Cost of one option round trip (buy at the ask, sell at the bid), in ₹ and as % of the premium paid.

Parts:
- spread: the full bid-ask spread (half paid on entry, half on exit), measured from live quotes;
- slippage: extra ticks beyond the quote (a bullet);
- charges: brokerage, STT, exchange, SEBI, stamp, GST at the rates valid that day (tanu/facts/charges.json);
- theta: time decay of an at-the-money option while held. An ATM premium is about proportional to the square root
  of the time left, so it loses premium / (2 x time left) per unit of time: per minute 1 / (2 x minutes to expiry).
"""
from __future__ import annotations

import json
from pathlib import Path

from ..data.facts import Facts, _pick, _d

RULES = Path(__file__).with_name("rules.json")
SESSION_MIN = 375


def rules(on, path: Path = RULES) -> dict:
    e = _pick(json.loads(Path(path).read_text())["entries"], _d(on))
    if e is None:
        raise LookupError(f"no cost rules valid on {on}")
    return e


def theta_pct(hold_min: float, minutes_to_expiry: float) -> float:
    """% of an ATM premium lost to time over hold_min minutes (exact for the square-root rule)."""
    t = max(float(minutes_to_expiry), 1.0)
    h = min(float(hold_min), t)
    return round((1 - ((t - h) / t) ** 0.5) * 100, 3)


def minutes_to_expiry(dte_days: int, minutes_left_today: float = SESSION_MIN / 2) -> float:
    """Trading minutes until expiry close: the rest of today plus full sessions after it."""
    return max(1.0, minutes_left_today + max(0, dte_days) * SESSION_MIN)


def round_trip(premium: float, spread: float, qty: int, day, *, hold_min: float, mins_to_expiry: float,
               extra_slip_ticks: float = 1, tick: float = 0.05, facts: Facts | None = None) -> dict:
    """premium: mid price per unit; spread: ask - bid per unit; qty: units (lots x lot size)."""
    f = facts or Facts()
    spread = max(float(spread), tick)
    entry = premium + spread / 2 + extra_slip_ticks * tick / 2
    exit_ = max(premium - spread / 2 - extra_slip_ticks * tick / 2, 0.0)
    paid = entry * qty
    spread_rs = (entry - exit_) * qty
    charges_rs = f.option_charges(entry, exit_, qty, day)
    th = theta_pct(hold_min, mins_to_expiry)
    theta_rs = premium * qty * th / 100
    total = spread_rs + charges_rs + theta_rs
    pct = lambda x: round(x / paid * 100, 3) if paid else None
    return {"paid_rs": round(paid, 2), "spread_rs": round(spread_rs, 2), "charges_rs": round(charges_rs, 2),
            "theta_rs": round(theta_rs, 2), "total_rs": round(total, 2), "spread_pct": pct(spread_rs),
            "charges_pct": pct(charges_rs), "theta_pct": th, "total_pct": pct(total)}
