"""Risk gate and Time Machine."""
import datetime as dt

import numpy as np

from live import riskgate
from live.paper import DEFAULTS
from live.timemachine import BARS, run


def _now(h, m):
    return dt.datetime(2026, 10, 30, h, m)


def test_gate_pass_sizes_by_max_loss():
    opt = {"ltp": 100, "ask": 100.5, "bid": 100, "lot": 75}
    g = {"blocked": False, "loss_limit": -6000, "trades_today": 0}
    r = riskgate.check({"sym": "NIFTY", "data": {"setup_levels": {"rr": 2}}}, opt, now=_now(10, 30), paper_cfg=DEFAULTS, guard=g, open_syms=set())
    assert r["verdict"] == "PASS" and r["lots"] == 3 and r["max_loss"] <= 5000      # 1% of 5 lakh


def test_gate_blocks_with_reasons():
    opt = {"ltp": 2.0, "ask": 2.4, "bid": 1.8, "lot": 75}
    g = {"blocked": True, "why": "Daily loss limit hit", "loss_limit": -6000, "trades_today": 9}
    r = riskgate.check({"sym": "SAIL", "data": {"setup_levels": {"rr": 1.0}}}, opt, now=_now(9, 17), paper_cfg=DEFAULTS, guard=g,
                       open_syms={"SAIL"}, ban=["SAIL"])
    text = " ".join(r["reasons"])
    assert r["verdict"] == "BLOCK" and r["lots"] == 0
    for word in ("09:20", "ban", "Already holding", "Daily loss", "Reward:risk", "spread", "expire worthless"):
        assert word in text


def test_gate_blocks_when_one_lot_is_too_much():
    opt = {"ltp": 900, "ask": 900, "bid": 899, "lot": 30}             # 900 x 30 x 20% = 5,400 > 2,000
    r = riskgate.check({"sym": "BANKNIFTY", "data": {}}, opt, now=_now(11, 0), paper_cfg=DEFAULTS,
                       guard={"blocked": False}, open_syms=set())
    assert r["verdict"] == "BLOCK" and "One lot risks" in r["reasons"][0]


def _sessions(n=60, drift=0.0, seed=1):
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        c = 100 * np.exp(np.cumsum(rng.normal(drift, 0.002, BARS)))
        o = np.r_[100, c[:-1]]
        out.append(np.stack([o, np.maximum(o, c) * 1.0005, np.minimum(o, c) * 0.9995, c], axis=1))
    return np.array(out)


def test_time_machine_is_honest_on_random_walk_and_sees_drift():
    flat = run(_sessions(), list(range(60)), entry=100, stop=99.5, target=100.5, direction="up", start_bar=10)
    m = flat["mc"]
    assert abs(m["target_first"] + m["stop_first"] + m["neither"] - 100) < 0.2
    assert abs(m["target_first"] - m["stop_first"]) < 8 and flat["verdict"]["grade"] == "flip"
    up = run(_sessions(drift=0.0008), list(range(60)), entry=100, stop=99.5, target=100.5, direction="up", start_bar=10)
    assert up["mc"]["target_first"] > up["mc"]["stop_first"] + 15 and up["verdict"]["grade"] == "edge"
    assert up["history"]["target"] > up["history"]["stop"]


def test_alert_levels_are_bounded():
    """A stop inside the next 15 minutes' noise, or a target beyond the session's usual range, gets pulled to sane values."""
    from live.record import levels

    class _B:
        bars = [(0, 100, 100.2, 99.9, 100.0)] * 15

    class _S:
        bars = {"X": _B()}

    m = {"sym": "X", "spot": 100.0, "sigma_1m": 0.05, "vwap": 100.05, "day_l": 96.0, "opt": {}}
    lv = levels(m, "down", _S())
    assert lv["stop_pct"] >= 0.29 and lv["target_pct"] <= 0.83 and lv["rr"] < 3


def test_sessions_accept_missing_last_bars():
    """Yahoo's stock feed skips 15:25; sessions that reach the 15:15 square-off still count."""
    import datetime as dt
    import pandas as pd
    from live.timemachine import BARS, sessions_from_minutes
    rows = []
    for k in range(2):
        day = dt.datetime(2026, 10, 12 + k, 9, 15, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))
        for b in range(BARS - 1 - k):          # day 1: 74 bars; day 2: 73 bars (to 15:15)
            ts = int((day + dt.timedelta(minutes=5 * b)).timestamp())
            rows.append({"ts": ts, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0 + b * 0.01})
    days, s = sessions_from_minutes(pd.DataFrame(rows))
    assert len(days) == 2 and s.shape == (2, BARS, 4) and s[0, -1, 3] == s[0, -2, 3]
