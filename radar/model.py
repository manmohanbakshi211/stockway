"""Walk-forward gradient boosting (scikit-learn, installs cleanly on Mac).

Two targets:
  big_move : will T's high-low range be in the top 20% of the past year?  (main signal)
  up_day   : will T close above T-1?  (direction lean - expect this to be weak)
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import roc_auc_score

TARGETS = ("big_move", "up_day")
MODEL_FILE = "radar_models.joblib"


def _fit(X: pd.DataFrame, y: pd.Series, params: dict) -> HistGradientBoostingClassifier:
    m = HistGradientBoostingClassifier(**params)
    m.fit(X.to_numpy(dtype=float), y.to_numpy())
    return m


def _proba(m, X: pd.DataFrame) -> np.ndarray:
    return m.predict_proba(X.to_numpy(dtype=float))[:, 1]


def _auc(y, p) -> float:
    y = np.asarray(y)
    return float(roc_auc_score(y, p)) if len(np.unique(y)) > 1 else float("nan")


def walk_forward(X: pd.DataFrame, y: pd.DataFrame, cfg: dict, log=print):
    """Returns (out-of-sample predictions, out-of-sample permutation importance for big_move)."""
    mcfg = cfg["model"]
    years = sorted(set(X.index.year))
    first = max(mcfg["first_test_year"], years[0] + mcfg["min_train_years"])
    oos, imps = [], []
    for target in TARGETS:
        mask_all = y[target].notna()
        for yr in [yr for yr in years if yr >= first]:
            tr = mask_all & (X.index.year < yr)
            te = mask_all & (X.index.year == yr)
            if tr.sum() < 250 or te.sum() < 20:
                continue
            m = _fit(X[tr], y.loc[tr, target], mcfg["params"])
            thr = float(np.quantile(_proba(m, X[tr]), 1 - mcfg["flag_rate"]))
            p = _proba(m, X[te])
            oos.append(pd.DataFrame({
                "date": X.index[te], "target": target, "prob": p,
                "flag": (p >= thr).astype(int), "threshold": thr,
                "actual": y.loc[te, target].to_numpy(), "test_year": yr,
            }))
            if target == "big_move" and y.loc[te, target].nunique() > 1:
                pi = permutation_importance(m, X[te].to_numpy(dtype=float), y.loc[te, target].to_numpy(),
                                            scoring="roc_auc", n_repeats=3, random_state=0)
                imps.append(pd.Series(pi.importances_mean, index=X.columns))
            log(f"  {target:<9} {yr}: train {tr.sum():>4}  test {te.sum():>3}  AUC {_auc(y.loc[te, target], p):.3f}")
    importance = pd.concat(imps, axis=1).mean(axis=1).sort_values(ascending=False) if imps else pd.Series(dtype=float)
    return pd.concat(oos, ignore_index=True), importance


def metrics(oos: pd.DataFrame) -> dict:
    out = {}
    for target, g in oos.groupby("target"):
        flagged = g[g["flag"] == 1]
        base = float(g["actual"].mean())
        prec = float(flagged["actual"].mean()) if len(flagged) else float("nan")
        hits = g[g["actual"] == 1]
        out[target] = {
            "days_tested": int(len(g)),
            "auc": _auc(g["actual"], g["prob"]),
            "base_rate": base,
            "flag_rate": float(g["flag"].mean()),
            "precision_when_flagged": prec,
            "lift_vs_random": prec / base if base else float("nan"),
            "recall_of_actual_days": float(hits["flag"].mean()) if len(hits) else float("nan"),
            "by_year_auc": {int(yr): _auc(gg["actual"], gg["prob"]) for yr, gg in g.groupby("test_year")},
        }
    return out


def train_final(X: pd.DataFrame, y: pd.DataFrame, cfg: dict, extra: dict | None = None) -> dict:
    """Train on all labelled history, save models + thresholds + reference values."""
    bundle = {"features": list(X.columns), "models": {}, "thresholds": {},
              "medians": X.median(numeric_only=True).reindex(X.columns).to_dict(),
              "trained_at": dt.datetime.now().isoformat(timespec="seconds"),
              "data_end": str(X.index.max().date())}
    bundle.update(extra or {})
    for target in TARGETS:
        mask = y[target].notna()
        m = _fit(X[mask], y.loc[mask, target], cfg["model"]["params"])
        bundle["models"][target] = m
        bundle["thresholds"][target] = float(np.quantile(_proba(m, X[mask]), 1 - cfg["model"]["flag_rate"]))
    joblib.dump(bundle, Path(cfg["model_dir"]) / MODEL_FILE)
    return bundle


def load_bundle(cfg: dict) -> dict | None:
    f = Path(cfg["model_dir"]) / MODEL_FILE
    return joblib.load(f) if f.exists() else None


def predict(bundle: dict, X: pd.DataFrame, target: str) -> np.ndarray:
    return _proba(bundle["models"][target], X.reindex(columns=bundle["features"]))


def drivers(bundle: dict, X: pd.DataFrame, target: str = "big_move", top: int = 6):
    """Per-row drivers: how much the probability changes when each factor is reset to its
    typical (median) value. Positive = this factor pushed the probability UP.
    Returns list of [(feature, value, effect)] per row."""
    feats = bundle["features"]
    Xr = X.reindex(columns=feats).to_numpy(dtype=float)
    med = np.array([bundle["medians"].get(f, np.nan) for f in feats], dtype=float)
    model = bundle["models"][target]
    base = model.predict_proba(Xr)[:, 1]
    n, k = Xr.shape
    variants = np.repeat(Xr, k, axis=0)
    cols = np.tile(np.arange(k), n)
    variants[np.arange(n * k), cols] = med[cols]
    p_var = model.predict_proba(variants)[:, 1].reshape(n, k)
    effect = base[:, None] - p_var
    out = []
    for i in range(n):
        order = np.argsort(-np.abs(effect[i]))[:top]
        out.append([(feats[j], float(Xr[i, j]) if not np.isnan(Xr[i, j]) else float("nan"), float(effect[i, j]))
                    for j in order if abs(effect[i, j]) > 1e-4])
    return out
