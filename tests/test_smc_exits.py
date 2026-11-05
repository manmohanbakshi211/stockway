"""2.44 gun pieces: the smart-money structure engine (no look-ahead), EMA 8/20 cross facts, yesterday's 50% level,
consolidation-breakout ages, and the new exit parts (opening-range and swing stops, points targets, option selling)."""
import math

import numpy as np
import pytest

from lab import exits as X, factory as F
from live import smc


def _bars(seed=3, n=75):
    rng = np.random.default_rng(seed)
    c = 100 + np.cumsum(rng.normal(0, 0.35, n))
    o = np.r_[100.0, c[:-1]]
    h = np.maximum(o, c) + rng.random(n) * 0.2
    l = np.minimum(o, c) - rng.random(n) * 0.2
    return o, h, l, c


def test_smc_has_no_look_ahead():
    o, h, l, c = _bars()
    full = smc.structure(o, h, l, c, 101.0, 99.0)
    for cut in (20, 40, 60):
        part = smc.structure(o[:cut], h[:cut], l[:cut], c[:cut], 101.0, 99.0)
        for k in smc.FACTS:
            a, b = full[k][:cut], part[k]
            assert np.array_equal(np.nan_to_num(a, nan=-9), np.nan_to_num(b, nan=-9)), k
    lo, hi = smc.last_swings(h, l)
    lo2, hi2 = smc.last_swings(h[:30], l[:30])
    assert np.array_equal(np.nan_to_num(lo[:30], nan=-9), np.nan_to_num(lo2, nan=-9))


def test_smc_break_change_of_character_and_order_block():
    # down leg with a swing low, a bounce making a swing high, then a strong break above it
    c = np.array([100, 99, 98, 97, 96, 95, 96, 97, 98, 97, 96, 95.5, 96, 97, 99, 101, 102, 101.5, 101, 101.2, 102.5, 103.0])
    o = np.r_[100.2, c[:-1]]
    h, l = np.maximum(o, c) + 0.1, np.minimum(o, c) - 0.1
    f = smc.structure(o, h, l, c)
    up = np.nonzero(f["smc_brk"] > 0)[0]
    assert len(up) and c[up[0]] > 98.1                     # the close above the bounce high (98) is the break
    assert f["smc_trend"][-1] >= 0 and set(np.unique(f["smc_choch"])) <= {-1.0, 0.0, 1.0}
    pd_ = f["smc_pd"][~np.isnan(f["smc_pd"])]
    assert len(pd_) and pd_[-1] > 0.5                       # at the end price is in the premium half


def test_ema_cross_pd_pos_and_breakout_age():
    c = np.r_[np.linspace(100, 90, 30), np.linspace(90, 110, 30)]
    f = F.ema_cross_facts(c)
    i = int(np.nonzero(f["ema8_20_x"])[0][0])
    assert f["ema8_20_x"][i] == 1 and np.isnan(f["ema8_20_age"][i - 1]) and list(f["ema8_20_age"][i:i + 3]) == [1, 2, 3]
    assert f["ema8_20s_x"].max() == 1                        # the smoothed lines cross too, later
    assert int(np.nonzero(f["ema8_20s_x"])[0][0]) > i
    up, dn = F.break_ages(np.array([0, 0, 1, 0, 0, -1, 0]), 7)
    assert np.isnan(up[1]) and list(up[2:]) == [0, 1, 2, 3, 4] and list(dn[5:]) == [0, 1]
    o = h = l = cc = np.array([100.0, 101.0, 102.0])
    bf = F.bar_features(o, h + 0.5, l - 0.5, cc, {"prev_high": 104.0, "prev_low": 100.0, "prev_close": 101.0}, None, None)
    assert abs(bf["pd_pos"][1] - 0.25) < 1e-9 and all(k in bf for k in smc.FACTS) and "ema8_20" in bf


def test_new_exit_parts_and_old_keys_unchanged():
    old, _ = X.spec_check({"stop": ["fc"], "momentum": "close", "max": 375})
    assert X.spec_key(old) == "x3ea4639"                      # the 2.43 file exit keeps its key (saved results stay)
    sp, why = X.spec_check({"stop": ["or30"], "opt_target_pts": 30, "sell": True})
    assert why is None and sp["sell"] and "SELL the opposite" in sp["words"] and "30 points on the option" in sp["words"]
    assert X.spec_check({"stop": ["or30"], "sell": "yes"})[0] is None
    assert X.spec_check({"stop": ["swing"], "target_pts": 50})[0]["target_pts"] == 50
    k = X.spec_key(sp)
    X.PACK_STYLES[k] = sp
    try:
        assert X.risk_pts(k, 100.0, 1, math.nan, lv={"or30": (98.0, 103.0)}) == 2.0          # bullish view: below the low
        assert math.isnan(X.risk_pts(k, 100.0, 1, math.nan, lv={"or30": (None, None)}))     # range not complete yet
        spot = np.full(30, 100.0)
        opt = np.r_[np.full(10, 80.0), np.full(20, 49.0)]        # the sold put decays 80 -> 49 (31 points)
        assert X.first_exit(k, spot, opt, 100.0, 80.0, 1, 2.0) == (10, "target")
        opt2 = np.r_[np.full(5, 80.0), np.full(25, 113.0)]       # it jumps 40%+ against the seller
        assert X.first_exit(k, spot, opt2, 100.0, 80.0, 1, 2.0) == (5, "premium stop")
        # the Lab sim: selling the opposite option that decays makes money; one that rises loses
        c = np.full(375, 100.0)
        sp_ = np.full(375, 50000.0)
        opp_down = np.r_[np.full(50, 200.0), np.full(325, 160.0)]
        opp_up = np.r_[np.full(50, 200.0), np.full(325, 300.0)]
        win = F._sim_lv(c, sp_, 40, 100.0, "BANKNIFTY", 30, 1, k, 50.0, 49900, 50100, lv={"or30": (49800.0, 50200.0)}, opp=opp_down)
        loss = F._sim_lv(c, sp_, 40, 100.0, "BANKNIFTY", 30, 1, k, 50.0, 49900, 50100, lv={"or30": (49800.0, 50200.0)}, opp=opp_up)
        assert win > 0 > loss
        r = {"dir": 1, "conds": [["or30_pos", ">", 0.5]], "win": [35, 330], "exit": k, "max_day": 1, "syms": ["BANKNIFTY"],
             "source": "pack:x:y"}
        assert "Sell the ATM put" in F.words(F.validate(r))
    finally:
        X.PACK_STYLES.pop(k, None)


@pytest.mark.parametrize("name", list(X.STYLES))
def test_built_in_styles_still_the_same(name):
    assert X.spec_of(name) is X.STYLES[name] and not X.STYLES[name].get("sell")
