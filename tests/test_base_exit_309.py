"""3.09 (Mandeep, 25 Nov 2026: "stoploss capital percentage nahi scalping ke hisab se chart, price ... ye test mein nahi by
default sab trades ke liye apply karo ... ye hi ab base rahega. har paper trade ke liye"): the base exit of every paper
trade is on the chart of the stock / index (rules/paper_exit.json), the old premium exit stays on one account to compare."""
import datetime as dt
import json
import types

import numpy as np

from lab import exits
from lab.chart_exit import swing_stop
from live import books as B
from live.engine import LiveEngine, paper_exit
from live.paper import PaperBook, exit_view
from live.store import Store
from tests.test_books import MAIN, _t, _state, DAY
from tests.test_trail_book import _alert

BASE = {**MAIN, "exit_style": "chand"}


def test_rules_file_names_a_known_style():
    pe = paper_exit()
    assert pe["style"] in exits.STYLES and pe["style"] == "chand"
    assert pe["valid_from"] == "2026-11-26" and pe["why"] and pe["early_atr_from_1min"] is True
    assert pe["book_leg"]["style"] == "chart" and pe["book_leg"]["exit"]["failed_breakout"] is False


def test_alert_trade_exits_on_the_chart(tmp_path):
    pb = PaperBook(Store(tmp_path / "t.db"), "upstox", dict(BASE), log=lambda m: None)
    tid = pb.try_open(_alert(R=12.0), _state(O1=10.0), _t(DAY, 10, 0))
    assert tid
    t = pb.open_trades()[0]
    pj = json.loads(t["plan"])
    assert pj["style"] == "chand" and pj["R"] == 12.0 and pj["s0"] == 500.0
    assert t["target"] == 1000.0                          # no premium target (placeholder), the trail decides
    assert t["stop"] == 6.0                               # only the emergency premium stop (-40%)
    assert t["hold_max"] == exits.STYLES["chand"]["max"]  # no 60-minute limit


def test_no_chart_risk_no_trade(tmp_path):
    pb = PaperBook(Store(tmp_path / "t.db"), "upstox", dict(BASE), log=lambda m: None)
    assert pb.try_open(_alert(R=None), _state(O1=10.0), _t(DAY, 10, 0)) is None
    assert any("no chart stop yet" in k for k in pb.skipped)


def test_lab_rule_percent_exit_becomes_the_chart_exit(tmp_path):
    pb = PaperBook(Store(tmp_path / "t.db"), "upstox", dict(BASE), log=lambda m: None)
    a = _alert(R=10.0)
    a["data"]["factory"] = {"exit": [25, 15, 45]}
    assert pb.try_open(a, _state(O1=10.0), _t(DAY, 10, 0))
    assert json.loads(pb.open_trades()[0]["plan"])["style"] == "chand"


def test_own_chart_plans_and_premium_accounts_keep_theirs(tmp_path):
    st = Store(tmp_path / "t.db")
    pb = PaperBook(st, "upstox", dict(BASE), log=lambda m: None)
    a = _alert(R=10.0)
    a["data"]["factory"] = {"plan": {"style": "atr2r", "R": 8.0}}
    assert pb.try_open(a, _state(O1=10.0), _t(DAY, 10, 0))
    assert json.loads(pb.open_trades()[0]["plan"])["style"] == "atr2r"
    old = PaperBook(st, "upstox#old", {**MAIN, "exit_style": "premium"}, log=lambda m: None)
    assert old.try_open(_alert(R=10.0), _state(O1=10.0), _t(DAY, 10, 0))
    t = old.open_trades()[0]
    assert t["plan"] in (None, "") and t["target"] == 12.0 and t["stop"] == 8.5     # +20% / -15% (MAIN)


def test_manual_trade_keeps_your_exit(tmp_path):
    pb = PaperBook(Store(tmp_path / "t.db"), "upstox", dict(BASE), log=lambda m: None)
    assert pb.try_open(_alert(R=None), _state(O1=10.0), _t(DAY, 10, 0), manual=True)
    t = pb.open_trades()[0]
    assert t["plan"] in (None, "") and t["target"] == 12.0


def test_accounts_inherit_the_base_and_old_exit_account_compares(tmp_path):
    st = Store(tmp_path / "t.db")
    bs = B.Books("upstox", st, dict(BASE), None, log=lambda m: None)
    sp = B.cfg()["books"]["main_pct"]
    assert sp["exit_style"] == "premium" and sp["pick"]["mirror_main"] and sp["on"]
    assert bs.books["main_pct"].cfg["exit_style"] == "premium"
    assert bs.books["idxslot"].cfg["exit_style"] == "chand"          # accounts with the main exit get the base
    now = _t(DAY, 10, 0)
    out = bs.consider(_alert(R=12.0), _state(O1=10.0), now, main={"id": 7, "lots": 2})
    assert out["main_pct"] == "taken" and out["trail"] == "taken"
    old = st.trades(source="upstox#main_pct")[0]
    assert old["plan"] in (None, "") and old["lots"] == 2 and old["target"] == 12.0
    # without a chart stop the old-exit account still mirrors the main account (it needs no ATR)
    out2 = bs.consider(_alert(R=None, i=2, key="O2"), _state(O2=10.0), now, main={"id": 8, "lots": 1})
    assert out2["main_pct"] == "taken" and out2["trail"].startswith("no chart risk")


