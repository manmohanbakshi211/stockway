"""Meta-model: should this setup signal be taken? Learned from the edge study (lab/edge.py), tested walk-forward.

Every month is predicted by a model trained only on earlier months, so the reported numbers are what the filter
would have done live. The live app uses it only if that out-of-sample test passes; otherwise alerts say so.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd

FEATURES = ["dir", "minute", "rr", "dte", "iv", "iv_rank", "iv_vs_forecast", "exp_move_pct", "vix", "vix_chg", "gap_pct",
            "ret5d", "move_pct", "range_vs_exp", "dist_vwap_pct", "rsi", "ema_spread_pct", "st_with", "cpr_width",
            "risk_atr", "pcr_near", "oi_flow", "wall_room", "is_bn"]
SETUPS = ["ema_pullback", "vwap_reclaim", "cpr_breakout", "pdh_break", "wall_fade"]
MIN_TRAIN_MONTHS = 4
PASS = {"min_taken": 60, "min_avg_rs": 0.0, "min_t": 1.5, "min_auc": 0.53}


def matrix(df: pd.DataFrame) -> pd.DataFrame:
    X = pd.DataFrame(index=df.index)
    for f in FEATURES:
        if f == "is_bn":
            X[f] = (df["sym"] == "BANKNIFTY").astype(int) if "sym" in df else 0
        else:
            X[f] = pd.to_numeric(df.get(f), errors="coerce")
    for s in SETUPS:
        X["s_" + s] = (df["setup"] == s).astype(int)
    return X


def _model():
    from sklearn.ensemble import HistGradientBoostingClassifier
    # small and heavily regularised: a few thousand noisy trades can't support a big model
    return HistGradientBoostingClassifier(max_iter=150, learning_rate=0.04, max_depth=3, min_samples_leaf=40,
                                          l2_regularization=1.0, random_state=7)


def _auc(y, p):
    from sklearn.metrics import roc_auc_score
    try:
        return float(roc_auc_score(y, p))
    except Exception:
        return math.nan


def walk_forward(sig: pd.DataFrame, take_frac: float = 0.35, log=print) -> tuple[pd.DataFrame, dict]:
    """Predict each month with a model trained on all earlier months. Threshold = the score above which the
    top `take_frac` of training signals fall (chosen on training data only)."""
    sig = sig.sort_values(["date", "minute"]).reset_index(drop=True)
    sig["month"] = pd.to_datetime(sig["date"]).dt.to_period("M").astype(str)
    months = sorted(sig["month"].unique())
    out = []
    for i, mo in enumerate(months):
        if i < MIN_TRAIN_MONTHS:
            continue
        tr = sig[sig["month"].isin(months[:i])]
        te = sig[sig["month"] == mo]
        if tr["win"].nunique() < 2 or te.empty:
            continue
        m = _model().fit(matrix(tr), tr["win"])
        p_tr = m.predict_proba(matrix(tr))[:, 1]
        thr = float(np.quantile(p_tr, 1 - take_frac))
        p = m.predict_proba(matrix(te))[:, 1]
        out.append(te.assign(p=p, thr=thr, take=(p >= thr).astype(int)))
    if not out:
        return pd.DataFrame(), {"ok": False, "why": f"Need at least {MIN_TRAIN_MONTHS + 1} months of signals."}
    oos = pd.concat(out, ignore_index=True)
    return oos, evaluate(oos)


def _stats(p: pd.Series) -> dict:
    n = len(p)
    if n == 0:
        return {"n": 0}
    win, loss = p[p > 0].sum(), -p[p < 0].sum()
    t = p.mean() / (p.std(ddof=1) / math.sqrt(n)) if n > 2 and p.std() > 0 else 0.0
    return {"n": n, "avg_rs": round(float(p.mean()), 1), "total_rs": round(float(p.sum()), 0), "win_pct": round(float((p > 0).mean() * 100), 1),
            "pf": round(float(win / loss), 2) if loss else None, "t": round(float(t), 2)}


def evaluate(oos: pd.DataFrame) -> dict:
    taken, all_ = oos[oos["take"] == 1], oos
    auc = _auc(oos["win"], oos["p"])
    s_t, s_a = _stats(taken["pnl"]), _stats(all_["pnl"])
    ok = (s_t["n"] >= PASS["min_taken"] and s_t["avg_rs"] > PASS["min_avg_rs"] and s_t["t"] >= PASS["min_t"]
          and (auc == auc and auc >= PASS["min_auc"]))
    by_month = [{"month": mo, "taken": int(g["take"].sum()), "taken_rs": round(float(g.loc[g["take"] == 1, "pnl"].sum()), 0),
                 "all_rs": round(float(g["pnl"].sum()), 0)} for mo, g in oos.groupby("month")]
    deciles = []
    if len(oos) >= 50:
        q = pd.qcut(oos["p"].rank(method="first"), 5, labels=False)
        deciles = [{"bucket": int(b) + 1, **_stats(g["pnl"])} for b, g in oos.groupby(q)]
    why = ("Signals the model picks made money out of sample, clearly above luck." if ok else
           "The filter did not beat luck out of sample yet; alerts show it for information only.")
    return {"ok": bool(ok), "why": why, "auc": round(auc, 3) if auc == auc else None, "taken": s_t, "all": s_a,
            "months": by_month, "by_score": deciles, "pass_rule": PASS}


def train_final(sig: pd.DataFrame, take_frac: float = 0.35):
    m = _model().fit(matrix(sig), sig["win"])
    p = m.predict_proba(matrix(sig))[:, 1]
    thr = float(np.quantile(p, 1 - take_frac))
    # importance by permutation on the training data (rough, for the "what matters" table)
    try:
        from sklearn.inspection import permutation_importance
        X = matrix(sig)
        r = permutation_importance(m, X, sig["win"], n_repeats=5, random_state=7, scoring="roc_auc")
        imp = sorted(zip(X.columns, r.importances_mean), key=lambda x: -x[1])[:12]
    except Exception:
        imp = []
    return m, thr, [(k, round(float(v), 4)) for k, v in imp]


def run(data_dir, sig: pd.DataFrame, log=print) -> dict:
    """Walk-forward test, then the final model on everything. Saves data/lab/meta.joblib and meta.json."""
    import joblib
    from .edge import setup_table
    from .walk import lab_dir
    d = lab_dir(data_dir)
    oos, ev = walk_forward(sig, log=log)
    tab = setup_table(sig)
    rep = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "signals": int(len(sig)),
           "period": {"from": str(sig["date"].min()), "to": str(sig["date"].max())} if len(sig) else None,
           "setups": json.loads(tab.round(2).to_json(orient="records")) if len(tab) else [], "walk_forward": ev}
    if len(sig) >= 200 and sig["win"].nunique() == 2:
        m, thr, imp = train_final(sig)
        rep["importance"] = imp
        rep["threshold"] = thr
        joblib.dump({"model": m, "threshold": thr, "features": list(matrix(sig.head(1)).columns), "ok": ev.get("ok", False),
                     "setups": rep["setups"], "trained": rep["generated"], "n": int(len(sig))}, d / "meta.joblib")
    if len(oos):
        oos.to_parquet(d / "meta_oos.parquet", index=False)
    (d / "meta.json").write_text(json.dumps(rep, default=str))
    log(f"Signal filter: {len(sig):,} signals, out-of-sample AUC {ev.get('auc')}, "
        f"taken {ev.get('taken', {}).get('n')} avg ₹{ev.get('taken', {}).get('avg_rs')} vs all ₹{ev.get('all', {}).get('avg_rs')}: "
        f"{'PASS' if ev.get('ok') else 'not passed'}")
    return rep
