"""Scheduled-event features: expiries, budget days, holidays, custom events.

All of these are known in advance, so they are computed for the target day itself.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

WEEKDAYS = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4}


def _rule_for(date: pd.Timestamp, rules: list[dict]):
    wd = None
    for r in rules:
        if date >= pd.Timestamp(r["from"]):
            wd = r["weekday"]
    return WEEKDAYS.get(wd) if wd else None


def _snap_to_trading(day: pd.Timestamp, trading: pd.DatetimeIndex) -> pd.Timestamp | None:
    """Expiry on a holiday moves to the previous trading day."""
    prior = trading[trading <= day]
    if len(prior) == 0:
        return None
    snapped = prior[-1]
    return snapped if (day - snapped).days <= 4 else None


def expiry_dates(trading: pd.DatetimeIndex, cfg: dict) -> tuple[set, set]:
    rules = cfg["expiry_rules"]
    start, end = trading.min(), trading.max() + pd.Timedelta(days=40)
    monthly, weekly = set(), set()
    for month_start in pd.date_range(start.replace(day=1), end, freq="MS"):
        month_end = month_start + pd.offsets.MonthEnd(0)
        wd = _rule_for(month_end, rules["monthly"])
        if wd is None:
            continue
        days = pd.date_range(month_start, month_end, freq="D")
        cands = days[days.weekday == wd]
        if len(cands):
            d = _snap_to_trading(cands[-1], trading) if cands[-1] <= trading.max() else cands[-1]
            if d is not None:
                monthly.add(d)
    for day in pd.date_range(start, end, freq="D"):
        wd = _rule_for(day, rules["weekly"])
        if wd is not None and day.weekday() == wd:
            d = _snap_to_trading(day, trading) if day <= trading.max() else day
            if d is not None:
                weekly.add(d)
    return monthly, weekly


def event_features(target_days: pd.DatetimeIndex, trading: pd.DatetimeIndex, cfg: dict) -> pd.DataFrame:
    """target_days may include one future day (the next session)."""
    all_days = trading.union(target_days)
    monthly, weekly = expiry_dates(all_days, cfg)
    monthly_sorted = np.array(sorted(monthly), dtype="datetime64[ns]")

    f = pd.DataFrame(index=target_days)
    f["dow"] = target_days.weekday
    f["is_monthly_expiry"] = [int(d in monthly) for d in target_days]
    f["is_weekly_expiry"] = [int(d in weekly) for d in target_days]
    nxt = np.searchsorted(monthly_sorted, target_days.values)
    days_to = []
    for d, i in zip(target_days, nxt):
        if i < len(monthly_sorted):
            days_to.append(int(np.busday_count(d.date(), pd.Timestamp(monthly_sorted[i]).date())))
        else:
            days_to.append(np.nan)
    f["bdays_to_monthly_expiry"] = days_to
    budget = {pd.Timestamp(d) for d in cfg.get("budget_days", [])}
    f["is_budget_day"] = [int(d in budget) for d in target_days]
    f["is_day_after_budget"] = 0
    for b in budget:
        after = all_days[all_days > b]
        if len(after) and after[0] in f.index:
            f.loc[after[0], "is_day_after_budget"] = 1
    prev = pd.Series(all_days, index=all_days).shift(1)
    f["calendar_gap_days"] = [(d - prev.get(d)).days if pd.notna(prev.get(d)) else 1 for d in target_days]
    f["is_month_end_week"] = (target_days.days_in_month - target_days.day < 7).astype(int)

    ev_path = cfg.get("events_csv")
    if ev_path and Path(ev_path).exists():
        ev = pd.read_csv(ev_path)
        ev["date"] = pd.to_datetime(ev["date"])
        for name, grp in ev.groupby("event"):
            col = "is_" + "".join(ch if ch.isalnum() else "_" for ch in str(name).lower())
            days = set(grp["date"])
            f[col] = [int(d in days) for d in target_days]
    return f
