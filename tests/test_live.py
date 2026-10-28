"""Tests for the live scanner. Run: python -m pytest tests -q"""
import datetime as dt

import pytest

from live import detectors
from live.analytics import max_pain, norm_iv
from live.paper import costs
from live.universe import atm_window, build, strike_step


def test_decode_real_protobuf():
    from live import upstox_feed_pb2 as pb
    from live.upstox import decode
    fr = pb.FeedResponse(type=pb.live_feed, currentTs=1)
    f = fr.feeds["NSE_FO|12345"]
    g = f.firstLevelWithGreeks
    g.ltpc.ltp, g.ltpc.cp, g.ltpc.ltt = 101.5, 90.0, 1700000000000
    g.firstDepth.bidP, g.firstDepth.askP, g.firstDepth.bidQ, g.firstDepth.askQ = 101.4, 101.6, 750, 1500
    g.optionGreeks.delta, g.optionGreeks.gamma = 0.52, 0.0012
    g.vtt, g.oi, g.iv = 123450, 98765, 0.1432
    idx = fr.feeds["NSE_INDEX|Nifty 50"].fullFeed.indexFF
    idx.ltpc.ltp, idx.ltpc.cp = 25100.5, 25000.0
    o = idx.marketOHLC.ohlc.add()
    o.interval, o.open, o.high, o.low = "1d", 25010, 25150, 24990
    eq = fr.feeds["NSE_EQ|INE002A01018"].fullFeed.marketFF
    eq.ltpc.ltp, eq.ltpc.cp, eq.vtt, eq.atp = 1420.0, 1400.0, 5_000_000, 1411.2
    q = eq.marketLevel.bidAskQuote.add()
    q.bidP, q.askP, q.bidQ, q.askQ = 1419.9, 1420.1, 10, 20
    typ, ticks = decode(fr.SerializeToString(), pb)
    assert typ == "live_feed"
    t = {x["key"]: x for x in ticks}
    opt = t["NSE_FO|12345"]
    assert opt["ltp"] == pytest.approx(101.5) and opt["oi"] == 98765 and opt["vol"] == 123450
    assert opt["bid"] == pytest.approx(101.4) and opt["delta"] == pytest.approx(0.52)
    n = t["NSE_INDEX|Nifty 50"]
    assert n["ltp"] == pytest.approx(25100.5) and n["day_h"] == 25150
    r = t["NSE_EQ|INE002A01018"]
    assert r["atp"] == pytest.approx(1411.2) and r["askq"] == 20


def test_max_pain():
    chain = {100: {"CE": 1000, "PE": 10}, 110: {"CE": 500, "PE": 500}, 120: {"CE": 10, "PE": 1000}}
    assert max_pain(chain) == 110


def test_norm_iv():
    assert norm_iv(0.15) == pytest.approx(15)
    assert norm_iv(15.0) == 15
    assert norm_iv(0) is None


def test_costs_reasonable():
    # 1 lot of 75 bought at 100, sold at 130
    c = costs(100, 130, 75)
    assert 50 < c < 80


def test_strikes():
    ks = [24900, 24950, 25000, 25050, 25100, 25150]
    assert strike_step(ks, 25020) == 50
    assert atm_window(ks, 25020, 1) == [24950, 25000, 25050]


def _m(**kw):
    base = {"sym": "TEST", "is_index": False, "spot": 100.0, "opt": {}, "fut": {}}
    base.update(kw)
    return base


def test_sharp_move_needs_both_z_and_size():
    prev = _m()
    assert detectors.run(_m(z_5m=5, ret_5m=0.2), prev, {}, 60) == []
    out = detectors.run(_m(z_5m=5, ret_5m=1.0), prev, {}, 60)
    assert out and out[0]["kind"] == "sharp_move" and out[0]["direction"] == "up" and out[0]["severity"] == 2
    out = detectors.run(_m(z_5m=-8, ret_5m=-1.5), prev, {}, 60)
    assert out[0]["severity"] == 3 and out[0]["direction"] == "down"


def test_orb_breakout_once_with_margin():
    prev = _m(spot=100.0, or_high=100.0, or_low=98.0, rel_vol=2)
    # tiny poke above the range: no alert
    assert not [a for a in detectors.run(_m(spot=100.05, or_high=100.0, or_low=98.0, rel_vol=2), prev, {}, 30) if a["kind"] == "orb"]
    out = [a for a in detectors.run(_m(spot=100.3, or_high=100.0, or_low=98.0, rel_vol=2), prev, {}, 30) if a["kind"] == "orb"]
    assert out and out[0]["direction"] == "up"


def test_unusual_options_value_filter():
    u = {"strike": 100, "side": "CE", "key": "k", "lots_5m": 50, "x_pace": 9, "prem_chg_5m": 30, "oi_chg_5m": 5,
         "ltp": 2.0, "kind": "buying", "direction": "up", "value_5m": 100_000}
    assert detectors.run(_m(opt={"unusual": [u]}), _m(), {}, 60) == []
    u["value_5m"] = 6_000_000
    out = detectors.run(_m(opt={"unusual": [u]}), _m(), {}, 60)
    assert out[0]["kind"] == "unusual_options" and out[0]["severity"] == 3


