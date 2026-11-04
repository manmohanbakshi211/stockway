"""2.33: data quality guard: bad prints, real moves, crossed quotes, stale prices; held alerts; honest test fills."""
import datetime as dt
import types

from live import bench as B, dataguard as G
from live.clock import IST
from live.state import MarketState
from live.store import Store

NOW = dt.datetime(2026, 11, 4, 11, 0, tzinfo=IST)


class Clock:
    def __init__(self, t):
        self.t = t

    def ts(self):
        return self.t

    def now(self):
        return dt.datetime.fromtimestamp(self.t, IST)


def _engine():
    st = MarketState()
    u = types.SimpleNamespace(key="IDX|NIFTY", fut_key="FUT|NIFTY", is_index=True)
    s = types.SimpleNamespace(key="EQ|ABC", fut_key=None, is_index=False)
    clk = Clock(NOW.timestamp())
    e = types.SimpleNamespace(state=st, uni=types.SimpleNamespace(underlyings={"NIFTY": u, "ABC": s}), clock=clk, source="upstox",
                              recorder=None, health=None)
    return e, st, clk


def _tick(st, clk, key, ltp, bid=None, ask=None):
    st.apply([{"key": key, "ltp": ltp, "bid": bid, "ask": ask}], clk.t)


def test_a_bad_print_is_held_then_thrown_out_and_a_real_move_released():
    e, st, clk = _engine()
    g = G.DataGuard(e)
    _tick(st, clk, "IDX|NIFTY", 25000)
    _tick(st, clk, "FUT|NIFTY", 25050)
    g.check(force=True)
    clk.t += 5
    _tick(st, clk, "IDX|NIFTY", 25900)                         # +3.6% in 5 s: suspect
    g.check(force=True)
    assert g.is_bad("IDX|NIFTY") and "jump" in g.symbol_problem("NIFTY")
    a = {"sym": "NIFTY", "kind": "orb", "direction": "up", "title": "NIFTY ORB"}
    assert g.hold(a) and g.held[0]["sym"] == "NIFTY"
    clk.t += 5
    _tick(st, clk, "IDX|NIFTY", 25010)                         # snaps back: a bad print
    g.check(force=True)
    assert not g.is_bad("IDX|NIFTY") and g.counts["bad_prints"] == 1 and g.symbol_problem("NIFTY") is None
    clk.t += 5
    _tick(st, clk, "IDX|NIFTY", 25500)                         # +2% and it stays: a real move
    g.check(force=True)
    assert g.is_bad("IDX|NIFTY")
    for _ in range(3):
        clk.t += 5
        _tick(st, clk, "IDX|NIFTY", 25510)
        g.check(force=True)
    assert not g.is_bad("IDX|NIFTY") and g.counts["real_moves"] == 1


def test_crossed_quotes_and_stale_prices():
    e, st, clk = _engine()
    g = G.DataGuard(e)
    _tick(st, clk, "OPT|1", 100, bid=101, ask=99)
    _tick(st, clk, "EQ|ABC", 500)
    g.check(force=True)
    assert g.is_bad("OPT|1") and g.suspect["OPT|1"]["why"] == "crossed quote"
    _tick(st, clk, "OPT|1", 100, bid=99, ask=101)
    g.check(force=True)
    assert not g.is_bad("OPT|1")
    clk.t += 400                                                 # the stock hasn't ticked for 400 s
    g.check(force=True)
    assert "EQ|ABC" in g.stale and g.symbol_problem("ABC") == "no live price update for minutes"
    assert not g.fresh("OPT|1")                                  # a 400-second-old quote is no fill


def test_the_bench_skips_old_quotes_and_never_exits_on_a_suspect_price(tmp_path):
    e, st, clk = _engine()
    g = G.DataGuard(e)
    exp = dt.date(2026, 11, 10)
    o = lambda k: types.SimpleNamespace(key=k, lot=65)
    u = types.SimpleNamespace(key="IDX|NIFTY", fut_key="FUT|NIFTY", is_index=True,
                              chains={exp: {25000.0: {"CE": o("CE0"), "PE": o("PE0")}}})
    e.uni.underlyings["NIFTY"] = u
    e.regimes, e.dguard = {}, g
    for k in ("CE0", "PE0"):
        _tick(st, clk, k, 100, bid=99, ask=100)
    opt = {"key": "CE0", "side": "CE", "strike": 25000.0, "expiry": exp.isoformat(), "lot": 65}
    cfg = {"target_pct": 20, "stop_pct": 20, "max_hold_min": 60, "no_entry_after": "14:45"}
    b = B.Bench(Store(tmp_path / "live.db"), "upstox", log=lambda *a: None)
    clk.t += 400                                                 # quotes now 400 s old
    assert b.add({"sym": "NIFTY", "kind": "orb", "direction": "up", "option": opt}, e, clk.now(), cfg) == 0
    assert b.stale_skips.get("buy") == 1
    for k in ("CE0", "PE0"):
        _tick(st, clk, k, 100, bid=99, ask=100)
    b.cool.clear()
    assert b.add({"sym": "NIFTY", "kind": "orb", "direction": "up", "option": opt}, e, clk.now(), cfg) >= 2
    g.check(force=True)
    clk.t += 5
    _tick(st, clk, "CE0", 300, bid=299, ask=301)                 # a 3x print on the bought call
    g.check(force=True)
    assert g.is_bad("CE0")
    b.update(st, clk.t, bad=g.is_bad)
    assert all(r.get("exit") is None for r in b.open if r["key1"] == "CE0")   # no fake target hit
    with b.bdb.lock:
        assert b.bdb.db.execute("SELECT COUNT(*) FROM bench_trades WHERE net IS NOT NULL AND key1='CE0'").fetchone()[0] == 0


def test_news_alerts_are_never_held():
    from live.engine import LiveEngine
    e = types.SimpleNamespace(dguard=types.SimpleNamespace(hold=lambda a: True))
    assert LiveEngine._data_hold(e, {"kind": "news", "sym": "NIFTY"}) is False
    assert LiveEngine._data_hold(e, {"kind": "orb", "sym": "NIFTY"}) is True
