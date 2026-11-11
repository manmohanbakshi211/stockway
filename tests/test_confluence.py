"""2.59: "Signals agree" alert and the "Index slot" paper account. Mandeep, 11 Nov 2026 (Bank Nifty breakout: new day
high + flag breakout + heavy PE writing within a minute, no pop-up; the main account's 3 slots full of stocks so the
breakout was bought 12 minutes late): "jo best ho vo karo"."""
import datetime as dt
import json

from live.clock import IST


def _engine():
    from live.engine import LiveEngine
    e = LiveEngine.__new__(LiveEngine)
    e.metrics = {"BANKNIFTY": {"spot": 60880.0}}
    e.got = []
    e._emit_safe = lambda a, now, lc: e.got.append(a)
    return e


def test_three_alert_types_the_same_way_make_one_signals_agree_alert():
    e = _engine()
    now = dt.datetime(2026, 11, 11, 10, 29, tzinfo=IST)
    for i, (k, t) in enumerate([("day_high", "new day high"), ("day_high", "again"), ("pattern_flag", "flag breakout")]):
        e._confluence_check({"sym": "BANKNIFTY", "kind": k, "direction": "up", "title": t}, now + dt.timedelta(seconds=10 * i), {})
    assert e.got == []                                         # 2 types only
    e._confluence_check({"sym": "BANKNIFTY", "kind": "unusual_options", "direction": "up", "title": "PE writing"},
                        now + dt.timedelta(seconds=50), {})
    assert len(e.got) == 1
    a = e.got[0]
    assert a["kind"] == "confluence" and a["direction"] == "up" and a["data"]["notify"] and a["severity"] == 2
    assert set(a["data"]["parts"]) == {"day_high", "pattern_flag", "unusual_options"} and "3 signals agree up" in a["title"]
    e._confluence_check({"sym": "BANKNIFTY", "kind": "orb", "direction": "up", "title": "x"}, now + dt.timedelta(seconds=60), {})
    assert len(e.got) == 1                                     # spent: the next one needs 3 new types


def test_old_and_opposite_alerts_do_not_count():
    e = _engine()
    now = dt.datetime(2026, 11, 11, 10, 0, tzinfo=IST)
    e._confluence_check({"sym": "BANKNIFTY", "kind": "day_high", "direction": "up", "title": "a"}, now, {})
    e._confluence_check({"sym": "BANKNIFTY", "kind": "orb", "direction": "down", "title": "b"}, now, {})
    e._confluence_check({"sym": "BANKNIFTY", "kind": "news", "direction": "up", "title": "c"}, now, {})          # ignored type
    e._confluence_check({"sym": "BANKNIFTY", "kind": "orb", "direction": "up", "title": "d"}, now + dt.timedelta(minutes=5), {})
    e._confluence_check({"sym": "BANKNIFTY", "kind": "sharp_move", "direction": "up", "title": "e"}, now + dt.timedelta(minutes=5), {})
    assert e.got == []                                         # the first one is older than 3 minutes


def test_signals_agree_never_trades_in_the_main_account_and_always_notifies():
    from live import registry
    from live.engine import confluence_cfg
    assert registry.label("confluence") == "Signals agree"
    assert "confluence" not in registry.paper_kinds()
    c = confluence_cfg()
    assert c["on"] and c["notify"] and c["severity"] < 3        # pop-up by its own flag, not by becoming Focus
    js = open("webapp/static/live.js").read()
    assert "a.data && a.data.notify" in js


def test_index_slot_keeps_one_slot_for_the_indices(tmp_path):
    from live import books as B
    from live.store import Store
    from tests.test_books import MAIN, _state, _t, DAY
    st = Store(tmp_path / "t.db")
    bs = B.Books("upstox", st, {**dict(MAIN), "max_open": 3}, None, log=lambda m: None)
    bk = bs.books["idxslot"]
    assert bk.cfg["index_slots"] == 1
    now = _t(DAY, 10, 0)

    def al(i, sym, idx):
        return {"id": i, "sym": sym, "kind": "volume_surge", "severity": 3, "direction": "up", "title": "t", "und_key": "UND",
                "is_index": idx, "option": {"key": f"O{i}", "label": f"{sym} 100 CE", "side": "CE", "lot": 10},
                "data": {"focus": True, "score": 80, "risk": {"verdict": "PASS", "lots": 1}}}
    px = {f"O{i}": 10.0 for i in range(1, 6)}
    r1 = bk.try_open(al(1, "S1", False), _state(**px), now)
    r2 = bk.try_open(al(2, "S2", False), _state(**px), now)
    r3 = bk.try_open(al(3, "S3", False), _state(**px), now)      # the third stock would fill the last slot
    r4 = bk.try_open(al(4, "BANKNIFTY", True), _state(**px), now)
    assert r1 and r2 and r3 is None and r4
    assert any("kept for Nifty" in k for k in bk.skipped)
    main = B.PaperBook(st, "upstox", {**dict(MAIN), "max_open": 3}, log=lambda m: None)
    assert "index_slots" not in main.cfg or not main.cfg.get("index_slots")     # the main account: as before
