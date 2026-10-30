"""Machine-learning strategies: instead of a hand-written rule, a model reads all the bar facts at once and scores
each possible option buy. Several models are trained (gradient-boosted trees, logistic regression, random forest) and
each is tested exactly like a rule: fit on TRAIN, the model / exit / score threshold chosen on VALIDATION, judged on the
HOLDOUT it never saw, with the same costs, slippage and small-account day accounting (one trade a day, none after a loss).

A model that passes is saved and can fire live like a promoted rule (paper only).
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import numpy as np

from . import factory as F

KINDS = {"hgb": "gradient-boosted trees", "logit": "logistic regression", "forest": "random forest"}
QUANTS = (0.80, 0.90, 0.95, 0.98)


def _model(kind):
    from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    if kind == "hgb":
        return HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=200,
                                              l2_regularization=1.0, random_state=0)
    if kind == "logit":
        return make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(C=0.1, max_iter=3000))
    return make_pipeline(SimpleImputer(strategy="median"),
                         RandomForestClassifier(n_estimators=200, min_samples_leaf=200, max_features="sqrt", n_jobs=-1, random_state=0))


def matrix(feats: dict, d: np.ndarray) -> np.ndarray:
    """Model input: the Strategy Lab bar facts (same code live) plus the trade direction."""
    cols = [np.asarray(feats[f], float) for f in F.FEATURES]
    return np.column_stack(cols + [np.asarray(d, float)])


def run(data_dir, book: F.Book | None = None, exits=None, log=print, should_stop=lambda: False) -> dict:
    t0 = time.time()
    if book is None:
        book = F.Book(F.load_or_build(data_dir, log=log))
    exits = exits or [e for e in ("std", "swing", "atr2r", "be3r") if e in book.pnl]
    buy = np.nonzero(book.cols["dir"] != 0)[0]
    X = matrix({f: book.cols[f][buy] for f in F.FEATURES}, book.cols["dir"][buy])
    part = book.part[buy]
    results = []
    for ex in exits:
        y_pnl = book.pnl[ex][buy]
        ok = ~np.isnan(y_pnl)
        tr = ok & (part == 0)
        y = (y_pnl > 0).astype(int)
        for kind in KINDS:
            if should_stop():
                break
            try:
                m = _model(kind).fit(X[tr], y[tr])
            except Exception as e:
                log(f"  ML {kind} / {ex}: {e}")
                continue
            score = np.full(len(book.df), np.nan)
            score[buy] = m.predict_proba(X)[:, 1]
            book.cols["ml_score"] = score
            vq = score[buy][part == 1]
            for q in QUANTS:
                thr = float(np.nanquantile(vq, q))
                s = {"id": f"ML-{kind}-{ex}-{int(q * 100)}", "dir": None, "conds": [["ml_score", ">", thr]], "win": [F.FIRST_ENTRY_TOD, F.LAST_ENTRY_TOD],
                     "exit": ex, "max_day": 1, "syms": sorted(set(book.symcode))}
                res = {}
                for d in (1, -1):                              # calls and puts are separate strategies, like rules
                    sd = {**s, "dir": d}
                    t = book.trades(sd)
                    res[d] = {name: F.stats(t[t["part"] == p], book.n_days[p]) for p, name in ((0, "train"), (1, "valid"), (2, "hold"))}
                    res[d]["acct40"] = F.equity40(book.trades(sd, part=2, max_premium=F.SMALL_LOT_MAX))
                for d in (1, -1):
                    r = res[d]
                    results.append({"id": f"{s['id']}-{'C' if d > 0 else 'P'}", "kind": kind, "model": KINDS[kind], "exit": ex,
                                    "syms": sorted({x if x in F.INDEX_SYMS else F.STOCKS for x in s["syms"]}),
                                    "dir": d, "q": q, "thr": round(thr, 4), **{k: r[k] for k in ("train", "valid", "hold", "acct40")},
                                    "_model": m})
            log(f"  ML {KINDS[kind]} with exit {ex}: tested {len(QUANTS) * 2} versions ({time.time() - t0:.0f}s)")
    book.cols.pop("ml_score", None)
    # choose on validation only; holdout is the honest test
    eligible = [r for r in results if r["valid"].get("n", 0) >= 10]
    eligible.sort(key=lambda r: -(r["valid"].get("t") or -9))
    for r in eligible:
        r["stage"] = ("promoted" if F.passes(r["valid"], F.PASS["valid"]) and F.passes(r["hold"], F.PASS["hold"])
                      else "failed holdout" if F.passes(r["valid"], F.PASS["valid"]) else "failed validation")
        th = r["hold"].get("t") or 0
        r["evidence"] = "strong" if th >= 2 else "moderate" if th >= 1 else "weak"
    best = eligible[:12]
    out_dir = Path(data_dir) / "lab" / "ml"
    out_dir.mkdir(parents=True, exist_ok=True)
    import joblib
    saved = []
    for r in best:
        if r["stage"] == "promoted":
            f = out_dir / f"{r['id']}.joblib"
            joblib.dump(r["_model"], f)
            r["file"] = f.name
            saved.append(r["id"])
    rep = {"at": dt.datetime.now().isoformat(timespec="seconds"), "tested": len(results), "secs": round(time.time() - t0),
           "best": [{k: v for k, v in r.items() if k != "_model"} for r in best], "promoted": saved,
           "note": (f"{len(results)} model strategies tested (3 models × {len(exits)} exits × {len(QUANTS)} score cut-offs × calls/puts). "
                    "Chosen on validation; holdout is the honest test. Selecting the best of many still flatters the winner a little.")}
    (Path(data_dir) / "lab" / "ml.json").write_text(json.dumps(rep, default=str))
    log(f"ML strategies: {len(results)} tested, {len(saved)} passed validation and holdout ({rep['secs']}s)")
    return rep


def load(data_dir) -> dict:
    f = Path(data_dir) / "lab" / "ml.json"
    try:
        return json.loads(f.read_text()) if f.exists() else {}
    except Exception:
        return {}


_CACHE: dict = {}


def live_rules(data_dir) -> list:
    """Promoted ML strategies with moderate/strong holdout evidence, as rules the live scan can fire (paper only)."""
    out = []
    for r in load(data_dir).get("best", []):
        if r.get("stage") == "promoted" and r.get("evidence") in ("moderate", "strong") and r.get("file"):
            out.append({"id": r["id"], "ml": r["file"], "dir": r["dir"], "thr": r["thr"], "exit": r["exit"], "max_day": 1,
                        "syms": r.get("syms") or ["BANKNIFTY", "NIFTY"],
                        "win": [F.FIRST_ENTRY_TOD, F.LAST_ENTRY_TOD], "hold": r["hold"],
                        "rule": f"{r['model']} score above {r['thr']:.3f} ({'call' if r['dir'] > 0 else 'put'}), exit: {F.EXIT_WORDS.get(r['exit'], r['exit'])}"})
    return out


def fires(data_dir, rule: dict, feats: dict, i: int | None = None) -> bool:
    i = len(feats["tod"]) - 1 if i is None else i
    if not (rule["win"][0] <= feats["tod"][i] <= rule["win"][1]):
        return False
    f = Path(data_dir) / "lab" / "ml" / rule["ml"]
    if f not in _CACHE:
        import joblib
        _CACHE[f] = joblib.load(f)
    x = matrix({k: [feats[k][i]] for k in F.FEATURES}, [rule["dir"]])
    return float(_CACHE[f].predict_proba(x)[0, 1]) > rule["thr"]