def test_universe_build():
    ms = lambda d: int(dt.datetime(d.year, d.month, d.day, 15, 30).timestamp() * 1000)
    exp = dt.date(2026, 11, 24)
    rows = [
        {"segment": "NSE_EQ", "instrument_key": "NSE_EQ|X", "short_name": "Xcorp", "trading_symbol": "XC"},
        {"segment": "NSE_FO", "underlying_symbol": "XC", "underlying_key": "NSE_EQ|X", "underlying_type": "EQUITY",
         "instrument_type": "FUT", "instrument_key": "NSE_FO|F1", "expiry": ms(exp), "lot_size": 500},
        {"segment": "NSE_FO", "underlying_symbol": "XC", "underlying_key": "NSE_EQ|X", "underlying_type": "EQUITY",
         "instrument_type": "CE", "instrument_key": "NSE_FO|C1", "expiry": ms(exp), "strike_price": 100, "lot_size": 500},
        {"segment": "NSE_FO", "underlying_symbol": "XC", "underlying_key": "NSE_EQ|X", "underlying_type": "EQUITY",
         "instrument_type": "PE", "instrument_key": "NSE_FO|P1", "expiry": ms(exp), "strike_price": 100, "lot_size": 500},
    ]
    uni = build(rows, dt.date(2026, 10, 28))
    u = uni.underlyings["XC"]
    assert u.fut_key == "NSE_FO|F1" and u.expiries == [exp] and u.name == "Xcorp"
    assert set(u.chains[exp][100.0]) == {"CE", "PE"}
    assert uni.by_key["NSE_FO|C1"][0] == "OPT"


# ---------------- P1-P5
def test_order_flow_classification():
    from live.state import MarketState
    st = MarketState()
    st.flow_keys = {"K"}
    st.apply([{"key": "K", "ltp": 100.0, "vol": 1000, "bid": 99.9, "ask": 100.1}], 60)
    st.apply([{"key": "K", "ltp": 100.1, "vol": 1500, "bid": 100.0, "ask": 100.2, "ltq": 10}], 61)   # at ask: buy
    st.apply([{"key": "K", "ltp": 100.0, "vol": 1700, "bid": 99.9, "ask": 100.1}], 62)               # at bid: sell
    i = st.get("K")
    assert i.cvd == 500 - 200
    b, s, n = i.flow_window(5, 60)
    assert (b, s, n) == (500, 200, 2)


def test_big_print_detected():
    from live.state import MarketState
    st = MarketState()
    st.flow_keys = {"K"}
    st.apply([{"key": "K", "ltp": 1000.0, "vol": 0, "bid": 999, "ask": 1001}], 60)
    st.apply([{"key": "K", "ltp": 1001.0, "vol": 600000, "bid": 1000, "ask": 1002, "ltq": 600000}], 61)
    assert st.prints and st.prints[-1][2] == 600000 and st.prints[-1][4] == 1


def test_gex_signs_and_flip():
    import datetime as dt
    from live.clock import IST
    from live.vol import gex
    now = dt.datetime(2026, 10, 28, 11, 0, tzinfo=IST)
    rows = [{"strike": k, "CE": {"oi": 1e6 if k >= 100 else 1e5, "iv": 15.0},
             "PE": {"oi": 3e6 if k <= 96 else 1e5, "iv": 15.0}} for k in range(90, 111)]
    g = gex({"rows": rows, "expiry": "2026-11-03"}, 100.0, now)
    assert g and g["call_wall"] >= 100 and g["put_wall"] <= 96
    assert g["flip"] is None or 90 <= g["flip"] <= 110


def test_levels_and_score():
    from live.record import evidence, levels, signal_score
    from live.state import MarketState
    m = {"sym": "X", "spot": 100.0, "vwap": 99.0, "vs_vwap_pct": 1.0, "ret_15m": 0.5, "or_high": 99.5, "or_low": 98.0,
         "sigma_1m": 0.05, "day_h": 101.5, "day_l": 97.0, "opt": {"support": 98.0, "resistance": 102.0}, "fut": {}}
    lv = levels(m, "up", MarketState())
    assert lv["stop"] < 100 < lv["target"] and lv["rr"] > 0
    ev = evidence(m, "up")
    assert sum(e["agree"] for e in ev) >= 3
    assert signal_score(2, ev, {"n": 40, "hit_rate": 60}) > signal_score(2, [], None)


def test_regime_trend():
    from live.learn import regime
    m = {"spot": 101.0, "day_o": 100.0, "day_h": 101.1, "day_l": 99.9, "vwap": 100.5, "opt": {"daily_move_pct": 1.0}}
    r = regime(m, 120, {})
    assert r["code"] == "trend_up"


def test_filing_classifier():
    from live.context import classify_filing
    assert classify_filing("Outcome of Board Meeting", "submitted the financial results for the period")[0] == "Results"
    assert classify_filing("Investor Presentation", "results presentation") is None
    assert classify_filing("Bagging/Receiving of orders/contracts", "received an order worth Rs 500 crore")[0] == "Order win"


def test_risk_sizing():
    import datetime as dt
    from live.paper import PaperBook
    class S:  # minimal store
        def trades(self, **k): return []
    b = PaperBook(S(), "sim", {"capital": 200000, "risk_pct": 1.0, "stop_pct": 20, "max_lots": 5})
    assert b.size(10.0, 75) == 5          # 150/lot risk vs 2000 budget, capped at 5
    assert b.size(1000.0, 35) == 1        # 7000/lot risk > budget: still 1 lot
    assert b.guard(dt.datetime(2026, 10, 28, 10, 0))["blocked"] is False
