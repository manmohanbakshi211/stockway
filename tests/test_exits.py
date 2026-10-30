"""2.18: chart-level exits (backtest == live), shadow grading, paper plan exits, ₹40K finder, model strategies."""
import datetime as dt
import types

import numpy as np
import pandas as pd

from lab import exits as X
from lab import factory as F


def _paths(seed, n=200):
    rng = np.random.default_rng(seed)
    spot = 1000 + np.cumsum(rng.normal(0, 1.2, n))
    opt = np.maximum(1, 20 + 0.5 * (spot - 1000) + rng.normal(0, 0.3, n))
    return spot, opt


def test_backtest_and_live_exit_agree():
    for seed in range(40):
        spot, opt = _paths(seed)
        for st in X.STYLES:
            for d in (1, -1):
                R = X.risk_pts(st, 1000.0, d, 4.0, 997.0, 1003.0)
                k, why = X.first_exit(st, spot, opt, 1000.0, 20.0, d, R)
                lp = X.LivePlan(st, 1000.0, d, R, 20.0, 0.0)
                k2, why2 = None, "time"
                for i, (s, o) in enumerate(zip(spot, opt)):
                    w = lp.check(s, o, i * 60.0 + 0.5)       # time limit is not part of first_exit (the slice is)
                    if w and w != "time":
                        k2, why2 = i, w
                        break
                assert k == k2, (seed, st, d, k, k2)


def test_styles_and_plan_numbers():
    assert X.risk_pts("atr2r", 100, 1, 2.0) == 2.0
    assert abs(X.risk_pts("bar2r", 100, 1, 2.0, 99.0, 101) - 1.2) < 1e-9   # beyond the candle low by 0.1 ATR
    assert X.risk_pts("bar2r", 100, 1, 2.0, 90.0, 101) == 4.0           # capped at 2 ATR
    p = X.plan("atr2r", 24000, 1, 20, 150, 0.5, 65)
    assert p["und_stop"] == 23980 and p["und_target"] == 24040 and p["opt_stop"] == 140 and p["opt_target"] == 170
    assert p["max_loss"] == 650 and p["lots"] == 1
    big = X.plan("atr2r", 24000, 1, 20, 900, 0.5, 65)
    assert big["lots"] == 0                                              # ₹58K a lot: not for ₹40K


def test_be_and_chandelier():
    lp = X.LivePlan("be3r", 100, 1, 2.0, 10, 0)
    assert lp.check(102.1, 11, 60) is None and lp.stop_move() == 0.0     # +1R reached: stop moved to entry
    assert lp.check(99.9, 10, 120) == "stop"
    ch = X.LivePlan("chand", 100, -1, 3.0, 10, 0)                        # a put: price falling is good
    ch.check(90, 14, 60)
    assert ch.levels()["stop"] == 95.0 and ch.check(95.5, 13, 70) == "stop"


class _I:
    def __init__(self, ltp):
        self.ltp, self.bid, self.ask = ltp, ltp, ltp


class _State(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)


def test_shadow_grades_every_style(tmp_path):
    from live.shadow import ShadowExits, board
    from live.store import Store
    st = Store(tmp_path / "t.db")
    sh = ShadowExits(st, "sim")
    a = {"id": 1, "day": "2026-10-30", "sym": "X", "kind": "orb", "direction": "up", "und_key": "U",
         "option": {"key": "O", "ltp": 100, "ask": 100, "lot": 50}}
    sh.add(a, 1000.0, {s: 5.0 for s in X.STYLES}, (20, 15, 60), 0.0)
    s = _State(U=_I(1004), O=_I(108))
    sh.update(s, 60)
    s = _State(U=_I(1011), O=_I(130))                                    # +2.2R: 2R targets hit, current exit +30% hit
    sh.update(s, 120)
    sh.update(s, 4000, force=True)
    b = {r["style"]: r for r in board(st, "sim", days=100000)["styles"]}
    assert b["current"]["avg_pct"] == 30.0 and b["atr2r"]["avg_pct"] == 30.0 and b["atr2r"]["n40"] == 1
    assert set(b) == {"current", *X.STYLES}


def test_paper_trade_follows_the_chart_plan(tmp_path):
    from live.paper import PaperBook
    from live.store import Store
    pb = PaperBook(Store(tmp_path / "p.db"), "sim", {"capital": 40000})
    now = dt.datetime(2026, 10, 30, 10, 0)
    s = _State(U=_I(1000), O=_I(100))
    tid = pb._open(s, now, sym="X", kind="factory", opt={"key": "O", "label": "X 1000 CE", "side": "CE", "lot": 50}, entry=100, ltp=100,
                   lots=1, direction=1, manual=0, und_key="U", why="Strategy Lab S0000abcd", plan={"style": "atr2r", "R": 5.0})
    assert tid and pb.open_trades()[0]["plan"]
    pb.update(_State(U=_I(1004), O=_I(104)), now + dt.timedelta(minutes=5))
    assert pb.open_trades()
    closed = pb.update(_State(U=_I(994.9), O=_I(96)), now + dt.timedelta(minutes=10))   # chart stop at 995 (premium only −4%)
    assert closed and closed[0]["reason"] == "chart stop"


def _book(n_days=150, seed=5):
    rng = np.random.default_rng(seed)
    rows = []
    for k in range(n_days):
        day = dt.date(2025, 1, 1) + dt.timedelta(days=k)
        for bar in range(2, 30):
            base = {f: float(rng.normal()) for f in F.FEATURES}
            base["tod"] = (bar + 1) * 5.0
            for d in (1, -1):
                edge = 900 if d == 1 and base["ret_b3"] > 1.0 else 0
                prem = 8000 if rng.random() < 0.7 else 15000
                rows.append({"sym": "NIFTY", "date": day, "bar": bar, **base, "dir": d, "premium": prem,
                             **{"pnl_" + r: rng.normal(-200, 800) + edge for r in F.BUY_EXITS}})
    return F.Book(pd.DataFrame(rows))


def test_plan40_and_exit_board():
    b = _book()
    good = F.score(b, F.validate({"dir": 1, "conds": [["ret_b3", ">", 1.0]], "win": [15, 330], "exit": "std", "syms": ["NIFTY"]}))
    p = F.plan40(b, [good])
    assert p["candidates"] == 1 and p["best"][0]["acct"]["n"] > 0
    t_all, t40 = b.trades(good), b.trades(good, max_premium=10000)
    assert len(t40) < len(t_all)
    e = F.exit_board(b, [good])
    assert {x["exit"] for x in e["board"]} == set(F.BUY_EXITS) and e["best_style"] in X.STYLES


def test_ml_strategies_find_a_planted_edge(tmp_path):
    from lab import mlstrat
    rep = mlstrat.run(tmp_path, book=_book(200), exits=["std"], log=lambda m: None)
    assert rep["tested"] == 3 * len(mlstrat.QUANTS) * 2
    top = rep["best"][0]
    assert top["dir"] == 1 and top["hold"]["avg"] > 0
    assert mlstrat.load(tmp_path)["best"]
