"""2.49: extra paper accounts next to the main one (Mandeep, 10 Nov 2026, points 14 and 15).

"Best only" takes only the highest-rated alerts; "3% a day" aims for +3% of the account a day, compounding, top
alerts only. Each is its own paper account (own money, own trades) and never touches the main account."""
import datetime as dt
import json

import pytest

from live import books as B
from live.clock import IST
from live.store import Store


def _t(day, h, m):
    return dt.datetime.fromisoformat(f"{day}T{h:02d}:{m:02d}:00").replace(tzinfo=IST)


def _ms(day, h, m):
    return int(_t(day, h, m).timestamp() * 1000)


class Inst:
    def __init__(self, ltp, ltt=None):
        self.ltp, self.bid, self.ask, self.ltt, self.t = ltp, ltp, ltp, ltt, None


DAY = "2026-11-09"
MAIN = {"capital": 500000, "risk_pct": 1.0, "max_daily_loss_pct": 3.0, "target_pct": 20, "stop_pct": 15,
        "max_hold_min": 60, "exit_time": "15:15", "no_entry_after": "14:45", "min_severity": 2, "max_open": 5,
        "max_trades_day": 10, "max_lots": 5, "kinds": ["volume_surge"]}


def _alert(i=1, kind="volume_surge", focus=True, sev=3, score=80, track=None, lots=1, key="O1"):
    return {"id": i, "sym": f"S{i}", "kind": kind, "severity": sev, "direction": "up", "title": "t", "und_key": "UND",
            "option": {"key": key, "label": f"S{i} 100 CE", "side": "CE", "lot": 100},
            "data": {"focus": focus, "score": score, "track": track, "risk": {"verdict": "PASS", "lots": lots}}}


GOOD = {"decided": 30, "hit_rate": 60, "avg_o60": 2.0, "n": 40}


def _books(tmp_path, user=None):
    st = Store(tmp_path / "t.db")
    return st, B.Books("upstox", st, dict(MAIN), user, log=lambda m: None)


def _state(**px):
    ms = _ms(DAY, 9, 59)
    s = {k: Inst(v, ms) for k, v in px.items()}
    s.setdefault("UND", Inst(500.0, ms))
    return s


def test_rules_file_loads_both_accounts():
    c = B.cfg()
    assert {"best", "target3"} <= set(c["books"])
    assert c["books"]["target3"]["day_target_pct"] == 3.0 and c["books"]["target3"]["compound"] is True


def test_best_only_takes_only_the_highest_rated(tmp_path):
    st, bs = _books(tmp_path)
    assert bs.pick("best", _alert(focus=False, track=GOOD)) == "not a Focus alert"
    assert bs.pick("best", _alert(score=60, track=GOOD)).startswith("score below")
    assert bs.pick("best", _alert(sev=2, track=GOOD)).startswith("strength below")
    assert bs.pick("best", _alert(track=None)) == "alert type not graded enough yet"
    assert bs.pick("best", _alert(track={**GOOD, "hit_rate": 40})) == "alert type's live record too weak"
    assert bs.pick("best", _alert(track={**GOOD, "avg_o60": -1.0})) == "alert type loses on average"
    assert bs.pick("best", _alert(track=GOOD)) is None
    assert bs.pick("best", _alert(kind="factory", track=None)) is None       # a Lab rule that passed the strict bar
    a = _alert(track=GOOD)
    a["data"]["muted"] = "demoted"
    assert bs.pick("best", a) == "demoted by its live record"


def test_accounts_trade_apart_from_the_main_account(tmp_path):
    st, bs = _books(tmp_path)
    now = _t(DAY, 10, 0)
    out = bs.consider(_alert(track=GOOD, kind="orb"), _state(O1=10.0), now)   # 'orb' is not a main-account kind: fine here
    assert out["best"] == "taken" and out["target3"] == "taken"
    assert {t["source"] for t in st.trades()} == {"upstox#best", "upstox#target3"}
    assert st.trades(source="upstox") == []                                   # the main account is untouched
    s = bs.summary("best", _state(O1=11.0), now)
    assert s["name"] == "Best only" and s["today_seen"] == 1 and s["today_taken"] == 1 and len(s["open"]) == 1
    out = bs.consider(_alert(i=2, focus=False, key="O2"), _state(O1=10.0, O2=10.0), now)
    assert out["best"] == "not a Focus alert" and bs.summary("best", None, now)["today_why"] == {"not a Focus alert": 1}


