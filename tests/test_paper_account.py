"""2.14: paper account, short positions, close-all, aggressive mode, GIFT Nifty parsing, watchlist."""
import datetime as dt

import pytest

from live.clock import IST
from live.paper import PaperBook
from live.store import Store


class Inst:
    def __init__(self, ltp, bid=None, ask=None):
        self.ltp, self.bid, self.ask = ltp, bid or ltp, ask or ltp


def _now(h, m):
    return dt.datetime(2026, 11, 2, h, m, tzinfo=IST)            # a Monday


OPT = {"key": "OPT", "label": "ABC 100 CE 23 Nov", "side": "CE", "lot": 100}


def test_short_position_pnl_and_exit(tmp_path):
    st = Store(tmp_path / "t.db")
    pb = PaperBook(st, "upstox", {"capital": 200000})
    state = {"OPT": Inst(10.0), "UND": Inst(500.0)}
    r = pb.open_manual(state, _now(10, 0), sym="ABC", opt=OPT, direction=-1, lots=2, und_key="UND")
    t = st.trades()[0]
    assert r["opened"] and t["dir"] == -1 and t["target"] < t["entry"] < t["stop"] and t["und_entry"] == 500.0
    state["OPT"] = Inst(7.0)                                       # option fell: a short gains
    s = pb.summary(state)
    assert s["open"][0]["unreal"] == pytest.approx(600.0)          # (10 - 7) x 200
    acct = s["account"]
    assert acct["used"] > 0 and acct["equity"] == pytest.approx(200000 + 600.0)
    out = pb.open_manual(state, _now(10, 5), sym="ABC", opt=OPT, direction=1)   # Buy closes the short
    assert out["closed"] and out["closed"]["net"] > 0 and st.trades()[0]["status"] == "closed"


def test_close_all_and_market_hours(tmp_path):
    st = Store(tmp_path / "t.db")
    pb = PaperBook(st, "upstox", {"capital": 200000})
    state = {"OPT": Inst(10.0), "OPT2": Inst(20.0)}
    pb.open_manual(state, _now(10, 0), sym="ABC", opt=OPT, direction=1)
    pb.open_manual(state, _now(10, 0), sym="XYZ", opt={**OPT, "key": "OPT2", "label": "XYZ"}, direction=1)
    assert len(pb.open_trades()) == 2
    done = pb.close_all(state, _now(10, 10))
    assert len(done) == 2 and not pb.open_trades()
    with pytest.raises(ValueError):
        pb.open_manual(state, _now(8, 50), sym="ABC", opt=OPT, direction=1)


def test_aggressive_mode_takes_blocked_ideas(tmp_path):
    st = Store(tmp_path / "t.db")
    alert = {"id": 1, "sym": "ABC", "kind": "pattern_triangle", "severity": 2, "direction": "up", "title": "t", "option": OPT,
             "data": {"risk": {"verdict": "BLOCK", "reasons": ["Reward:risk low"], "can_override": True}}}
    state = {"OPT": Inst(10.0)}
    calm = PaperBook(st, "upstox", {"capital": 200000})
    assert calm.try_open(alert, state, _now(10, 0)) is None
    agg = PaperBook(st, "upstox", {"capital": 200000, "aggressive": True})
    assert agg.try_open(alert, state, _now(9, 10)) is None           # never on pre-open prices
    tid = agg.try_open(alert, state, _now(10, 0))
    assert tid and st.trades()[0]["manual"] == 3 and st.trades()[0]["mode"] == "aggressive"


def test_gift_nifty_parse():
    from live.giftnifty import parse
    d = {"data": [{"INSTRUMENT": "Index Futures", "SYMBOL": "NIFTY", "EXPIRYDATE": "29-Dec-2026", "LASTPRICE": 23219.5, "PERCHANGE": 0},
                  {"INSTRUMENT": "Index Futures", "SYMBOL": "NIFTY", "EXPIRYDATE": "23-Nov-2026", "LASTPRICE": 23115.5, "CHANGE": 30.0, "PERCHANGE": 0.13},
                  {"INSTRUMENT": "Index Futures", "SYMBOL": "BANKNIFTY", "EXPIRYDATE": "23-Nov-2026", "LASTPRICE": 55000}]}
    r = parse(d)
    assert r["spot"] == 23115.5 and r["chg_pct"] == 0.13 and r["expiry"] == "23-Nov-2026"
    assert parse({"data": []}) is None


def test_watchlist_manual(tmp_path):
    from live import watchlist
    st = Store(tmp_path / "t.db")
    assert watchlist.change(st, add="reliance") == ["RELIANCE"]
    assert watchlist.change(st, add="TCS") == ["TCS", "RELIANCE"]
    assert watchlist.change(st, add="TCS") == ["TCS", "RELIANCE"]
    assert watchlist.change(st, remove="RELIANCE") == ["TCS"]
