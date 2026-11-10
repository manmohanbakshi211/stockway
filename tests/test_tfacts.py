"""2.47 gun pieces for the 51-strategy book: bigger-timeframe / multi-day facts (lab/tfacts.py), two-step entries
("buy above the high of that candle"), exits on a fact (exit_if) and on the option's own price, option momentum facts."""
import hashlib
import json
import math

import numpy as np
import pandas as pd
import pytest

from lab import exits as X, factory as F, tfacts as T


def _walk(days=40, nb=75, seed=5):
    rng = np.random.default_rng(seed)
    n = days * nb
    c = 25000 + np.cumsum(rng.normal(0, 8, n))
    o = np.r_[c[0], c[:-1]]
    h, l = np.maximum(o, c) + rng.random(n) * 5, np.minimum(o, c) - rng.random(n) * 5
    return o, h, l, c, np.repeat(np.arange(days), nb), np.tile(np.arange(nb), days)


def test_every_fact_is_registered_and_has_no_look_ahead():
    o, h, l, c, day, pos = _walk()
    full = T.compute(o, h, l, c, day, pos)
    assert set(full) == set(F.TF_FACTS) and set(F.TF_FACTS) <= set(F.FEATURES)
    for cut in (len(c) - 37, len(c) - 75 * 3 - 11, 1500):          # mid-candle cuts on every timeframe
        part = T.compute(o[:cut], h[:cut], l[:cut], c[:cut], day[:cut], pos[:cut])
        for k in full:
            assert np.array_equal(np.nan_to_num(full[k][:cut], nan=-9e9), np.nan_to_num(part[k], nan=-9e9)), k
    for k, v in full.items():                                       # every fact fires / has values somewhere
        assert (~np.isnan(v)).any(), k


def test_live_window_gives_the_lab_facts():
    o, h, l, c, day, pos = _walk()
    full = T.compute(o, h, l, c, day, pos)
    last = day.max()
    hist = [(o[day == d], h[day == d], l[day == d], c[day == d]) for d in range(last)]
    sl = slice(last * 75, last * 75 + 50)
    live = T.live(hist, o[sl], h[sl], l[sl], c[sl])
    for k, v in live.items():
        a = full[k][sl]
        assert np.array_equal(np.isnan(a), np.isnan(v)), k
        ok = ~np.isnan(a)
        assert np.allclose(a[ok], v[ok], atol=0.05), k                 # EMA-type warm-up differences only


def test_bigger_candles_are_read_only_when_closed():
    nb = 75
    c = np.r_[np.full(nb, 100.0), np.arange(nb) * 1.0 + 100]
    o, h, l = c.copy(), c + 0.5, c - 0.5
    day, pos = np.repeat([0, 1], nb), np.tile(np.arange(nb), 2)
    f = T.compute(o, h, l, c, day, pos, {"tfs": {"30m": {"candle": ["green"]}}})
    i0 = nb                                                          # day 2, first bar (09:15-09:20)
    assert f["new_30m"][i0 + 5] == 1 and f["new_30m"][i0 + 4] == 0
    # the first 30m candle of day 2 (bars 0-5) has high 105.5; bar 6 closes at 106 -> above it
    assert f["ph_brk_30m"][i0 + 6] == 1 and f["ph_brk_30m"][i0 + 5] in (0.0, 1.0)
    # during day 2's first candle the last closed candle is yesterday's last (flat): green = 0 there
    assert f["green_30m"][i0 + 2] == 0 and f["green_30m"][i0 + 5] == 1


def test_pivots_from_yesterday():
    nb = 75
    y = np.r_[np.linspace(100, 110, nb)]                             # yesterday: H 110.5, L 99.5, C 110
    t = np.r_[np.full(10, 104.0), np.linspace(104, 120, nb - 10)]
    c = np.r_[y, t]
    o, h, l = c.copy(), c + 0.5, c - 0.5
    day, pos = np.repeat([0, 1], nb), np.tile(np.arange(nb), 2)
    f = T.pivot_facts(o, h, l, c, day, pos)
    H, L, C = 110.5, 99.5, 110.0
    P = (H + L + C) / 3
    S1 = 2 * P - H
    assert np.isnan(f["piv_pos"][:nb]).all()
    i = nb + 3                                                       # 104 is between S1 (102.8) and P (106.7)
    assert abs(f["piv_pos"][i] - (-(P - 104) / (P - S1))) < 1e-9
    j = nb + 10 + int(np.argmax(t[10:] >= 2 * P - L))                 # first bar at / above R1
    assert 1 <= f["piv_pos"][j] < 1.2
    assert (f["piv_x"][nb:] == 1).sum() >= 2                         # crosses P / R1 / R2 on the way up


