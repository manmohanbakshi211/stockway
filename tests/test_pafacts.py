"""2.68 price-action facts and level stops (gun pieces for the Ghanshyam option-buying course, lab/pafacts.py):
fall / rise from the open, a confirmed hammer / shooting star, the morning move, where the 2:20 candle sits, how much of
today's fall / rise is taken back, and the pm / fib38 / sig stops. Nothing looks ahead; a failure leaves only these
facts empty; the course's strategy file reads cleanly."""
import json
from pathlib import Path

import numpy as np
import pytest

from lab import exits as X, factory as F, packs as P, pafacts as PA

ROOT = Path(__file__).resolve().parents[1]


def _day(n=75, seed=1):
    rng = np.random.default_rng(seed)
    c = 50000 * np.exp(np.cumsum(rng.normal(0, 0.0012, n)))
    o = np.r_[50000, c[:-1]]
    h = np.maximum(o, c) * (1 + rng.uniform(0, 0.0008, n))
    l = np.minimum(o, c) * (1 - rng.uniform(0, 0.0008, n))
    return o, h, l, c


def test_every_fact_is_listed_and_known_to_the_lab():
    for k in PA.FACTS:
        assert k in F.FEATURES
    for k in PA.LEVELS:
        assert k in X.STOP_KINDS and k in X.LEVEL_STOPS


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_no_fact_or_level_looks_ahead(seed):
    o, h, l, c = _day(seed=seed)
    full, lv_full = PA.facts(o, h, l, c), PA.levels(o, h, l, c)
    for k in (10, 45, 61, 70):
        part, lv_part = PA.facts(o[:k], h[:k], l[:k], c[:k]), PA.levels(o[:k], h[:k], l[:k], c[:k])
        for name in PA.FACTS:
            np.testing.assert_array_equal(part[name], full[name][:k], err_msg=name)
        for name, (a, b) in lv_part.items():
            np.testing.assert_array_equal(a, lv_full[name][0][:k])
            np.testing.assert_array_equal(b, lv_full[name][1][:k])


def test_open_fall_rise_and_a_confirmed_hammer():
    o = np.array([100, 99.9, 99.7, 99.47, 99.45, 99.6, 99.7])
    h = np.array([100.05, 99.95, 99.75, 99.52, 99.62, 99.75, 99.8])
    l = np.array([99.85, 99.65, 99.45, 99.30, 99.40, 99.55, 99.65])
    c = np.array([99.9, 99.7, 99.5, 99.50, 99.6, 99.72, 99.78])
    f = PA.facts(o, h, l, c)
    assert f["open_fall"][3] == pytest.approx(0.7)
    assert f["open_rise"][0] == pytest.approx(0.05)
    assert f["hs_conf"].tolist() == [0, 0, 0, 0, 1, 0, 0]       # bar 3 is the hammer, bar 4 closes above its high
    sig_lo, sig_hi = PA.levels(o, h, l, c)["sig"]
    assert np.isnan(sig_lo[3]) and sig_lo[4] == pytest.approx(99.30) and np.isnan(sig_hi).all()


def test_a_shooting_star_needs_the_close_below_its_low_within_three_bars():
    o = np.array([100, 100.1, 100.3, 100.52, 100.5, 100.5, 100.49, 100.48])
    h = np.array([100.15, 100.35, 100.55, 100.70, 100.55, 100.53, 100.52, 100.50])
    l = np.array([99.95, 100.05, 100.25, 100.48, 100.49, 100.46, 100.45, 100.30])
    c = np.array([100.1, 100.3, 100.5, 100.50, 100.5, 100.49, 100.485, 100.35])
    f = PA.facts(o, h, l, c)
    assert (f["hs_conf"] == 0).all()                             # the close below 100.48 comes 4 bars later: too late
    c2 = c.copy()
    c2[5] = 100.40
    l2 = l.copy()
    l2[5] = 100.38
    f2 = PA.facts(o, h, l2, c2)
    assert f2["hs_conf"].tolist() == [0, 0, 0, 0, 0, -1, 0, 0]
    assert PA.levels(o, h, l2, c2)["sig"][1][5] == pytest.approx(100.70)


