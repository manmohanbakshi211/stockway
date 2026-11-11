"""Ideas on trial ("shadow" in live/registry.py): recorded and graded only, never in alerts, Telegram, Focus or paper."""
import datetime as dt
import types

import numpy as np

from live import candles as CDL
from live import registry
from live.clock import IST
from live.store import Store
from live.trials import Trials, board


class _I:
    def __init__(self, ltp):
        self.ltp = ltp


class _State(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)


def _trial(sym="NIFTY", kind="candle_pin_bar", direction="up", entry=100.0, stop=99.0, target=102.0):
    return {"sym": sym, "kind": kind, "direction": direction, "severity": 1, "title": "t", "detail": "d", "price": entry,
            "day": "2026-11-04", "data": {"setup_levels": {"entry": entry, "stop": stop, "target": target}}}


def test_grading_target_stop_time(tmp_path):
    st = Store(tmp_path / "live.db")
    tr = Trials(st, "upstox")
    t0 = 1_000_000.0
    a = tr.add(_trial(), "K", t0, 30)
    b = tr.add(_trial(direction="down", entry=100, stop=101, target=98), "K", t0, 30)
    c = tr.add(_trial(sym="BANKNIFTY", entry=100, stop=99, target=102), "B", t0, 30)
    assert tr.add(_trial(), "K", t0 + 60, 30) is None                     # same idea inside its cooldown
    s = _State(K=_I(101.0), B=_I(100.5))
    tr.update(s, t0 + 60)
    s["K"] = _I(102.2)                                                    # up trial hits 2R; down trial hits its stop
    tr.update(s, t0 + 120)
    tr.update(s, t0 + 61 * 60)                                            # BANKNIFTY times out at +0.5R
    with st.lock:
        rows = {r["id"]: dict(r) for r in st.db.execute("SELECT * FROM trial_alerts").fetchall()}
    assert rows[a]["result"] == "hit" and rows[b]["result"] == "miss"
    assert rows[a]["r_exit"] == 2.0 and rows[b]["r_exit"] == -1.0          # no credit past the target (102.2 counts as 2R)
    assert rows[c]["result"] == "time" and rows[c]["r_exit"] == 0.5
    bd = board(st, "upstox")
    pin = next(k for k in bd["kinds"] if k["kind"] == "candle_pin_bar")
    assert pin["hits"] == 1 and pin["misses"] == 1 and pin["timed"] == 1
    assert {k["kind"] for k in bd["on_trial"]} == set(registry.shadow_kinds())


def test_engine_keeps_trials_out_of_everything_you_see(tmp_path, monkeypatch):
    from live.engine import LiveEngine
    e = LiveEngine(lambda: {"live": {}}, tmp_path)
    e.trials = Trials(e.store, "upstox")
    e.source = "upstox"
    sent = []
    monkeypatch.setattr(e.telegram, "send", lambda *a, **k: sent.append(a))
    now = dt.datetime(2026, 11, 4, 10, 0, tzinfo=IST)
    for k in registry.shadow_kinds():
        e._emit({**_trial(kind=k)}, now, e.cfg()["live"])
    assert not e.alerts_mem and not e.store.alerts() and not sent and not e.cool and not e.focus_last
    with e.store.lock:
        n = e.store.db.execute("SELECT COUNT(*) FROM trial_alerts").fetchone()[0]
    assert n == len(registry.shadow_kinds())


def test_shadow_kinds_are_never_auto_paper_traded():
    from live import paper
    assert not set(registry.shadow_kinds()) & set(paper.DEFAULTS["kinds"])


def test_every_candle_bullet_points_at_real_code():
    for name in registry.candle_spec():
        assert name in CDL.PATTERNS, name


