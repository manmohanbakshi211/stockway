"""Risk gate: every trade idea gets PASS or BLOCK, with the reason, before it can become a paper trade.

The gate never places anything (Stockway places no real orders, ever). It answers: does this idea fit the rules
you set? Checks, in order:

  window        no new entries before 09:20 (opening range still forming) or after the cut-off (14:45)
  ban           stock in the F&O ban period: no new positions allowed by the exchange
  duplicate     already holding a position on this symbol
  day limits    daily loss limit reached, or trades per day used up
  history       the alert type is historically losing on our own replay
  reward:risk   the setup's target is less than `min_rr` times its risk
  spread        option bid-ask spread wider than `max_spread_pct` of the price (the spread eats the trade)
  price         option cheaper than `min_premium` (lottery tickets: most expire worthless)
  size          lots so that hitting the stop loses at most the max loss per trade; if even one lot is too much,
                BLOCK (never round up to one lot)

The first four are hard rules: a paper trade cannot be taken against them, even by hand. The rest can be
overridden by hand with a confirmation; such trades are tagged "override" so their record is kept apart.
"""
from __future__ import annotations

import datetime as dt

from .paper import costs

DEFAULTS = {
    "window_start": "09:20",
    "window_end": "14:45",
    "min_rr": 1.2,
    "max_spread_pct": 3.0,
    "min_premium": 3.0,
    "block_losing_history": True,
}


def check(alert: dict, opt: dict | None, *, now: dt.datetime, paper_cfg: dict, guard: dict, open_syms: set,
          ban: list | None = None, cfg: dict | None = None) -> dict:
    c = {**DEFAULTS, **(cfg or {})}
    reasons, warns, hard = [], [], []

    def stop(msg):                      # rules no override can break: time window, ban, duplicate, day limits
        reasons.append(msg)
        hard.append(msg)
    hm = now.strftime("%H:%M")
    if now.weekday() >= 5:
        stop("Market closed today (weekend).")
    if hm < c["window_start"]:
        stop(f"Before {c['window_start']}: the opening range is still forming." if hm >= "09:15" else
             "Market not open yet: pre-open prices are not tradable.")
    if hm >= min(c["window_end"], paper_cfg.get("no_entry_after", "14:45")):
        stop(f"After {min(c['window_end'], paper_cfg.get('no_entry_after', '14:45'))}: too late for a new intraday trade.")
    if ban and alert.get("sym") in ban:
        stop(f"{alert['sym']} is in the F&O ban: the exchange allows no new positions.")
    if alert.get("sym") in open_syms:
        stop(f"Already holding a {alert['sym']} position.")
    if guard.get("blocked"):
        stop(guard.get("why") or "Day limits reached.")
    data = alert.get("data") or {}
    grade = (data.get("history") or {}).get("grade") or data.get("history_grade")
    if c["block_losing_history"] and grade == "losing":
        reasons.append("This signal type has lost money on our own replay of past years.")
    rr = (data.get("setup_levels") or data.get("levels") or {}).get("rr")
    if rr is not None and float(rr) < float(c["min_rr"]):
        reasons.append(f"Reward:risk {float(rr):.2f} is under your minimum {c['min_rr']}.")
    lots, max_loss, capital_used, per_lot = 0, None, None, None
    if not opt:
        stop("No tradable option found for this idea.")
    else:
        ltp = opt.get("ltp") or 0
        ask = opt.get("ask") or ltp
        bid = opt.get("bid")
        if bid and ask and ask > bid > 0:
            spread = (ask - bid) / ((ask + bid) / 2) * 100
            if spread > float(c["max_spread_pct"]):
                reasons.append(f"Bid-ask spread {spread:.1f}% of the price (limit {c['max_spread_pct']}%): the spread alone eats the trade.")
        if ask and ask < float(c["min_premium"]):
            reasons.append(f"Option at ₹{ask:.2f}: under ₹{c['min_premium']}, these mostly expire worthless.")
        lot = int(opt.get("lot") or 0)
        stop_pct = float(paper_cfg.get("stop_pct", 20))
        if ask and lot:
            stop_px = ask * (1 - stop_pct / 100)
            per_lot = ask * lot * stop_pct / 100 + costs(ask, stop_px, lot)
            budget = float(paper_cfg.get("capital", 500000)) * float(paper_cfg.get("risk_pct", 1.0)) / 100
            lots = min(int(paper_cfg.get("max_lots", 5)), int(budget // per_lot)) if per_lot > 0 else 0
            if lots < 1:
                reasons.append(f"One lot risks ₹{per_lot:,.0f} at the stop, more than your max loss per trade ₹{budget:,.0f}.")
            max_loss = round(per_lot * max(lots, 1), 0)
            capital_used = round(ask * lot * max(lots, 1), 0)
            if capital_used > float(paper_cfg.get("capital", 500000)) * 0.25:
                warns.append(f"Uses ₹{capital_used:,.0f}, over a quarter of the capital.")
    verdict = "BLOCK" if reasons else "PASS"
    return {"verdict": verdict, "reasons": reasons, "hard": hard, "can_override": bool(reasons) and not hard, "warnings": warns, "lots": lots if verdict == "PASS" else 0,
            "max_loss": max_loss, "capital_used": capital_used, "per_lot_loss": round(per_lot, 0) if per_lot else None,
            "rules": {"max_loss_per_trade": round(float(paper_cfg.get("capital", 500000)) * float(paper_cfg.get("risk_pct", 1.0)) / 100, 0),
                      "daily_loss_limit": guard.get("loss_limit"), "trades_today": guard.get("trades_today"),
                      "max_trades": paper_cfg.get("max_trades_day"), "min_rr": c["min_rr"]}}
