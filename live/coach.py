"""Prop-firm challenge and tilt coach, both read from the paper account's own trades (nothing new is traded).

Challenge (rules/challenge.json): from a start day, reach the profit target without breaking the daily loss or the
overall drawdown limit, over at least `min_days` trading days. The status says where you stand and what broke.

Tilt coach: your own patterns, in numbers: how trades go right after losses, by who took them (auto, you, override,
aggressive), by hour, and on busy days. The risk check shows a warning (never a block) when you are on a losing streak.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .clock import IST

RULES = Path(__file__).resolve().parents[1] / "rules" / "challenge.json"
_DEF = {"profit_target_pct": 10.0, "max_drawdown_pct": 10.0, "daily_loss_pct": 4.0, "min_days": 20, "max_days": 60}


def rules() -> dict:
    try:
        return {**_DEF, **json.loads(RULES.read_text())}
    except Exception:
        return dict(_DEF)


def _day(t) -> str:
    return dt.datetime.fromtimestamp(t["entry_ts"], IST).date().isoformat()


def challenge(trades: list, capital: float, start: str | None) -> dict:
    """trades: closed paper trades (dicts with entry_ts, exit_ts, net). start: ISO day or None (= first trade day)."""
    r = rules()
    closed = sorted((t for t in trades if t.get("status") == "closed" and t.get("net") is not None), key=lambda t: t["exit_ts"] or 0)
    if not closed or not capital:
        return {"state": "not started", "rules": r, "why": "No closed paper trades yet."}
    start = start or _day(closed[0])
    closed = [t for t in closed if _day(t) >= start]
    by_day = {}
    for t in closed:
        by_day.setdefault(_day(t), []).append(t["net"])
    days = sorted(by_day)
    eq, peak, worst_dd, worst_day, broke = 0.0, 0.0, 0.0, 0.0, None
    curve = []
    for d in days:
        pnl = sum(by_day[d])
        worst_day = min(worst_day, pnl)
        if broke is None and pnl < -capital * r["daily_loss_pct"] / 100:
            broke = f"Daily loss limit broken on {d}: ₹{pnl:,.0f} (limit −₹{capital * r['daily_loss_pct'] / 100:,.0f})."
        eq += pnl
        peak = max(peak, eq)
        worst_dd = min(worst_dd, eq - peak)
        if broke is None and eq - peak < -capital * r["max_drawdown_pct"] / 100:
            broke = f"Drawdown limit broken on {d}: ₹{eq - peak:,.0f} from the best point (limit −₹{capital * r['max_drawdown_pct'] / 100:,.0f})."
        curve.append({"day": d, "pnl": round(pnl), "equity": round(eq)})
    target = capital * r["profit_target_pct"] / 100
    n_days = len(days)
    if broke:
        state = "failed"
    elif eq >= target and n_days >= r["min_days"]:
        state = "passed"
    elif n_days >= r["max_days"]:
        state, broke = "failed", f"Out of time: {n_days} trading days without reaching +₹{target:,.0f}."
    else:
        state = "in progress"
    return {"state": state, "why": broke, "start": start, "days": n_days, "net": round(eq), "target": round(target),
            "progress_pct": round(max(0.0, eq) / target * 100) if target else None, "worst_day": round(worst_day),
            "max_drawdown": round(worst_dd), "trades": len(closed), "rules": r, "curve": curve[-60:],
            "days_left": max(0, r["min_days"] - n_days)}


def patterns(trades: list) -> dict:
    """Plain numbers about how you trade, for the Paper page."""
    closed = sorted((t for t in trades if t.get("status") == "closed" and t.get("net") is not None), key=lambda t: t["entry_ts"])
    if len(closed) < 5:
        return {"lines": [], "n": len(closed)}

    def agg(xs):
        n = len(xs)
        return {"n": n, "net": round(sum(xs)), "avg": round(sum(xs) / n) if n else None,
                "win_pct": round(sum(1 for x in xs if x > 0) / n * 100) if n else None}
    allx = [t["net"] for t in closed]
    base = agg(allx)
    after = []
    streak = 0
    for t in closed:
        if streak >= 2:
            after.append(t["net"])
        streak = streak + 1 if t["net"] < 0 else 0
    by_mode, by_hour, by_day = {}, {}, {}
    for t in closed:
        by_mode.setdefault(t.get("mode") if not str(t.get("mode") or "").startswith("factory:") else "Lab rule", []).append(t["net"])
        h = dt.datetime.fromtimestamp(t["entry_ts"], IST).hour
        by_hour.setdefault(f"{h:02d}:00", []).append(t["net"])
        by_day.setdefault(_day(t), []).append(t["net"])
    busy = [sum(v) for v in by_day.values() if len(v) > 6]
    calm = [sum(v) for v in by_day.values() if len(v) <= 6]
    lines = []
    a = agg(after)
    if a["n"] >= 3:
        lines.append(f"After 2 losses in a row: {a['n']} trades, won {a['win_pct']}%, average ₹{a['avg']:,} "
                     f"(all trades: {base['win_pct']}%, ₹{base['avg']:,}).")
    for mname, xs in sorted(by_mode.items(), key=lambda kv: sum(kv[1])):
        s = agg(xs)
        lines.append(f"Taken by {mname}: {s['n']} trades, net ₹{s['net']:,}, won {s['win_pct']}%.")
    if busy and calm:
        lines.append(f"Busy days (7+ trades): {len(busy)} day(s), average ₹{sum(busy) / len(busy):,.0f} a day; calmer days: "
                     f"{len(calm)} day(s), average ₹{sum(calm) / len(calm):,.0f}.")
    hours = sorted(((h, agg(v)) for h, v in by_hour.items() if len(v) >= 3), key=lambda x: x[1]["net"])
    if hours:
        lines.append(f"Worst hour: {hours[0][0]} (₹{hours[0][1]['net']:,} over {hours[0][1]['n']} trades); "
                     f"best hour: {hours[-1][0]} (₹{hours[-1][1]['net']:,}).")
    return {"lines": lines, "n": len(closed), "base": base, "after_2_losses": a,
            "by_mode": {k: agg(v) for k, v in by_mode.items()}, "by_hour": {k: agg(v) for k, v in by_hour.items()}}


def streak_warning(trades_today: list, history: list) -> str | None:
    """A warning for the risk check when today's last two closed trades both lost (information, never a block)."""
    closed = sorted((t for t in trades_today if t.get("status") == "closed" and t.get("net") is not None), key=lambda t: t["exit_ts"] or 0)
    if len(closed) < 2 or not (closed[-1]["net"] < 0 and closed[-2]["net"] < 0):
        return None
    p = patterns(history)
    a = p.get("after_2_losses") or {}
    if a.get("n", 0) >= 3:
        return (f"Two losses in a row today. Your trades right after two losses won {a['win_pct']}% (average ₹{a['avg']:,}); "
                "a 30-minute pause usually helps.")
    return "Two losses in a row today: a 30-minute pause usually helps."
