"""2.82: the ML strategies' gradient-boosted trees failed every night ("ML hgb / std: window shape cannot be larger than
input array shape", 13 nights x 4 exits): VIX is in the 1-year Lab bar table only from 5 Aug 2026, so it is empty in the
TRAIN part, and scikit-learn 1.9 refuses an all-missing column. KeepSeen drops such columns; the trees are the same as
trees trained without them."""
import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

from lab import factory as F
from lab import mlstrat
from lab.mlsafe import KeepSeen
from tests.test_exits import _book


def _data(n=20000, seed=3):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 5))
    X[:, 1] = np.nan                                   # empty in training (like VIX before Aug 2026)
    X[rng.random(n) < 0.3, 3] = np.nan                 # partly missing: kept, missing values go to the trees as before
    y = (X[:, 0] + 0.5 * rng.normal(size=n) > 0).astype(int)
    return X, y


def test_keepseen_drops_only_empty_columns_and_the_trees_are_the_same():
    X, y = _data()
    m = mlstrat._model("hgb").fit(X, y)
    assert m[0].dropped_ == [1] and m[0].keep_.tolist() == [True, False, True, True, True]
    ref = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=200,
                                         l2_regularization=1.0, random_state=0).fit(np.delete(X, 1, 1), y)
    Xt, _ = _data(3000, seed=9)
    Xt[:50, 1] = 2.0                                   # live values in the dropped column do not change the score
    assert np.array_equal(m.predict_proba(Xt), ref.predict_proba(np.delete(Xt, 1, 1)))


def test_keepseen_changes_nothing_when_no_column_is_empty():
    X, y = _data()
    X[:, 1] = 0.5
    k = KeepSeen().fit(X)
    assert k.dropped_ == [] and np.array_equal(k.transform(X), X, equal_nan=True)


def test_the_ml_run_tests_all_three_models_when_vix_is_empty_in_train(tmp_path):
    b = _book(200)
    b.cols["vix"] = np.where(b.part == 0, np.nan, b.cols["vix"])     # VIX only in validation / holdout
    logs = []
    rep = mlstrat.run(tmp_path, book=b, exits=["std"], log=logs.append)
    assert not [m for m in logs if "window shape" in m or m.startswith("  ML hgb /")]
    assert rep["tested"] == 3 * len(mlstrat.QUANTS) * 2
    assert {r["kind"] for r in rep["best"]} <= set(mlstrat.KINDS) and any(m.startswith("  ML gradient-boosted trees") for m in logs)


def test_a_saved_trees_model_loads_and_scores_live(tmp_path):
    X, y = _data(6000)
    m = mlstrat._model("hgb").fit(np.column_stack([X[:, :1].repeat(len(F.ML_FEATURES), 1), np.sign(X[:, 2])]), y)
    (tmp_path / "lab" / "ml").mkdir(parents=True)
    joblib.dump(m, tmp_path / "lab" / "ml" / "t.joblib")
    feats = {k: [0.0] for k in F.ML_FEATURES}
    feats["tod"] = [60.0]
    rule = {"ml": "t.joblib", "dir": 1, "thr": -1.0, "win": [F.FIRST_ENTRY_TOD, F.LAST_ENTRY_TOD]}
    assert mlstrat.fires(tmp_path, rule, feats) is True
