"""Conviction model (2.17): aligned features, time-ordered holdout, trust gate, 0-100 conviction."""
import json

import numpy as np

from live import learn


def _alert(d="up"):
    return {"kind": "sharp_move", "severity": 3, "direction": d, "option": {"ltp": 50, "bid": 49, "ask": 51, "cost_per_lot": 9000, "delta": 0.5}}


def test_features_are_signed_with_the_alert():
    m = {"chg_pct": 2.0, "ret_5m": 0.5, "vs_vwap_pct": 1.0, "is_index": False, "opt": {"atm_iv": 30}}
    ctx = {"mkt": {"nifty": {"chg_pct": 0.8}, "breadth": {"adv": 40, "dec": 10, "n": 50}, "global": -0.4, "news_tone": 1}}
    up = learn.features(_alert("up"), m, [], None, 60, ctx)
    dn = learn.features(_alert("down"), m, [], None, 60, ctx)
    assert up["chg_al"] == 2.0 and dn["chg_al"] == -2.0
    assert up["nifty_al"] == 0.8 and dn["nifty_al"] == -0.8
    assert abs(up["breadth_al"] - 0.6) < 1e-9 and up["global_al"] == -0.4
    assert abs(up["spread_pct"] - 4.0) < 1e-9 and up["cost_lot"] == 9000
    assert set(learn.NUM) <= set(up) | {"x"} - {"x"}


def _rows(n_days, per_day, signal=True, seed=0):
    rng = np.random.default_rng(seed)
    rows, ts = [], 0
    for d in range(n_days):
        for _ in range(per_day):
            ts += 60
            al = float(rng.normal())
            f = learn.features(_alert("up"), {"chg_pct": al, "is_index": False}, [], None, 120, {})
            f["regime"] = rng.choice(["trend_up", "range", "quiet"])
            p = 1 / (1 + np.exp(-2.5 * al)) if signal else 0.4
            rows.append({"ts": ts, "day": f"2026-10-{d + 1:02d}", "features": json.dumps(f), "o0": 50,
                         "result": "hit" if rng.random() < p else "miss", "o60": float(rng.normal())})
    return rows


def test_ranker_trusts_a_real_signal_only_with_enough_days(tmp_path):
    r = learn.Ranker(tmp_path, "t")
    info = r.train(_rows(6, 120))
    assert info["auc"] > 0.7 and info["trained"] and info["days"] == 6
    hi = r.predict(learn.features(_alert(), {"chg_pct": 2.5}, [], None, 120, {}))
    lo = r.predict(learn.features(_alert(), {"chg_pct": -2.5}, [], None, 120, {}))
    assert hi > lo and r.conviction(hi) > 80 > 20 > r.conviction(lo)
    r2 = learn.Ranker(tmp_path, "t")                       # reloads from disk
    assert r2.info["trained"] and abs(r2.predict(learn.features(_alert(), {"chg_pct": 2.5}, [], None, 120, {})) - hi) < 1e-9
    few = learn.Ranker(tmp_path, "few").train(_rows(3, 200))
    assert not few["trained"] and "days" in few["why"]


def test_ranker_does_not_trust_noise(tmp_path):
    r = learn.Ranker(tmp_path, "noise")
    info = r.train(_rows(6, 120, signal=False, seed=4))
    assert not info["trained"] and r.predict({"kind": "x"}) is None