def _book_df():
    """One symbol, 3 days, bars every 5 minutes, both directions; facts `a` (setup) and `b` (trigger)."""
    rows = []
    for di, d in enumerate(pd.date_range("2026-01-01", periods=10).date):
        for i in range(3, 60):
            for dr in (1, -1):
                tod = (i + 1) * 5
                rows.append({"sym": "NIFTY", "date": d, "bar": i, "dir": dr, "tod": tod, "premium": 1000.0,
                             "a": 1.0 if i == 10 else 0.0, "b": 1.0 if i in (8, 12, 20) else 0.0,
                             **{"pnl_" + e: (100.0 if i == 12 else -50.0) for e in F.all_exits()}})
    return pd.DataFrame(rows)


def test_two_step_entries_setup_then_trigger(monkeypatch):
    monkeypatch.setitem(F.FEATURES, "a", ("setup", ""))
    monkeypatch.setitem(F.FEATURES, "b", ("trigger", ""))
    b = F.Book(_book_df())
    one = F.validate({"dir": 1, "conds": [["a", ">", 0.5]], "win": [15, 330], "exit": "std", "syms": ["NIFTY"]})
    two = F.validate({"dir": 1, "conds": [["a", ">", 0.5]], "win": [15, 330], "exit": "std", "syms": ["NIFTY"],
                      "then": {"conds": [["b", ">", 0.5]], "within": 3}})
    assert "then" not in one and two["then"] == {"conds": [["b", ">", 0.5]], "within": 3}
    core = {k: one[k] for k in ("dir", "conds", "win", "exit", "max_day", "syms")}
    assert one["id"] == "S" + hashlib.sha1(json.dumps(core, sort_keys=True).encode()).hexdigest()[:8]   # old ids kept
    assert two["id"] != one["id"] and "then, within 3 bars" in F.words(two)
    t1, t2 = b.trades(one), b.trades(two)
    assert set(t1["tod"]) == {55} and set(t2["tod"]) == {65}         # setup bar 10; trigger bar 12 (not 8, not 20)
    assert (t2["pnl"] == 100).all()
    short = dict(two, then={"conds": [["b", ">", 0.5]], "within": 1})
    assert len(b.trades(short)) == 0                                 # bar 12 is 2 bars after the setup
    # live: the same rule on a day's facts
    fe = {"tod": (np.arange(30) + 1) * 5.0, "a": np.where(np.arange(30) == 10, 1.0, 0.0),
          "b": np.where(np.isin(np.arange(30), [8, 12, 20]), 1.0, 0.0)}
    assert F.signal_now(two, fe, "NIFTY", 12) and not F.signal_now(two, fe, "NIFTY", 20) and not F.signal_now(two, fe, "NIFTY", 8)
    assert not F.signal_now(two, {"tod": fe["tod"]}, "NIFTY", 12)  # a fact the screen lacks: no fire, no error


def test_exit_on_a_fact_and_on_the_option_price():
    sp, why = X.spec_check({"stop": ["pct", 3], "exit_if": [["st73_5m", "<", 0]], "opt_stop_pct": 25, "opt_target_pct": 100})
    assert why is None and sp["exit_if"] == [["st73_5m", "<", 0.0]] and "option stop 25%" in sp["words"]
    assert "Supertrend 7,3" in sp["words"]
    assert X.spec_check({"stop": ["pct", 3], "exit_if": [["nope", "<", 0]]})[0] is None
    old, _ = X.spec_check({"stop": ["fc"], "momentum": "close", "max": 375})
    assert X.spec_key(old) == "x3ea4639"
    k = X.spec_key(sp)
    X.PACK_STYLES[k] = sp
    try:
        n = 40
        fx = {"tod": (np.arange(n) + 1) * 5.0, "st73_5m": np.where(np.arange(n) >= 20, -1.0, 1.0)}
        sk = X.signal_k(fx, sp["exit_if"], 5, 30, 375)
        assert sk == 5 * 20 + 5 - 31                                  # decided at bar 20's close, out the next minute
        spot = np.full(300, 100.0)
        opt = np.r_[np.full(10, 100.0), np.full(290, 74.0)]          # the option falls 26%
        assert X.first_exit(k, spot, opt, 100.0, 100.0, 1, 3.0) == (10, "option stop")
        opt2 = np.full(300, 101.0)
        assert X.first_exit(k, spot, opt2, 100.0, 100.0, 1, 3.0, sig_k=sk) == (sk, "signal exit")
        c = np.full(375, 100.0)
        win = F._sim_lv(np.r_[np.full(150, 100.0), np.full(225, 140.0)], np.full(375, 25000.0), 30, 100.0, "NIFTY", 75, 1, k,
                        10.0, 24990, 25010, i=5, fx={"tod": fx["tod"], "st73_5m": np.full(n, 1.0)})
        out = F._sim_lv(np.r_[np.full(150, 100.0), np.full(225, 140.0)], np.full(375, 25000.0), 30, 100.0, "NIFTY", 75, 1, k,
                        10.0, 24990, 25010, i=5, fx=fx)
        assert win > 0 and out < win                                   # the signal exit left before the option rose
        assert c is not None
    finally:
        X.PACK_STYLES.pop(k, None)


