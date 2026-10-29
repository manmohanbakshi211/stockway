"""Run every strategy variant over history, then choose variants walk-forward and report out-of-sample results.

Walk-forward: for each month, look only at the previous 6 months, keep the variant of each strategy with the
most reliable profit (t-stat of trade P&L), require enough trades and a profit factor above 1.15, and trade it in
the next month. Only those next-month trades are counted. This is how a desk avoids fooling itself.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .data import UNDERLYINGS, day_table, hist_dir, iter_days, vix_table
from .engine import charges, simulate
from .strategies import FAMILIES, NICE, SELLING, describe, grid

DEFAULTS = {
    "capital": 500000,
    "risk_pct": 1.5,         # planned loss (stops hit, +50% buffer) of one trade, % of capital
    "worst_pct": 5.0,        # theoretical worst case of one trade, % of capital
    "alloc_pct": 25.0,       # max margin one strategy may use, % of capital
    "max_lots": 20,
    "train_months": 6,
    "min_trades": {"iron_fly": 40, "iron_condor": 40, "expiry_condor": 10, "orb_spread": 12, "trend_spread": 10,
                   "chart_setup": 12},
    "min_pf": 1.15,
    "min_t": 2.0,            # statistical bar: profit must be ~2 standard errors above zero (filters lucky variants)
}


def lab_dir(data_dir) -> Path:
    d = Path(data_dir) / "lab"
    d.mkdir(parents=True, exist_ok=True)
    return d


def size(row, cfg) -> int:
    cap = cfg["capital"]
    by_risk = min(math.floor(cap * cfg["risk_pct"] / 100 / max(1.0, row["plan_loss_rs"])),
                  math.floor(cap * cfg["worst_pct"] / 100 / max(1.0, row["max_loss_rs"])))
    by_margin = math.floor(cap * cfg["alloc_pct"] / 100 / max(1.0, row["margin_rs"]))
    return int(max(0, min(by_risk, by_margin, cfg["max_lots"])))


def run_grid(data_dir, syms=("NIFTY", "BANKNIFTY"), log=print, should_stop=lambda: False, families=None) -> pd.DataFrame:
    """Every variant of every strategy on every day. Returns one row per trade (per lot)."""
    rows = []
    vix = vix_table(data_dir)
    families = families or list(FAMILIES)
    combos = {fam: list(grid(fam)) for fam in families}
    for si, sym in enumerate(syms):
        lot = UNDERLYINGS[sym]["lot"]
        log(f"Step {2 + si}/5: {sym}: reading days")
        tab = day_table(data_dir, sym)
        if tab.empty:
            log(f"  {sym}: no data")
            continue
        tab = tab.merge(vix, on="date", how="left")
        feats = {r["date"]: r for r in tab.to_dict("records")}
        n, t0 = 0, time.time()
        for day in iter_days(data_dir, sym):
            if should_stop():
                return pd.DataFrame(rows)
            f = feats.get(day.date)
            if not f:
                continue
            for fam in families:
                fn = FAMILIES[fam][0]
                for pid, p in combos[fam]:
                    pos = fn(day, f, p)
                    if pos is None:
                        continue
                    r = simulate(day, pos)
                    if r is None:
                        continue
                    ch = charges(r["fills"], lot)
                    spot = day.spot[pos.entry_m]
                    rows.append({"sym": sym, "date": day.date, "family": fam, "pid": pid,
                                 "net_lot": r["gross_pts"] * lot - ch, "gross_lot": r["gross_pts"] * lot, "costs_lot": ch,
                                 "max_loss_rs": r["max_loss_pts"] * lot + ch, "margin_rs": pos.margin_est(spot, lot),
                                 "plan_loss_rs": pos.planned_loss_pts() * lot + ch,
                                 "premium": r["premium"], "reason": r["reason"], "entry_m": pos.entry_m,
                                 "exit_m": r["exit_m"], "expiry": bool(f.get("is_expiry")), "vix_pct": f.get("vix_pct")})
            n += 1
            if n % 50 == 0:
                log(f"  {sym}: {n} days tested ({time.time() - t0:.0f}s)")
        log(f"  {sym}: {n} days, {sum(1 for r in rows if r['sym'] == sym):,} simulated trades")
    return pd.DataFrame(rows)


def _stats(x: pd.Series) -> dict:
    n = len(x)
    if n == 0:
        return {"n": 0}
    wins, losses = x[x > 0].sum(), -x[x < 0].sum()
    sd = x.std(ddof=1) if n > 1 else 0
    return {"n": n, "mean": float(x.mean()), "sum": float(x.sum()), "win": float((x > 0).mean() * 100),
            "pf": float(wins / losses) if losses > 0 else (99.0 if wins > 0 else 0.0),
            "t": float(x.mean() / (sd / math.sqrt(n))) if sd > 0 else 0.0}


def choose(train: pd.DataFrame, fam: str, cfg) -> tuple[str | None, dict]:
    best, best_s = None, None
    for pid, g in train.groupby("pid"):
        s = _stats(g["net_lot"])                 # judge per lot, independent of sizing
        if s["n"] < cfg["min_trades"].get(fam, 15) or s["mean"] <= 0 or s["pf"] < cfg["min_pf"] or s["t"] < cfg["min_t"]:
            continue
        if best_s is None or s["t"] > best_s["t"]:
            best, best_s = pid, s
    return best, best_s or {}


def walk_forward(trades: pd.DataFrame, cfg: dict, all_days: list) -> dict:
    trades = trades.copy()
    trades["month"] = pd.to_datetime(trades["date"]).dt.to_period("M")
    trades["lots"] = trades.apply(lambda r: size(r, cfg), axis=1)
    trades["net_rs"] = trades["net_lot"] * trades["lots"]
    trades = trades[trades["lots"] > 0]          # a trade too big for the risk limit is not taken
    months = sorted(trades["month"].unique())
    tm = cfg["train_months"]
    oos, choices = [], []
    for i in range(tm, len(months)):
        test, train_m = months[i], months[i - tm:i]
        tr = trades[trades["month"].isin(train_m)]
        te = trades[trades["month"] == test]
        for (sym, fam), g in tr.groupby(["sym", "family"]):
            pid, s = choose(g, fam, cfg)
            choices.append({"month": str(test), "sym": sym, "family": fam, "pid": pid, "train": s})
            if pid:
                oos.append(te[(te["sym"] == sym) & (te["pid"] == pid)])
    oos = pd.concat(oos) if oos else trades.iloc[0:0]
    test_start = months[tm].start_time.date() if len(months) > tm else None
    days = sorted(d for d in all_days if test_start and d >= test_start)
    daily = oos.groupby("date")["net_rs"].sum().reindex(days, fill_value=0.0) if days else pd.Series(dtype=float)
    return {"oos": oos, "daily": daily, "choices": choices, "trades": trades}


def summary(daily: pd.Series, capital: float) -> dict:
    if daily.empty:
        return {}
    eq = daily.cumsum()
    dd = eq - eq.cummax()
    traded = daily[daily != 0]
    sd = daily.std(ddof=1)
    years = max(len(daily) / 250, 1e-9)
    return {"days": int(len(daily)), "trade_days": int(len(traded)), "avg_day": float(daily.mean()),
            "median_day": float(daily.median()), "avg_trade_day": float(traded.mean()) if len(traded) else 0.0,
            "green_pct": float((traded > 0).mean() * 100) if len(traded) else 0.0,
            "pf": float(traded[traded > 0].sum() / -traded[traded < 0].sum()) if (traded < 0).any() else 99.0,
            "total": float(daily.sum()), "max_dd": float(dd.min()), "max_dd_pct": float(dd.min() / capital * 100),
            "worst_day": float(daily.min()), "best_day": float(daily.max()),
            "sharpe": float(daily.mean() / sd * math.sqrt(250)) if sd > 0 else 0.0,
            "annual_pct": float(daily.sum() / capital / years * 100),
            "losing_months": None}


def report(wf: dict, cfg: dict, tables: dict) -> dict:
    daily, oos, trades = wf["daily"], wf["oos"], wf["trades"]
    cap = cfg["capital"]
    s = summary(daily, cap)
    monthly = []
    if not daily.empty:
        dm = daily.groupby(pd.to_datetime(pd.Series(daily.index)).dt.to_period("M").values).agg(["sum", "count"])
        for per, r in dm.iterrows():
            d = daily[[pd.Timestamp(x).to_period("M") == per for x in daily.index]]
            t = d[d != 0]
            monthly.append({"month": str(per), "pnl": float(r["sum"]), "days": int(len(t)),
                            "green": int((t > 0).sum())})
        s["losing_months"] = sum(1 for m in monthly if m["pnl"] < 0)
        s["months"] = len(monthly)
    by = []
    if not oos.empty:
        for (sym, fam), g in oos.groupby(["sym", "family"]):
            st = _stats(g["net_rs"])
            by.append({"sym": sym, "family": fam, "name": NICE[fam], **st,
                       "avg_lots": float(g["lots"].mean()), "costs": float((g["costs_lot"] * g["lots"]).sum()),
                       "reasons": g["reason"].value_counts().to_dict()})
        by.sort(key=lambda r: -r["sum"])
    # regime: India VIX 1-year percentile at entry, and expiry days
    regimes = []
    if not oos.empty:
        o = oos.copy()
        o["vix_b"] = pd.cut(o["vix_pct"], [-1, 33, 66, 101], labels=["Low VIX", "Normal VIX", "High VIX"])
        for b, g in list(o.groupby("vix_b", observed=True)) + [("Expiry days", o[o["expiry"]]), ("Other days", o[~o["expiry"]])]:
            if len(g):
                regimes.append({"bucket": str(b), "trades": int(len(g)), "pnl": float(g["net_rs"].sum()),
                                "avg": float(g["net_rs"].mean()), "win": float((g["net_rs"] > 0).mean() * 100)})
    # honest check: how much better did the best variants look in hindsight than out of sample?
    insample = []
    test_months = set(oos["month"].astype(str)) if not oos.empty else set()
    tt = trades[trades["month"].astype(str).isin(test_months)]
    for (sym, fam), g in tt.groupby(["sym", "family"]):
        best = g.groupby("pid")["net_rs"].sum().max()
        got = oos[(oos["sym"] == sym) & (oos["family"] == fam)]["net_rs"].sum() if not oos.empty else 0
        insample.append({"sym": sym, "family": fam, "hindsight_best": float(best), "walk_forward": float(got)})
    hist = []
    traded = daily[daily != 0]
    if len(traded):
        lo, hi = float(traded.quantile(0.01)), float(traded.quantile(0.99))
        bins = np.linspace(lo, hi, 25)
        cnt, edges = np.histogram(traded.clip(lo, hi), bins=bins)
        hist = [{"from": float(a), "to": float(b), "n": int(c)} for a, b, c in zip(edges[:-1], edges[1:], cnt)]
    return {"summary": s, "monthly": monthly, "equity": [{"date": str(d), "eq": float(v)} for d, v in daily.cumsum().items()],
            "daily": [{"date": str(d), "pnl": float(v)} for d, v in daily.items()], "hist": hist, "by_strategy": by,
            "regimes": regimes, "insample": insample, "choices": wf["choices"][-80:]}


def make_plan(trades: pd.DataFrame, cfg: dict) -> list:
    """Today's plan: choose each strategy's variant on the most recent training window."""
    if trades.empty:
        return []
    months = sorted(trades["month"].unique())
    train = trades[trades["month"].isin(months[-cfg["train_months"]:])]
    plan = []
    for (sym, fam), g in train.groupby(["sym", "family"]):
        pid, s = choose(g, fam, cfg)
        if pid:
            p = dict(next(pp for i, pp in grid(fam) if i == pid))
            sel = g[g["pid"] == pid]
            plan.append({"sym": sym, "family": fam, "name": NICE[fam], "pid": pid, "params": p,
                         "describe": describe(fam, p), "selling": fam in SELLING, "train": s,
                         "lots_typ": int(sel["lots"].median()) if len(sel) else 0,
                         "max_loss_typ": float((sel["max_loss_rs"] * sel["lots"]).median()) if len(sel) else 0,
                         "margin_typ": float((sel["margin_rs"] * sel["lots"]).median()) if len(sel) else 0,
                         "active": True})
        else:
            plan.append({"sym": sym, "family": fam, "name": NICE[fam], "pid": None, "active": False,
                         "why": "No variant passed the bar in the last 6 months (enough trades, profit factor 1.15+, "
                                "and profit clearly above luck)."})
    return sorted(plan, key=lambda x: (not x["active"], x["sym"], x["family"]))


CAVEATS = [
    "A variant is only traded if its last-6-month profit is about 2 standard errors above zero, with enough trades "
    "and a profit factor of 1.15+. If nothing passes, the desk stays in cash: no trade is better than a bad trade.",
    "Only out-of-sample months are counted: each month's variants were chosen using the 6 months before it.",
    "Fills use 1-minute closes plus slippage per leg; real fills in fast markets can be worse.",
    "Charges include brokerage, STT 0.15% on option sales, exchange, SEBI, GST and stamp duty.",
    "Lot sizes are today's (Nifty 65, Bank Nifty 30). Margins are estimates; Upstox shows real margin when live.",
    "Bank Nifty uses monthly-expiry options throughout; Nifty uses the nearest weekly expiry.",
    "Past results are not a promise. Paper trade the plan for at least a month before using real money.",
]


def run_all(data_dir, user_cfg: dict | None = None, log=print, should_stop=lambda: False) -> dict:
    cfg = {**DEFAULTS, **(user_cfg or {})}
    cfg["min_trades"] = {**DEFAULTS["min_trades"], **(cfg.get("min_trades") or {})}
    syms = [s for s in UNDERLYINGS if any((hist_dir(data_dir) / s).glob("raw_*.parquet"))]
    if not syms:
        raise RuntimeError("No history yet. Download data first.")
    log("Step 1/5: preparing")
    trades = run_grid(data_dir, syms, log, should_stop)
    if should_stop():
        raise RuntimeError("Stopped")
    if trades.empty:
        raise RuntimeError("No trades could be simulated. Check the downloaded data.")
    log("Step 4/5: walk-forward selection")
    all_days = sorted(set(trades["date"]))
    wf = walk_forward(trades, cfg, all_days)
    log("Step 5/5: report")
    res = report(wf, cfg, {})
    res["plan"] = make_plan(wf["trades"], cfg)
    res.update(generated=dt.datetime.now().isoformat(timespec="seconds"), config=cfg, syms=syms,
               period={"from": str(min(all_days)), "to": str(max(all_days)),
                       "test_from": str(wf["daily"].index[0]) if len(wf["daily"]) else None},
               caveats=CAVEATS, variants=sum(len(list(grid(f))) for f in FAMILIES))
    d = lab_dir(data_dir)
    (d / "results.json").write_text(json.dumps(res, default=str))
    (d / "plan.json").write_text(json.dumps({"generated": res["generated"], "plan": res["plan"], "config": cfg}, default=str))
    wf["trades"].drop(columns=["month"]).to_parquet(d / "trades.parquet", index=False)
    try:
        from .edge import study
        from .meta import run as meta_run
        log("Extra: testing every chart-setup signal on the same history (option buy, costs included)")
        sig = study(data_dir, syms, log=log, should_stop=should_stop)
        if len(sig):
            sig.to_parquet(d / "signals.parquet", index=False)
            meta_run(data_dir, sig, log=log)
    except Exception as e:
        log(f"Setup study skipped: {e}")
    s = res["summary"]
    if s:
        log(f"Out-of-sample: avg ₹{s['avg_day']:,.0f}/day, {s['green_pct']:.0f}% green trading days, "
            f"max drawdown ₹{s['max_dd']:,.0f}, {s['trade_days']} trading days")
    return res