def test_half_book_sells_the_booked_part_at_the_chart_level(tmp_path):
    st = Store(tmp_path / "t.db")
    bs = B.Books("upstox", st, dict(BASE), None, log=lambda m: None)
    a = _alert(R=12.0)
    a["data"]["chart"] = {"stop": 492.0, "atr": 8.0, "named": [("Yesterday's high", 510.0)]}
    out = bs.consider(a, _state(O1=10.0), _t(DAY, 10, 0), main={"id": 7, "lots": 2})
    assert out["halftrail"] == "taken"
    legs = {t["mode"]: t for t in st.trades(source="upstox#halftrail")}
    book = json.loads(legs["leg:book"]["plan"])
    assert book["style"] == "chart" and book["role"] == "t1" and book["stop"] == 492.0
    assert book["c"]["nomove_bars"] == 999 and book["c"]["failed_breakout"] is False and book["t1"] == 510.0
    assert json.loads(legs["leg:trail"]["plan"])["style"] == "chand"


def test_swing_stop():
    h = [10, 11, 12, 11, 10, 11, 12, 13, 12]
    l = [9, 10, 9.5, 9, 8.6, 9.5, 10, 11, 11.5]
    assert swing_stop(h, l, 1, 12.5, 2.0) == 8.4               # swing low 8.6 (1.95 ATR away) minus 0.1 ATR
    assert swing_stop(h, l, 1, 12.5, 10.0) == 12.5 - 15.0      # no swing 0.75-2 ATR away: 1.5 ATR
    assert swing_stop([5, 6, 9, 6, 5], [4, 5, 8, 5, 4], -1, 7.0, 2.0) == 9.2    # puts: above the swing high


def test_exit_view_shows_the_underlying_levels(tmp_path):
    pb = PaperBook(Store(tmp_path / "t.db"), "upstox", dict(BASE), log=lambda m: None)
    pb.try_open(_alert(R=12.0), _state(O1=10.0), _t(DAY, 10, 0))
    t = pb.summary(_state(O1=10.0))["open"][0]
    v = t["exit_view"]
    assert v["style"] == "chand" and v["stop"] == 488.0 and v["target"] is None and v["d"] == 1
    assert exit_view({"plan": None}) is None and exit_view({"plan": "{bad"}) is None


def _stub(rows5=None, rows1=()):
    paper = types.SimpleNamespace(cfg={"exit_style": "chand", "capital": 500000, "risk_pct": 1.0})
    charts = types.SimpleNamespace(five_min=lambda s, d: rows5, _session_start=lambda d: _t(DAY, 9, 15).timestamp(),
                                   today_bars=lambda s: list(rows1))
    e = types.SimpleNamespace(charts=charts, paper=paper, _chart_named=lambda s, n: [])
    e.best_style = lambda: LiveEngine.best_style(e)
    return e


def test_first_minutes_get_a_chart_stop_from_one_minute_bars():
    t0 = _t(DAY, 9, 15).timestamp()
    rows = [[t0 + 60 * i, 100 + i, 101 + i, 99 + i, 100.5 + i, 0] for i in range(8)]
    a = {"sym": "S1", "direction": "up", "option": {"ltp": 10.0, "lot": 100, "delta": 0.5}}
    p, R, s0 = LiveEngine.trade_plan(_stub(None, rows), a, {"spot": 108.0}, _t(DAY, 9, 23))
    atr = 2.0 * np.sqrt(5)                                      # true range 2 a minute, x sqrt(5)
    assert abs(R["chand"] - 1.5 * atr) < 1e-6 and p["style"] == "chand"
    assert a["data"]["chart"]["stop"] == round(108.0 - 1.5 * atr, 2)
    a2 = {"sym": "S1", "direction": "up"}
    assert LiveEngine.trade_plan(_stub(None, rows[:3]), a2, {"spot": 108.0}, _t(DAY, 9, 18)) == (None, None, None)


def test_alert_plan_shows_the_base_style():
    e = _stub()
    assert LiveEngine.best_style(e) == "chand"
    e.paper.cfg["exit_style"] = "premium"                       # the base off: the alert's plan comes from the Lab again
    e._best_style = ("atr2r", dt.datetime.now().timestamp() + 1e9)
    assert LiveEngine.best_style(e) in exits.STYLES


def test_selfcheck_lists_the_base_exit():
    from live import selfcheck
    assert any(x["id"] == "base_exit" and x["ok"] for x in selfcheck.static_checks())