def test_strategy_file_with_two_step_entry_and_signal_exit():
    from lab import packs
    d = {"stockway_pack": 1, "id": "t247", "name": "test", "version": 1,
         "exits": {"st_out": {"stop": ["bar"], "exit_if": [["st73_5m", "<", 0]]}},
         "strategies": [
             {"name": "BB support then break", "dir": 1, "conds": [["new_30m", ">", 0.5], ["bbp_30m", "<", 0.15], ["green_30m", ">", 0.5]],
              "then": {"conds": [["ph_brk_30m", ">", 0.5]], "within": 6}, "exit": ["st_out", "std"], "syms": ["NIFTY"]},
             {"name": "Unknown", "dir": 1, "conds": [["tod", ">", 20]], "then": {"conds": [["zz_fact", ">", 0]], "within": 2}}]}
    r = packs.check(json.dumps(d).encode())
    assert r["fatal"] is None and len(r["rules"]) == 2 and all(x["then"]["within"] == 6 for x in r["rules"])
    assert any("zz_fact" in c["needs"] for c in r["cannot_test"])


def test_option_momentum_facts():
    class Day:
        step = 50.0
        spot = np.full(375, 25000.0)
        def atm(self, m):
            return 25000.0
        def price(self, k, side, m, kind="c"):
            return 100.0 + (m - 14) * (0.2 if side == "CE" else -0.1) if m >= 14 else 100.0
    f = F.opt_facts(Day(), 75, np.full(75, 1.0))
    assert np.isnan(f["ce_m0930"][1]) and f["ce_m0930"][2] == 0
    assert abs(f["ce_m0930"][5] - 3 * 5 * 0.2) < 1e-9 and f["pe_m0930"][5] < 0
    assert np.isnan(f["ce_m1000"][7]) and f["ce_m1000"][8] == 0 and np.allclose(f["strd_avg"], 0)
    assert set(f) == set(F.OPT_FACTS)


@pytest.mark.parametrize("name", sorted(T.IND))
def test_a_failing_indicator_breaks_only_itself(name, monkeypatch):
    o, h, l, c, day, pos = _walk(days=15)
    monkeypatch.setitem(T.IND, name, lambda *a: (_ for _ in ()).throw(RuntimeError("bad bullet")))
    f = T.compute(o, h, l, c, day, pos)
    cfg = T.config()
    mine = {f"{x}_{tf}" for tf, inds in cfg["tfs"].items() for ind, outs in inds.items() if ind == name for x in outs}
    for k, v in f.items():
        if k in mine:
            assert np.isnan(v).all(), k
    others = [k for k in f if k not in mine and not k.startswith(("new_", "ph_brk_", "piv_"))]
    assert all((~np.isnan(f[k])).any() for k in others if not any(k.startswith(p) for p in ("cloud", "tk_", "sma50_1H")))
    assert math.isfinite(len(f))