def test_morning_move_and_where_the_2_20_candle_sits():
    o, h, l, c = _day()
    f = PA.facts(o, h, l, c)
    assert np.isnan(f["am_move"][43]) and f["am_move"][44] == pytest.approx((c[44] / o[0] - 1) * 100)
    assert np.isnan(f["pm_rng"][59])
    ah, al = h[45:60].max(), l[45:60].min()
    pos = ((h[60] + l[60]) / 2 - al) / (ah - al)
    assert f["pm_rng"][60] == pytest.approx(pos) and f["pm_rng"][74] == pytest.approx(pos)
    assert f["pm_edge"][60] == pytest.approx(abs(pos - 0.5) * 2)
    lo, hi = PA.levels(o, h, l, c)["pm"]
    assert np.isnan(lo[59]) and lo[61] == l[60] and hi[61] == h[60]


def test_fibonacci_retracement_of_the_fall_and_its_038_stop():
    h = np.array([101.0, 100.6, 100.1, 99.6, 99.9, 100.5])
    l = np.array([100.4, 99.9, 99.4, 99.0, 99.3, 99.8])
    c = np.array([100.6, 100.0, 99.5, 99.2, 99.8, 100.4])
    o = np.r_[100.5, c[:-1]]
    f = PA.facts(o, h, l, c)
    # bar 5: high 101 (bar 0), low after it 99.0 (bar 3): (100.4 - 99) / 2 = 0.70
    assert f["fib_dday"][5] == pytest.approx(0.70)
    assert f["fall_day"][5] == pytest.approx(2 / 101 * 100)
    assert PA.levels(o, h, l, c)["fib38"][0][5] == pytest.approx(99.0 + 0.38 * 2)


def test_risk_from_the_new_level_stops():
    sp = {"stop": ["pm"], "momentum": "close", "max": 35}
    chk, why = X.spec_check(sp)
    assert why is None and "14:15-14:20" in X.spec_words(chk)
    key = X.spec_key(chk)
    X.PACK_STYLES[key] = {**chk, "words": X.spec_words(chk)}
    try:
        assert X.risk_pts(key, 100.0, 1, 1.0, lv={"pm": (99.5, 100.2)}) == pytest.approx(0.5)
        assert X.risk_pts(key, 100.0, -1, 1.0, lv={"pm": (99.5, 100.2)}) == pytest.approx(0.2)
        assert np.isnan(X.risk_pts(key, 100.0, 1, 1.0, lv={"pm": (np.nan, np.nan)}))
        assert np.isnan(X.risk_pts(key, 100.0, 1, 1.0, lv={"pm": (100.1, 100.4)}))     # stop on the wrong side: no trade
    finally:
        X.PACK_STYLES.pop(key, None)
    for kind in ("fib38", "sig"):
        assert X.spec_check({"stop": [kind]})[1] is None


def test_a_failing_fact_leaves_only_its_own_columns_empty(monkeypatch):
    o, h, l, c = _day()
    ctx = {"prev_close": 50000, "prev_high": 50300, "prev_low": 49800, "gap": 0.0}
    good = F.bar_features(o, h, l, c, ctx)
    monkeypatch.setattr(PA, "facts", lambda *a: 1 / 0)
    bad = F.bar_features(o, h, l, c, ctx)
    for k in PA.FACTS:
        assert np.isnan(bad[k]).all()
    for k in ("rsi", "pm_pos", "fc_pos", "smc_trend"):
        np.testing.assert_array_equal(np.nan_to_num(bad[k], nan=-9), np.nan_to_num(good[k], nan=-9))


def test_the_course_strategy_file_reads_cleanly(monkeypatch):
    monkeypatch.setattr(P, "each_symbol_on", lambda: False)
    raw = (ROOT / "rules" / "strategy_files" / "ghanshyam_course.json").read_bytes()
    res = P.check(raw)
    assert res["fatal"] is None and res["problems"] == []
    assert all(s["status"] == "ok" for s in res["strategies"]) and len(res["strategies"]) == 25    # v2 (2.69): 9:20 merged
    assert len(res["rules"]) >= 80
    stops = {sp["stop"][0] for sp in res["exits"].values()}
    assert {"pm", "fib38", "sig"} <= stops
    d = json.loads(raw)
    assert not any(k in json.dumps(d).lower() for k in ("place_order", "modify_order", "api_key", "access_token"))   # text, no keys
