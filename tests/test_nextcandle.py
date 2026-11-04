"""2.39: the next candle from the smaller ones (1m -> 5m, 5m -> 15m, 15m -> 1H, 1H -> 1D): facts of a candle cut at k
small candles, the most similar past moments, an honest test on unseen days against the plain 'keeps its colour'
rule, live predictions as small candles close, grading when the candle closes, and the chart / smart report data."""
import datetime as dt
import types

import numpy as np

from live import fortune as F, nextcandle as N

DAY = dt.date(2026, 11, 4)


def _minutes(days, seed=1, start=DAY - dt.timedelta(days=70), plant=False, today=False):
    """1-minute bars on weekdays. plant: a 5-minute candle whose first 3 minutes come on heavy volume reverses."""
    rng = np.random.default_rng(seed)
    rows, px, d, n = [], 1000.0, start, 0
    while n < days:
        if d.weekday() < 5 and (today or d < DAY):
            t0 = F._open_ts(d)
            for c in range(75):
                heavy = plant and rng.random() < 0.3
                drift = 0.0
                for m in range(5):
                    o = px
                    if plant and m == 3:
                        drift = -0.003 * np.sign(px - rows[-3][1]) if heavy else 0.0
                    px *= 1 + rng.normal(0, 0.0008) + drift
                    v = (900 if heavy and m < 3 else 100) * (1 + rng.random())
                    rows.append([t0 + 60 * (5 * c + m), o, max(o, px) * 1.0001, min(o, px) * 0.9999, px, v])
            n += 1
        d += dt.timedelta(days=1)
    return rows


def test_facts_after_k_small_candles():
    rows = _minutes(30)
    t = N.table(rows, "5m", "1m", [3, 4])
    m, x, y = t["meta"][0], t["X"][0], t["Y"][0]
    assert len(t["meta"]) == len(t["X"]) == len(t["Y"]) and {mm["k"] for mm in t["meta"]} == {3, 4}
    assert x.shape == (len(N.FEATURES),) and 0 <= x[N.FEATURES.index("pos")] <= 1
    g = F.groups(rows, "5m")[m["i"]][1]
    assert m["o"] == g[0][1] and m["c"] == g[m["k"] - 1][4]
    assert abs(y[0] - (g[-1][4] - m["c"]) / m["atr"]) < 1e-9 and y[3] == float(g[-1][4] > g[0][1])


def test_honest_test_finds_a_planted_edge_and_no_edge_in_noise():
    c = N.DEFAULTS
    planted = N.table(_minutes(40, seed=3, plant=True), "5m", "1m", [3])
    bt = N.backtest(planted, 3, c["neighbors"], 0.25, c["call_min"])
    assert bt["calls"] >= 100 and bt["edge"] and bt["extra_pts"] >= 5        # volume says it reverses: the model sees it
    noise = N.table(_minutes(40, seed=4), "5m", "1m", [3])
    bn = N.backtest(noise, 3, c["neighbors"], 0.25, c["call_min"])
    assert not bn["edge"] and abs(bn["extra_pts"]) <= 6                      # a random walk: nothing beyond 'keeps its colour'
    assert 55 <= bn["in68_pct"] <= 82                                        # the 68% box is honest
    words = " ".join(N.words("5m", "1m", 3, bn, "on unseen days"))
    assert "nothing reliable" in words and "keeps the colour" in words
    assert all(s["time"] and "p_green" in s for s in bt["shown"])


def _engine(hist, today, now):
    class Store:
        def __init__(self):
            import sqlite3, threading
            self.db = sqlite3.connect(":memory:", check_same_thread=False)
            self.lock = threading.Lock()
    clk = types.SimpleNamespace(ts=lambda: now[0], speed=1.0)
    e = types.SimpleNamespace(store=Store(), source="upstox", running=True, clock=clk, metrics={"NIFTY": {"spot": 1}, "BANKNIFTY": {"spot": 1}},
                              in_play=set(), uni=None, signal_facts=lambda s, d, n: {"flow_imb": 0.3, "news_n": 2, "label": "x"})
    e.charts = types.SimpleNamespace(today_bars=lambda s: [r for r in today if r[0] + 60 <= now[0] + 60])
    e.mtf = types.SimpleNamespace(long_rows=lambda sym, base, days: (hist if base == 1 else __import__("live.mtf", fromlist=["x"]).resample(hist, base), "test"))
    return e


def test_live_predictions_grading_and_view(monkeypatch):
    hist = _minutes(30, seed=7)
    today = [r for r in _minutes(1, seed=8, start=DAY, today=True)]
    t0 = F._open_ts(DAY)
    now = [t0 + 3 * 60 + 5]                                   # 3 one-minute candles of the first 5-minute candle closed
    e = _engine(hist, today, now)
    monkeypatch.setattr(N, "cfg", lambda: {**N.DEFAULTS, "cascade": [{"tf": "5m", "from": "1m", "after": 3}], "track": ["NIFTY"],
                                            "market_ref": {"default": None}, "track_in_play": 0})
    nc = N.NextCandle(e)
    assert nc.view("NIFTY", "5m")["status"] == "building"
    nc.pool("NIFTY", "5m", DAY.isoformat(), wait=True)
    nc.tick(now[0])
    cur = nc.cur[("NIFTY", "5m")]
    p = cur["pred"]
    assert cur["k"] == 3 and p["low"] <= min(p["open"], p["close"]) and p["high"] >= max(p["open"], p["close"])
    assert p["close_lo"] <= p["close"] <= p["close_hi"] and 0 <= p["p_green"] <= 100 and p["similar"] == 60
    assert cur["facts"] == {"flow_imb": 0.3, "news_n": 2}               # recorded for a later challenger (numbers only)
    v = nc.view("NIFTY", "5m")
    assert v["status"] == "ready" and v["now"]["time"] == int(t0 + F.IST_SHIFT) and v["now"]["k"] == 3 and v["report"]
    now[0] = t0 + 4 * 60 + 5                                  # the 4th minute closed: the prediction is updated
    nc.tick(now[0])
    assert nc.cur[("NIFTY", "5m")]["k"] == 4 and nc.cur[("NIFTY", "5m")]["first"]["k"] == 3
    now[0] = t0 + 5 * 60 + 5                                  # the 5-minute candle closed: graded (first and last prediction)
    nc.tick(now[0])
    rows = e.store.db.execute("SELECT k, right, in68, err, naive_err, facts FROM nextcandle").fetchall()
    assert sorted(r[0] for r in rows) == [3, 4] and all(r[3] is not None for r in rows)
    assert ("NIFTY", "5m") not in nc.cur or nc.cur[("NIFTY", "5m")]["start"] != t0
    v = nc.view("NIFTY", "5m")
    assert v["recent"] and v["recent"][0]["actual"]["close"] == F.groups([r for r in today if r[0] < t0 + 300], "5m")[0][1][-1][4]
    assert nc.status["graded"] == 2


def test_cascade_pairs_are_bullets(monkeypatch):
    real = N.pairs()
    assert real == {"5m": {"from": "1m", "after": 3}, "15m": {"from": "5m", "after": 2}, "1H": {"from": "15m", "after": 3},
                    "1D": {"from": "1H", "after": 3}}
    monkeypatch.setattr(N, "cfg", lambda: {**N.DEFAULTS, "cascade": [{"tf": "5m", "from": "15m", "after": 1}, {"tf": "zz"},
                                                                     {"tf": "15m", "from": "5m", "after": 2}]})
    assert N.pairs() == {"15m": {"from": "5m", "after": 2}}             # broken ones skipped, the rest kept