def test_risk_check_must_pass(tmp_path):
    st, bs = _books(tmp_path)
    a = _alert(track=GOOD)
    a["data"]["risk"] = {"verdict": "BLOCK", "reasons": ["premium too high"]}
    out = bs.consider(a, _state(O1=10.0), _t(DAY, 10, 0))
    assert out["best"].startswith("risk check") and st.trades() == []


def test_three_percent_account_books_the_day_and_stops(tmp_path):
    st, bs = _books(tmp_path, {"target3": {"capital": 100000}})
    now = _t(DAY, 10, 0)
    bs.consider(_alert(track=GOOD, lots=2), _state(O1=100.0), now)            # 2 lots x 100 = ₹20,000 in
    t = st.trades(source="upstox#target3")[0]
    assert t["lots"] == 2
    ds = bs.day_state("target3", _state(O1=100.0), now)
    assert ds["start"] == 100000 and ds["target"] == 3000 and ds["stop"] == 2000
    bs.update(_state(O1=110.0), _t(DAY, 10, 5))                               # +₹2,000 open: not yet 3%
    assert st.trades(source="upstox#target3", status="open")
    bs.update(_state(O1=116.0), _t(DAY, 10, 10))                              # +₹3,200 open: day target reached
    done = st.trades(source="upstox#target3")[0]
    assert done["status"] == "closed" and done["reason"] == "day target reached"
    assert bs.consider(_alert(i=3, track=GOOD, key="O3"), _state(O3=10.0), _t(DAY, 10, 20))["target3"] == "day target reached"


def test_three_percent_account_compounds_day_to_day(tmp_path):
    st, bs = _books(tmp_path, {"target3": {"capital": 100000}})
    st.add_trade({"source": "upstox#target3", "day": "2026-11-06", "sym": "X", "kind": "orb", "opt_key": "K", "label": "X",
                  "side": "CE", "lot": 100, "lots": 1, "entry": 10, "entry_ts": 1, "status": "closed", "net": 4000.0,
                  "exit": 50, "exit_ts": 2, "target": 12, "stop": 8, "last": 50})
    ds = bs.day_state("target3", _state(), _t(DAY, 10, 0))
    assert ds["start"] == 104000 and ds["target"] == pytest.approx(3120)       # yesterday's result is today's base
    assert bs.day_state("best", _state(), _t(DAY, 10, 0)) is not None


def test_a_broken_account_never_stops_the_others(tmp_path, monkeypatch):
    bad = {"books": {"best": B.cfg()["books"]["best"], "broken": {"name": "Broken", "capital": "lots of money"}}}
    monkeypatch.setattr(B, "cfg", lambda: bad)
    st, bs = _books(tmp_path)
    assert "best" in bs.books and "broken" not in bs.books and "broken" in bs.errors
    assert bs.consider(_alert(track=GOOD), _state(O1=10.0), _t(DAY, 10, 0))["best"] == "taken"
    assert any(x["id"] == "broken" and x["error"] for x in bs.list())


def test_compare_lines_up_the_accounts(tmp_path):
    st, bs = _books(tmp_path, {"target3": {"capital": 100000}})
    for i, (src, day, net) in enumerate([("upstox", "2026-11-05", -500.0), ("upstox", "2026-11-06", 900.0),
                                         ("upstox#target3", "2026-11-05", 3500.0), ("upstox#target3", "2026-11-06", -1000.0)]):
        st.add_trade({"source": src, "day": day, "sym": "X", "kind": "orb", "opt_key": "K", "label": "X", "side": "CE",
                      "lot": 100, "lots": 1, "entry": 10, "entry_ts": i, "status": "closed", "net": net, "exit": 12,
                      "exit_ts": i + 1, "target": 12, "stop": 8, "last": 12})
    rows = {r["id"]: r for r in B.compare(st, "upstox", MAIN, bs)}
    assert rows["main"]["trades"] == 2 and rows["main"]["net"] == 400 and rows["main"]["max_drop"] == -500
    assert rows["target3"]["target_days"] == 1 and rows["target3"]["green_days"] == 1 and rows["best"]["trades"] == 0


def test_account_capital_is_a_setting_without_a_code_change(tmp_path, monkeypatch):
    from radar import config as C
    monkeypatch.setattr(C, "USER_SETTINGS", tmp_path / "user_settings.json")
    C.save_user_settings({"live.books.target3.capital": "250000", "live.books.some_new_account.capital": 1e5})
    saved = json.loads((tmp_path / "user_settings.json").read_text())
    assert saved["live.books.target3.capital"] == 250000.0
    with pytest.raises(ValueError):
        C.save_user_settings({"live.books.target3.max_open": 9})                 # only the capital is open to the app