def test_live_screen_adds_the_new_facts_only_when_a_rule_uses_them():
    import datetime as dt
    import types
    from live.chartdata import ChartData
    from live.clock import IST
    o, h, l, c, day, pos = _walk(days=14)
    t0 = dt.datetime(2026, 11, 2, 9, 15, tzinfo=IST).timestamp()
    rows = []
    for d in range(13):                                             # 13 earlier days of 5-min bars (Dhan stand-in)
        ix = np.nonzero(day == d)[0]
        for j, g in enumerate(ix):
            rows.append([t0 + d * 86400 + j * 300, o[g], h[g], l[g], c[g], 0])
    logs = []
    eng = types.SimpleNamespace(log=logs.append, mtf=types.SimpleNamespace(long_rows=lambda s, b, n: (rows, "test")))
    cd = ChartData(eng)
    today = dt.datetime.fromtimestamp(t0 + 13 * 86400, IST)
    ix = np.nonzero(day == 13)[0][:40]
    bars = (o[ix], h[ix], l[ix], c[ix])
    m = {"spot": 25000.0, "opt": {"rows": [{"strike": 25000.0, "CE": {"ltp": 100.0}, "PE": {"ltp": 90.0}}]}}
    f = {"tod": (np.arange(40) + 1) * 5.0}
    cd._add_new_facts(f, "NIFTY", today, 40, m, bars, [1.0] * 40, [{"conds": [["tod", ">", 1]]}])
    assert set(F.TF_FACTS) <= set(f) and np.isnan(f["bbp_30m"]).all()            # no rule needs them: NaN, no fetch
    f2 = {"tod": f["tod"]}
    rules = [{"conds": [["tod", ">", 1]], "then": {"conds": [["bbp_30m", ">", 0]]}}, {"conds": [["ce_m0930", ">", 0]]}]
    cd._add_new_facts(f2, "NIFTY", today, 40, m, bars, [1.0] * 40, rules)
    full = T.compute(o[:13 * 75 + 40], h[:13 * 75 + 40], l[:13 * 75 + 40], c[:13 * 75 + 40], day[:13 * 75 + 40], pos[:13 * 75 + 40])
    a, b = full["bbp_30m"][-40:], f2["bbp_30m"]
    assert np.allclose(a[~np.isnan(a)], b[~np.isnan(a)]) and not logs
    assert "ce_m0930" in f2 and len(f2["ce_m0930"]) == 40


def test_sweep_of_yesterdays_low_back_inside_and_retest():
    nb = 75
    y = np.linspace(100, 110, nb)                                    # yesterday: low 99.9, high 110.1
    t = np.full(nb, 104.0)
    t[10:13] = [101.0, 100.4, 99.0]                                  # bar 12 closes below yesterday's low (swept)
    t[13:20] = [100.6, 101.2, 101.6, 100.9, 101.4, 101.8, 102.2]   # back inside; bar 16 dips to the level
    c = np.r_[y, t]
    o = np.r_[c[0], c[:-1]]
    h, l = np.maximum(o, c) + 0.1, np.minimum(o, c) - 0.1
    l[nb + 17] = 99.95                                               # bar 17 retests the low and closes green
    o[nb + 17] = 100.5
    day, pos = np.repeat([0, 1], nb), np.tile(np.arange(nb), 2)
    f = T.sweep_facts(o, h, l, c, day, pos, tol_atr=0.5)
    b = f["pd_back"][nb:]
    assert b[12] == 0 and b[13] == 1 and b[14] == 2 and b[16] == 4
    assert f["pd_rt"][nb + 17] == 1 and (f["pd_rt"][nb:] == 1).sum() == 1 and np.isnan(f["pd_back"][:nb]).all()


def test_bigger_chart_level_break_and_retest():
    nb = 75
    days = 4
    rng = np.random.default_rng(2)
    base = np.r_[np.linspace(110, 100, nb), np.linspace(100, 112, nb), np.linspace(112, 104, nb)]   # 4H swing low ~100
    d4 = np.r_[np.linspace(104, 97, 30), np.linspace(97, 99.8, 12), np.linspace(99.8, 95, 33)]      # break, retest, fall
    c = np.r_[base, d4] + rng.normal(0, 0.02, nb * days)
    o = np.r_[c[0], c[:-1]]
    h, l = np.maximum(o, c) + 0.05, np.minimum(o, c) - 0.05
    day, pos = np.repeat(np.arange(days), nb), np.tile(np.arange(nb), days)
    x = T.xretest(o, h, l, c, day, pos, 12, 6)                       # 1H levels, 30M execution (enough candles here)
    assert (x[3 * nb:] == -1).any() and not (x == 1).any()
    first = 3 * nb + int(np.argmax(x[3 * nb:] == -1))
    assert c[first] < 100 and (pos[first] + 1) % 6 == 0 or x[first - 1] != -1


def test_day_extreme_stop():
    sp, why = X.spec_check({"stop": ["dayx"], "target_r": 2})
    assert why is None and "today's low" in sp["words"]
    k = X.spec_key(sp)
    X.PACK_STYLES[k] = sp
    try:
        assert X.risk_pts(k, 100.0, 1, math.nan, lv={"dayx": (97.0, 104.0)}) == 3.0
        assert X.risk_pts(k, 100.0, -1, math.nan, lv={"dayx": (97.0, 104.0)}) == 4.0
    finally:
        X.PACK_STYLES.pop(k, None)
