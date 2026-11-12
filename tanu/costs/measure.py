"""Measured inputs for the cost model.

- atm_quotes(chains): from option-chain snapshots, the at-the-money call and put of every snapshot (strike nearest
  to spot) with bid, ask, mid, spread and spread % of mid. Snapshots without a two-sided quote are dropped.
- day_spreads(...): per symbol and day, the median ATM spread %, median mid premium, number of snapshots.
- lab_drag(path): the legacy 5-year Lab's average result of a random ATM buy (every bar, both directions, one exit)
  per symbol and year, read in place. Note: for stock options that table used an assumed slippage (at least ₹0.25 or
  1% of the price per side), not measured quotes, so measured spreads are preferred where they exist.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def atm_quotes(chains: pd.DataFrame) -> pd.DataFrame:
    c = chains.dropna(subset=["spot", "strike", "bid", "ask"])
    c = c[(c["bid"] > 0) & (c["ask"] > c["bid"])].copy()
    if c.empty:
        return pd.DataFrame(columns=["sym", "ts", "expiry", "side", "strike", "bid", "ask", "mid", "spread", "spread_pct"])
    c["dist"] = (c["strike"] - c["spot"]).abs()
    near = c.groupby(["sym", "ts", "expiry"])["dist"].transform("min")
    a = c[c["dist"] == near].copy()
    a["mid"] = (a["bid"] + a["ask"]) / 2
    a["spread"] = a["ask"] - a["bid"]
    a["spread_pct"] = a["spread"] / a["mid"] * 100
    return a[["sym", "ts", "expiry", "side", "strike", "bid", "ask", "mid", "spread", "spread_pct"]].reset_index(drop=True)


def nearest_expiry(q: pd.DataFrame) -> pd.DataFrame:
    """Keep only each symbol's nearest expiry in each snapshot (what an intraday buyer trades)."""
    if q.empty:
        return q
    first = q.groupby(["sym", "ts"])["expiry"].transform("min")
    return q[q["expiry"] == first]


def day_spreads(chains: pd.DataFrame, day: str) -> pd.DataFrame:
    q = nearest_expiry(atm_quotes(chains))
    if q.empty:
        return pd.DataFrame(columns=["sym", "day", "expiry", "spread_pct", "spread", "mid", "snapshots"])
    g = q.groupby("sym")
    out = pd.DataFrame({"expiry": g["expiry"].min(), "spread_pct": g["spread_pct"].median(), "spread": g["spread"].median(),
                        "mid": g["mid"].median(), "snapshots": g["ts"].nunique()}).reset_index()
    out.insert(1, "day", day)
    return out


def lab_drag(path: Path | str, exit_col: str = "pnl_std", since: str | None = None) -> pd.DataFrame:
    """Per symbol: mean % of premium of a random buy (bars on the 15-minute grid, both directions), n, and the same
    for the last 12 months of the table."""
    cols = ["sym", "date", "tod", "dir", "premium", exit_col]
    d = pd.read_parquet(path, columns=cols, filters=[("dir", "!=", 0)])
    d = d[(d["tod"] >= 10) & (d["tod"] <= 315) & (d["tod"] % 15 == 0) & (d["premium"] > 0)]
    d["pct"] = d[exit_col] / d["premium"] * 100
    d["date"] = d["date"].astype(str)
    if since:
        d = d[d["date"] >= since]
    last = sorted(d["date"].unique())
    cut = last[-250] if len(last) > 250 else last[0]
    g = d.groupby("sym")["pct"]
    recent = d[d["date"] >= cut].groupby("sym")["pct"]
    out = pd.DataFrame({"lab_drag_pct": g.mean(), "lab_n": g.size(), "lab_drag_12m_pct": recent.mean()}).reset_index()
    out["sym"] = out["sym"].astype(str)
    return out.replace([np.inf, -np.inf], np.nan)
