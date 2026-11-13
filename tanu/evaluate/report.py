"""Evaluation (ADR-0005): each setup's trades split into learn (first 80% of the replayed days) and holdout (the last
20%, never used for any choice), with costs already inside each trade. A setup "passes" only when:
  - learn: at least min_trades trades, average > 0 and t >= the multiple-testing bar (Bonferroni over the setups tried,
    5% two-sided), and
  - holdout: at least min_hold trades and average > 0,
  - and it beats the baseline setup on both.
Every result is kept (failed ones too)."""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd


def stats(x) -> dict:
    x = pd.Series(x, dtype=float).dropna()
    n = len(x)
    if n == 0:
        return {"n": 0}
    sd = float(x.std(ddof=1)) if n > 1 else 0.0
    out = {"n": n, "avg_pct": round(float(x.mean()), 2), "win_pct": round(float((x > 0).mean() * 100), 1),
           "t": round(float(x.mean() / (sd / math.sqrt(n))), 2) if n > 1 and sd > 0 else 0.0}
    pos = x[x > 0].sort_values(ascending=False)
    if len(pos) >= 5:
        out["top10_share_pct"] = round(float(pos.head(max(1, n // 10)).sum() / pos.sum() * 100), 0)
    return out


def evaluate(trades: pd.DataFrame, baseline: str = "random_baseline", min_trades: int = 30, min_hold: int = 10,
             hold_frac: float = 0.2, k: int | None = None) -> dict:
    """baseline: the yardstick setup id, or its prefix: for a search id "entry__exit__filter" the yardstick is
    "<baseline>__exit__filter" (same exit and filter). k: number of ideas tried (default: the setups here)."""
    t = trades.dropna(subset=["pct"]).copy()
    days = sorted(t["day"].unique())
    if not days:
        return {"days": 0, "setups": []}
    cut = days[int(len(days) * (1 - hold_frac))]
    t["part"] = np.where(t["day"] >= cut, "hold", "learn")
    def base_of(s: str) -> str:
        return baseline + s[s.index("__"):] if "__" in s else baseline

    is_base = lambda s: s == baseline or s.startswith(baseline + "__")
    tested = [s for s in t["setup"].unique() if not is_base(s)]
    k = max(1, k or len(tested))
    t_bar = round(NormalDist().inv_cdf(1 - 0.05 / (2 * k)), 2)
    bases = {b: {p: stats(t[(t["setup"] == b) & (t["part"] == p)]["pct"]) for p in ("learn", "hold")}
             for b in {base_of(s) for s in t["setup"].unique()}}
    out = {"days": len(days), "first": days[0], "last": days[-1], "holdout_from": cut, "t_bar": t_bar, "k": k,
           "baseline": bases.get(baseline, {}), "setups": []}
    for s, g in t.groupby("setup"):
        base = bases.get(base_of(s), {})
        L, H = stats(g[g["part"] == "learn"]["pct"]), stats(g[g["part"] == "hold"]["pct"])
        row = {"setup": s, "learn": L, "hold": H, "rs_per_trade": round(float(g["pnl_rs"].mean()), 0),
               "by_year": {y: stats(gg["pct"]) for y, gg in g.groupby(g["day"].str[:4])},
               "by_sym": {y: stats(gg["pct"]) for y, gg in g.groupby("sym")},
               "exits": g["reason"].value_counts().to_dict()}
        if not is_base(s):
            beats = all(row[p].get("avg_pct", -1e9) > base.get(p, {}).get("avg_pct", -1e9) for p in ("learn", "hold"))
            row["passes"] = bool(L.get("n", 0) >= min_trades and L.get("avg_pct", -1) > 0 and L.get("t", 0) >= t_bar
                                 and H.get("n", 0) >= min_hold and H.get("avg_pct", -1) > 0 and beats)
            why = []
            if L.get("n", 0) < min_trades:
                why.append(f"only {L.get('n', 0)} learn trades")
            if L.get("avg_pct", -1) <= 0:
                why.append("learn average not above 0")
            elif L.get("t", 0) < t_bar:
                why.append(f"learn t {L.get('t')} < {t_bar} (luck not ruled out)")
            if H.get("n", 0) < min_hold:
                why.append(f"only {H.get('n', 0)} holdout trades")
            elif H.get("avg_pct", -1) <= 0:
                why.append("holdout average not above 0")
            if not beats:
                why.append("does not beat the baseline")
            row["why_not"] = why
        out["setups"].append(row)
    out["setups"].sort(key=lambda r: -(r["hold"].get("avg_pct") or -999))
    return out


def markdown(rep: dict) -> str:
    lines = [f"Replayed {rep['days']} days ({rep.get('first')} to {rep.get('last')}); holdout from {rep.get('holdout_from')}; "
             f"ideas tried k = {rep.get('k')}, pass bar t >= {rep.get('t_bar')}.", "", "| Setup | Learn n / avg / t | Holdout n / avg | ₹ / trade | Verdict |",
             "|---|---|---|---|---|"]
    for r in rep["setups"]:
        L, H = r["learn"], r["hold"]
        verdict = "baseline" if "passes" not in r else ("PASS" if r["passes"] else "; ".join(r["why_not"]))
        lines.append(f"| {r['setup']} | {L.get('n', 0)} / {L.get('avg_pct')}% / {L.get('t')} | {H.get('n', 0)} / "
                     f"{H.get('avg_pct')}% | {r['rs_per_trade']} | {verdict} |")
    return "\n".join(lines)
