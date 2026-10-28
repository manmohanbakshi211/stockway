"""Option-buying proxy backtest.

We have no free 10-year option-chain history, so we test the question that matters for
an option buyer: on flagged days, did Bank Nifty actually move MORE than the options
market had priced in?

  Buy an ATM straddle at the open, exit at the close (intraday).
  Cost of that bet ~ expected move priced by India VIX:
        cost = straddle_factor x implied_daily_sigma          (in % of spot)
  Payoff ~ |close - open| / open                              (in % of spot)
  P&L  = payoff - cost - trading costs (cost_frac_of_implied x implied)

Positive P&L on flagged days = realised moves beat implied => buying options on those days
had an edge. This is a proxy: it ignores intraday gamma path, skew, and Bank Nifty's own IV.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def straddle_proxy(oos: pd.DataFrame, X: pd.DataFrame, y: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    b = cfg["backtest"]
    g = oos[oos["target"] == "big_move"].set_index("date")
    df = pd.DataFrame(index=g.index)
    df["prob"] = g["prob"]
    df["flag"] = g["flag"]
    df["implied"] = X.loc[g.index, "bn_implied_daily"]
    df["move"] = y.loc[g.index, "oc_pct"].abs()
    df["cost"] = df["implied"] * b["straddle_factor"]
    df["fees"] = df["implied"] * b["cost_frac_of_implied"]
    df["pnl_pct"] = df["move"] - df["cost"] - df["fees"]
    df = df.dropna(subset=["pnl_pct"])
    return df


def summarize(bt: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for label, sub in (("Every day", bt), ("Flagged days only", bt[bt["flag"] == 1]),
                       ("Not flagged", bt[bt["flag"] == 0])):
        if sub.empty:
            continue
        wins = sub[sub["pnl_pct"] > 0]["pnl_pct"]
        losses = sub[sub["pnl_pct"] <= 0]["pnl_pct"]
        rows.append({
            "Strategy": label,
            "Trades": len(sub),
            "Win rate": f"{(sub['pnl_pct'] > 0).mean():.0%}",
            "Avg win (% of spot)": f"{wins.mean():.2%}" if len(wins) else "-",
            "Avg loss (% of spot)": f"{losses.mean():.2%}" if len(losses) else "-",
            "Expectancy / trade": f"{sub['pnl_pct'].mean():.3%}",
            "Total (% of spot)": f"{sub['pnl_pct'].sum():.1%}",
        })
    return pd.DataFrame(rows)
