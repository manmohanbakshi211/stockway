"""Multiverse brain (2.63): a fast learned model on every test-bench signal, judged only on days it never saw.

Mandeep, 11 Nov 2026: "AI kaha kaha project ko better ... zada smart aur accurate bana sakta hai, kya kya cheej bhot
jaldi pakad sakta hai".

Gun: the multiverse already has, for every signal of every recorded day, the facts at that moment (the same ones the
bench records: the stock, its options, the index, breadth, sector, world, VIX, news, order book, movers, bigger
picture) and what its option really did. This learns, with gradient-boosted trees (milliseconds per signal), the net %
a signal makes under one fixed way of trading it (rules/multiverse.json brain.exit, ATM, at once), so it can rank a
live signal the moment it fires.
Honest by construction:
- walk-forward: the model for day D is trained only on days before D, then scored on D: does its top 10% beat all
  signals that day, and how well does it rank (AUC of "made money")?
- the live model (trained on every day so far) is "on trial" until the walk-forward shows its top 10% beating the
  rest on most of the unseen days; until then its number is information only, never a filter.
Bullets: rules/multiverse.json "brain" (exit, top share, trust rule, minimum rows). Market data only.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

MODEL = "multiverse_brain.joblib"


def feature_names(rows: list, min_share: float = 0.5) -> list:
    """Numeric facts present in at least min_share of the signals (the same names live and here)."""
    cnt: dict = {}
    for f in rows:
        for k, v in f.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
                cnt[k] = cnt.get(k, 0) + 1
    n = max(1, len(rows))
    return sorted(k for k, c in cnt.items() if c / n >= min_share and not k.startswith("_"))


def matrix(rows: list, names: list, kinds: list, kind_list: list, regimes: list, regime_list: list) -> np.ndarray:
    X = np.full((len(rows), len(names) + len(kind_list) + len(regime_list)), np.nan, np.float32)
    for i, f in enumerate(rows):
        for j, k in enumerate(names):
            v = f.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
                X[i, j] = float(v)
        ks = kinds[i] if isinstance(kinds[i], (list, tuple, set)) else [kinds[i]]
        for j, k in enumerate(kind_list):
            X[i, len(names) + j] = 1.0 if k in ks else 0.0
        for j, r in enumerate(regime_list):
            X[i, len(names) + len(kind_list) + j] = 1.0 if regimes[i] == r else 0.0
    return X


def _model(n_rows: int = 10_000):
    from sklearn.ensemble import HistGradientBoostingRegressor
    leaf = int(min(40, max(5, n_rows // 20)))            # small samples: smaller leaves (the trees can still split)
    return HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=leaf,
                                         l2_regularization=1.0, random_state=7)


def _auc(y_pos: np.ndarray, score: np.ndarray) -> float | None:
    pos, neg = score[y_pos], score[~y_pos]
    if not len(pos) or not len(neg):
        return None
    order = np.argsort(np.concatenate([pos, neg]))
    ranks = np.empty(len(order))
    ranks[order] = np.arange(1, len(order) + 1)
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def learn(days: list, data_dir, cfg: dict, log=print) -> dict:
    """days: [{"day", "facts": [...], "kinds": [...], "regime": [...], "y": net %, "valid": bool}] oldest first."""
    bc = cfg.get("brain") or {}
    top = float(bc.get("top_share", 0.1))
    min_rows = int(bc.get("min_rows", 400))
    allrows = [f for d in days for f, v in zip(d["facts"], d["valid"]) if v]
    if len(allrows) < min_rows or len(days) < 2:
        return {"note": f"needs {min_rows}+ signals over 2+ days (has {len(allrows)} over {len(days)})"}
    names = feature_names(allrows)
    kc: dict = {}
    for d in days:
        for ks in d["kinds"]:
            for k in ks:
                kc[k] = kc.get(k, 0) + 1
    kind_list = sorted([k for k, n in kc.items() if n >= 30], key=lambda k: -kc[k])[:40]
    regime_list = sorted({r for d in days for r in d["regime"]})
    mats = []
    for d in days:
        X = matrix(d["facts"], names, d["kinds"], kind_list, d["regime"], regime_list)
        mats.append((X[d["valid"]], d["y"][d["valid"]]))
    walk = []
    for i in range(2, len(days)):                         # days before i only
        Xtr = np.concatenate([m[0] for m in mats[:i]])
        ytr = np.concatenate([m[1] for m in mats[:i]])
        Xte, yte = mats[i]
        if len(Xtr) < min_rows or len(Xte) < int(bc.get("min_test_rows", 50)):
            continue
        m = _model(len(Xtr)).fit(Xtr, ytr)
        p = m.predict(Xte)
        k = max(1, int(len(p) * top))
        best = np.argsort(-p)[:k]
        walk.append({"day": days[i]["day"], "n": int(len(p)), "all_avg": round(float(yte.mean()), 2),
                     "top_avg": round(float(yte[best].mean()), 2), "top_n": int(k),
                     "auc": round(_auc(yte > 0, p), 3) if _auc(yte > 0, p) is not None else None})
    Xall = np.concatenate([m[0] for m in mats])
    yall = np.concatenate([m[1] for m in mats])
    model = _model(len(Xall)).fit(Xall, yall)
    pin = model.predict(Xall)
    wins = sum(1 for w in walk if w["top_avg"] > w["all_avg"])
    trusted = len(walk) >= int(bc.get("trust_days", 5)) and wins >= math.ceil(len(walk) * float(bc.get("trust_share", 0.7)))
    info = {"rows": int(len(yall)), "days": len(days), "features": len(names), "kinds": kind_list, "walk": walk,
            "walk_wins": wins, "trusted": bool(trusted), "exit": bc.get("exit"),
            "quantiles": [round(float(q), 3) for q in np.quantile(pin, np.linspace(0, 1, 21))]}
    if walk:
        tn = sum(w["top_n"] for w in walk)
        an = sum(w["n"] for w in walk)
        info["walk_top_avg"] = round(sum(w["top_avg"] * w["top_n"] for w in walk) / tn, 2) if tn else None
        info["walk_all_avg"] = round(sum(w["all_avg"] * w["n"] for w in walk) / an, 2) if an else None
    try:
        import joblib
        joblib.dump({"model": model, "names": names, "kinds": kind_list, "regimes": regime_list, "info": info},
                    Path(data_dir) / MODEL)
    except Exception as e:
        info["save_error"] = str(e)[:120]
    log(f"Multiverse brain: {info['rows']:,} signals, {len(names)} facts; walk-forward {len(walk)} unseen day(s), top 10% "
        f"beat all on {wins}; {'trusted' if trusted else 'on trial'}")
    return info


_CACHE: dict = {}


def predict(data_dir, kind, facts: dict) -> dict | None:
    """Live: the expected net % for a signal like this (the brain's exit), where it ranks among the training signals,
    and whether the brain is trusted yet. None until a model exists."""
    p = Path(data_dir) / MODEL
    try:
        mt = p.stat().st_mtime
    except OSError:
        return None
    if _CACHE.get("mt") != mt or _CACHE.get("p") != str(p):
        try:
            import joblib
            _CACHE.update(mt=mt, p=str(p), obj=joblib.load(p))
        except Exception:
            _CACHE.update(mt=mt, p=str(p), obj=None)
    o = _CACHE.get("obj")
    if not o:
        return None
    kinds = kind if isinstance(kind, (list, tuple, set)) else [kind]
    X = matrix([facts], o["names"], [kinds], o["kinds"], [str(facts.get("regime") or "na")], o["regimes"])
    v = float(o["model"].predict(X)[0])
    qs = o["info"].get("quantiles") or []
    pct = int(round(100 * np.searchsorted(qs, v) / max(1, len(qs) - 1))) if qs else None
    return {"expect": round(v, 2), "pct": min(100, pct) if pct is not None else None, "trusted": bool(o["info"].get("trusted")),
            "exit": o["info"].get("exit"), "days": o["info"].get("days")}
