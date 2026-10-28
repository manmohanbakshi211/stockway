"""Tests for chart indicators, chart setups, the world monitor and the market brain."""
import numpy as np

from live import setups, ta
from live.brain import rules
from live.world import World


def test_indicators_basic():
    c = np.linspace(100, 120, 60)
    assert np.allclose(ta.ema(np.full(30, 50.0), 9), 50.0)
    r = ta.rsi(c, 14)
    assert np.isnan(r[:13]).all() and r[-1] > 95          # steady rise -> RSI near 100
    h, l = c + 0.5, c - 0.5
    st, sd = ta.supertrend(h, l, c, 10, 3.0)
    assert sd[-1] > 0 and st[-1] < c[-1]                  # uptrend: supertrend below price
    vw, u1, d1, u2, d2 = ta.vwap_bands(h, l, c, np.zeros(60))
    assert d2[-1] <= d1[-1] <= vw[-1] <= u1[-1] <= u2[-1]
    p = ta.cpr(110, 100, 105)
    assert p["BC"] <= p["P"] <= p["TC"] and p["S1"] < p["P"] < p["R1"] and p["width_pct"] >= 0


def test_resample_five_minutes():
    t = np.arange(10) * 60.0
    o = h = l = c = np.arange(10, dtype=float)
    rt, ro, rh, rl, rc, rv = ta.resample(t, o, h, l, c, np.ones(10), 5, 0)
    assert list(rt) == [0, 300] and list(ro) == [0, 5] and list(rc) == [4, 9] and list(rh) == [4, 9] and list(rv) == [5, 5]


def _bars(closes):
    c = np.asarray(closes, dtype=float)
    o = np.r_[c[0], c[:-1]]
    return {"t": np.arange(len(c)) * 300.0, "o": o, "h": np.maximum(o, c) + 2, "l": np.minimum(o, c) - 2, "c": c,
            "v": np.zeros(len(c))}


def test_pdh_break_signal_has_stop_below_and_target_above():
    closes = [100 + i * 1.5 for i in range(12)] + [125]      # last bar closes above yesterday's high (120)
    b = _bars(closes)
    b["c"][-2] = 119.0
    ind = setups.indicators(b)
    ctx = {"is_index": True, "pdh": 120.0, "pdl": 90.0, "pdc": 100.0, "cpr": ta.cpr(120, 90, 100),
           "or_high": max(b["h"][:3]), "or_low": min(b["l"][:3])}
    sig = [s for s in setups.evaluate(b, ind, ctx) if s["setup"] == "pdh_break"]
    assert sig, "expected a previous-day-high break"
    s = sig[0]
    assert s["direction"] == "up" and s["stop"] < s["entry"] < s["target"] and s["rr"] > 0


def test_no_signal_on_flat_market():
    b = _bars([100.0] * 20)
    ind = setups.indicators(b)
    ctx = {"is_index": True, "pdh": 130.0, "pdl": 70.0, "pdc": 100.0, "cpr": ta.cpr(130, 70, 100),
           "or_high": 102.0, "or_low": 98.0}
    assert setups.evaluate(b, ind, ctx) == []


def test_world_pressure_and_news_tags(tmp_path):
    w = World(tmp_path, log=lambda *a: None)
    w.rows = {"ES=F": {"name": "S&P 500 futures", "region": "us", "chg_pct": 1.0},
              "^N225": {"name": "Nikkei 225", "region": "asia", "chg_pct": -0.5}}
    w.corr = {"ES=F": {"next": 0.5, "beta_next": 0.4}, "^N225": {"same": 0.4, "beta_same": 0.3}}
    p = w.pressure()
    # weighted: (0.4*1.0*0.5 + 0.3*-0.5*0.4) / 0.9 = 0.156
    assert abs(p["implied_pct"] - 0.16) < 0.01 and p["label"] == "positive"
    assert p["drivers"][0]["name"] == "S&P 500 futures"

    class FakeNews:
        def fetch(self, _):
            return [{"title": "RBI cuts repo rate, rupee gains"}, {"title": "Local bakery opens new store"}]
    w.news = FakeNews()
    w.refresh_news()
    assert w.tags["RBI cuts repo rate, rupee gains"]["impact"] == "high"
    assert w.tags["Local bakery opens new store"]["impact"] == "low"


def test_brain_rules_bias_and_changes():
    s = {"indices": {"NIFTY": {"spot": 25000, "chg_pct": 0.8, "score": 40, "regime": "Trend up", "support": 24900,
                               "resistance": 25200, "gamma": "pinning"}},
         "breadth": {"n": 50, "adv": 40, "dec": 10}, "global_pressure": {"implied_pct": 0.3, "label": "positive"},
         "vix": {"chg_pct": -6}, "fii_index_futures_long_pct": 45, "events_next_2h": [("USD", "CPI y/y", 40)],
         "ban": [], "hot_stocks": []}
    n = rules(s, None)
    assert n["bias"] == "bullish" and 40 <= n["confidence"] <= 85
    assert n["levels"]["NIFTY"]["support"] == 24900
    assert any("CPI" in r for r in n["risks"])
    prev = {"indices": {"NIFTY": {"spot": 24900, "regime": "Range"}}, "global_pressure": {"label": "neutral"}}
    n2 = rules(s, prev)
    assert any("since the last note" in c for c in n2["changed"]) and any("Range" in c for c in n2["changed"])