def _pin_bar_case():
    # a fall, then a bullish pin bar whose long lower tail tests 100 (a key level) and closes near its high
    c = [110, 109, 108, 107, 106, 105, 104, 103, 102, 101.5]
    o = [x + 0.6 for x in c]
    h = [x + 0.9 for x in c]
    l = [x - 0.3 for x in c]
    o += [101.3]; h += [101.7]; l += [99.9]; c += [101.6]
    return [np.array(x, float) for x in (o, h, l, c)]


def test_trade_ideas_needs_the_level_when_the_registry_says_so():
    o, h, l, c = _pin_bar_case()
    spec = {"pin_bar": {"at_level": True, "bars": 1}}
    near = CDL.trade_ideas(o, h, l, c, [100.0], spec, last_only=True)
    far = CDL.trade_ideas(o, h, l, c, [80.0], spec, last_only=True)
    above = CDL.trade_ideas(o, h, l, c, [103.0], spec, last_only=True)        # level above a bullish candle: wrong side
    assert len(near) == 1 and not far and not above
    x = near[0]
    assert x["d"] == 1 and x["stop"] == 99.9 and abs(x["target"] - (x["entry"] + 2 * (x["entry"] - 99.9))) < 1e-9
    free = CDL.trade_ideas(o, h, l, c, [80.0], {"pin_bar": {"at_level": False, "bars": 1}}, last_only=True)
    assert len(free) == 1


def test_trade_ideas_survive_a_broken_bullet(monkeypatch):
    o, h, l, c = _pin_bar_case()
    bad = dict(CDL.PATTERNS)
    bad["engulfing"] = (lambda s: 1 / 0, "cdl_engulf", "broken")
    monkeypatch.setattr(CDL, "PATTERNS", bad)
    ideas = CDL.trade_ideas(o, h, l, c, [100.0], registry.candle_spec(), last_only=True)
    assert any(x["name"] == "pin_bar" for x in ideas)


def test_book_rules_in_the_library_are_valid():
    from lab import factory as F, library
    bk = [r for r in library.rules() if r["source"].startswith("book:")]
    assert len(bk) > 100
    for r in bk:
        assert F.validate(r), r
        for f, _, _ in r["conds"]:
            assert f in F.FEATURES, f


def test_a_held_back_lab_rule_is_still_measured_on_the_bench_only(tmp_path, monkeypatch):
    """2.68.1: two different Strategy Lab rules, same symbol and side, inside the feed's cooldown: the second one adds
    nothing to the feed, Telegram, Focus or paper, but the test bench still records it under its own rule id."""
    from live.engine import LiveEngine
    e = LiveEngine(lambda: {"live": {}}, tmp_path)
    e.source = "upstox"
    got, sent = [], []
    e.bench = object()
    monkeypatch.setattr(e, "bench_signal", lambda *a, **k: got.append(a) or 1)
    monkeypatch.setattr(e.telegram, "send", lambda *a, **k: sent.append(a))
    now = dt.datetime(2026, 11, 4, 10, 0, tzinfo=IST)
    key = ("NIFTY", "factory", "up")
    e.cool[key] = (now.timestamp() - 60, 3, "S1111aaaa")
    fa = lambda rid: {"sym": "NIFTY", "kind": "factory", "direction": "up", "severity": 3, "title": f"rule {rid}",
                      "data": {"factory": {"id": rid}}}
    e._emit(fa("S2222bbbb"), now, e.cfg()["live"])
    e._emit(fa("S1111aaaa"), now, e.cfg()["live"])          # the same rule again: its own repeat stays quiet
    assert [g[4] for g in got] == ["S2222bbbb"] and got[0][:2] == ("NIFTY", "up")
    assert not e.alerts_mem and not e.store.alerts() and not sent and not e.focus_last
    assert e.cool[key][2] == "S1111aaaa"
    e.cool[key] = (now.timestamp() - 60, 3)                 # a cooldown written before 2.68.1 (no rule id) still works
    e._emit(fa("S3333cccc"), now, e.cfg()["live"])
    assert len(got) == 1
