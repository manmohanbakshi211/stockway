"""2.85: Trend Rider's exit (lab/rider_exit.py) and its own paper account (rules/paper_books.json "rider").

Mandeep, 22 Nov 2026: "loss less ho, time limit tak sirf premium na ghista rahe agar koi movement ka chance nahi hai
toh, reversal ho toh trail humara profit bachaye rakhe but ... ye nahi ki sl hit karwake aage ka achha move na le
paaye. trade ka initial stoploss bhi achhi strong or confident ho." 1 lot, the capital chosen in the app."""
import datetime as dt
import inspect
import json

from lab import rider_exit as X
from live import books as B
from live.clock import IST
from live.store import Store
from tests.test_books import MAIN, Inst, _ms, _t, DAY

T0 = _t(DAY, 10, 0).timestamp()            # 10:00, the start of a 5-minute bar


def _plan(d=1, s0=100.0, stop=98.0, atr=1.0, entry=50.0, **c):
    return X.RiderPlan(s0, d, stop, atr, entry, T0, {**X.DEFAULT, **c})


def _bar(p, k, o, h, l, c, opt=None):
    """Feed one 5-minute bar (index k after 10:00) as four ticks, then the first tick of the next bar closes it."""
    base = T0 + 300 * k
    why = None
    for i, px in enumerate((o, h, l, c)):
        why = why or p.check(px, opt, base + 10 + 60 * i)
    return why or p.check(c, opt, base + 300 + 1)


def test_the_stop_is_on_a_close_not_on_a_wick():
    p = _plan()
    assert _bar(p, 0, 100.0, 100.5, 97.0, 99.0) is None          # wick to 97 below the 98 stop: not out
    assert _bar(p, 1, 99.0, 99.2, 97.5, 97.8) == "stop"            # a close below 98: out


def test_lock_moves_the_stop_to_entry_once_the_move_reaches_one_r():
    p = _plan()
    assert _bar(p, 0, 100.0, 102.2, 99.8, 102.0) is None          # best 102.2 >= +1R (2 points)
    assert p.locked and p.stop >= 100.0                            # a winner cannot become a loser
    assert _bar(p, 1, 102.0, 102.1, 99.9, 100.0) == "trail"       # back to entry on a close: out at about flat


def test_the_trail_stays_loose_on_a_pullback_and_tightens_on_reversal_signs():
    p = _plan(nomove_bars=99)
    for k, (o, h, l, c) in enumerate([(100, 102.5, 99.9, 102.3), (102.3, 104.0, 102.0, 103.8), (103.8, 106.0, 103.5, 105.8)]):
        assert _bar(p, k, o, h, l, c) is None
    assert p.locked and not p.tight and abs(p.stop - (106.0 - 2.5)) < 1e-9      # loose: 2.5 ATR behind the best
    assert _bar(p, 3, 105.8, 105.9, 104.0, 104.2) is None          # an ordinary pullback: still in, still loose
    assert not p.tight
    # the next close is lower again AND below the previous bar's low: two reversal signs -> the trail tightens to
    # 1.5 ATR (106 - 1.5 = 104.5) and the 103.8 close is out; the loose 103.5 would have given the profit back
    assert _bar(p, 4, 104.2, 104.3, 103.6, 103.8) == "trail"
    assert p.tight and abs(p.stop - 104.5) < 1e-9


def test_no_move_gets_out_before_the_premium_melts():
    p = _plan()
    for k in range(2):
        assert _bar(p, k, 100.0, 100.3, 99.6, 100.1, opt=49.0) is None
    assert _bar(p, 2, 100.1, 100.3, 99.7, 100.2, opt=48.0) == "no move"      # 3 bars, under 0.3 ATR, option below entry
    q = _plan()
    for k in range(2):
        _bar(q, k, 100.0, 100.3, 99.6, 100.1, opt=49.0)
    assert _bar(q, 2, 100.1, 100.6, 99.9, 100.5, opt=52.0) is None         # it moved: stays


def test_emergency_premium_stop_and_time_exit():
    p = _plan()
    assert p.check(100.0, 32.0, T0 + 30) == "premium stop"          # -36% on the option, any tick
    late = _t(DAY, 15, 10).timestamp()
    q = X.RiderPlan(100.0, 1, 98.0, 1.0, 50.0, late - 600, dict(X.DEFAULT))
    assert q.check(100.2, 50.0, late + 1) == "time"


def test_a_put_trade_mirrors_the_rules():
    p = _plan(d=-1, s0=100.0, stop=102.0)
    assert _bar(p, 0, 100.0, 103.0, 99.5, 101.0) is None           # wick above the stop: not out
    assert _bar(p, 1, 101.0, 101.0, 97.6, 97.8) is None             # -2.2 points: +1R for a put
    assert p.locked and p.stop <= 100.0


def test_plan_round_trip_keeps_state():
    p = _plan()
    _bar(p, 0, 100.0, 102.2, 99.8, 102.0)
    q = X.RiderPlan.from_dict(json.loads(json.dumps(p.to_dict())))
    assert q.to_dict() == p.to_dict() and q.locked and q.levels()["locked"]


def test_exit_numbers_come_from_the_rules_file():
    c = X.exit_cfg()
    assert c["lock_r"] == 1.0 and c["emergency_premium_pct"] == 35.0 and c["exit_hhmm"] == "15:10"
    from lab import rider as R
    assert R.cfg()["cooldown_min"] == 15 and R.cfg()["max_signals_per_symbol_day"] == 2     # second signal = re-entry


# ---------------------------------------------------------------------- the paper account
def _rider_alert(i=1, key="O1", stop=495.0, atr=2.0, price=500.0):
    return {"id": i, "sym": f"S{i}", "kind": "trend_rider", "severity": 2, "direction": "up", "title": "S Trend Rider",
            "und_key": "UND", "price": price,
            "option": {"key": key, "label": f"S{i} 500 CE", "side": "CE", "lot": 100},
            "data": {"rider": {"stop": stop, "atr": atr, "trigger": "day high"}}}


def _state(**px):
    ms = _ms(DAY, 9, 59)
    s = {k: Inst(v, ms) for k, v in px.items()}
    s.setdefault("UND", Inst(500.0, ms))
    return s


def test_rider_account_loads_and_takes_only_rider_signals(tmp_path):
    st = Store(tmp_path / "t.db")
    bs = B.Books("upstox", st, dict(MAIN), None, log=lambda m: None)
    sp = B.cfg()["books"]["rider"]
    assert sp["exit_style"] == "rider" and sp["pick"]["rider"] and sp.get("capital") is None    # the app's capital
    assert "rider" in bs.books and bs.has_rider() and bs.books["rider"].cfg["capital"] == MAIN["capital"]
    now = _t(DAY, 10, 0)
    out = bs.offer_rider(_rider_alert(), _state(O1=40.0), now)
    assert out == {"rider": "taken"}
    t = st.trades(source="upstox#rider")[0]
    plan = json.loads(t["plan"])
    assert plan["style"] == "rider" and plan["stop"] == 495.0 and plan["atr"] == 2.0 and t["lots"] == 1
    assert abs(t["stop"] - 40.0 * 0.65) < 0.01 and t["hold_max"] == 400
    assert "rider" not in bs.consider({**_rider_alert(i=2, key="O2"), "kind": "volume_surge"}, _state(O2=40.0), now)
    assert st.trades(source="upstox") == []                        # the main account never sees it


def test_rider_account_refuses_without_stop_or_when_price_is_through_it(tmp_path):
    st = Store(tmp_path / "t.db")
    bs = B.Books("upstox", st, dict(MAIN), None, log=lambda m: None)
    now = _t(DAY, 10, 0)
    assert bs.offer_rider(_rider_alert(stop=None), _state(O1=40.0), now)["rider"].startswith("no setup stop")
    assert bs.offer_rider({**_rider_alert(i=2, key="O2"), "und_key": None}, _state(O2=40.0), now)["rider"] \
        .startswith("no live price")
    s = _state(O3=40.0)
    s["UND"] = Inst(494.0, _ms(DAY, 9, 59))                         # spot already below the 495 stop
    assert bs.offer_rider(_rider_alert(i=3, key="O3"), s, now)["rider"] == "price already through the setup's stop"
    assert st.trades(source="upstox#rider") == []


def test_rider_trade_exits_on_its_own_rules_and_fast_checks_do_not_write_every_tick(tmp_path):
    st = Store(tmp_path / "t.db")
    bs = B.Books("upstox", st, dict(MAIN), None, log=lambda m: None)
    t0 = _t(DAY, 10, 0)
    bs.offer_rider(_rider_alert(), _state(O1=40.0), t0)
    bk = bs.books["rider"]
    writes = []
    real = st.update_trade
    st.update_trade = lambda tid, **kw: (writes.append(set(kw)), real(tid, **kw))[1]
    s = _state(O1=40.0)
    for sec in range(10, 280, 15):                                  # ticks inside the first bar, no close yet
        s["UND"].ltp = 500.0 + (sec % 3) * 0.2
        bk.update(s, t0 + dt.timedelta(seconds=sec), fast=True)
    assert not any("plan" in w for w in writes)                     # the bar in progress stays in memory
    s["UND"].ltp = 494.0                                            # a tick far below the stop, still inside bar 1
    bk.update(s, t0 + dt.timedelta(seconds=310), fast=True)        # closes bar 0 at ~500: not a stop
    assert bk.open_trades()
    bk.update(s, t0 + dt.timedelta(seconds=605), fast=True)        # bar 1 closes at 494 < 495: out
    tr = st.trades(source="upstox#rider")[0]
    assert tr["status"] == "closed" and tr["reason"] == "chart stop"


def test_engine_trial_hands_trend_rider_signals_to_the_account():
    from live.engine import LiveEngine
    src = inspect.getsource(LiveEngine._trial)
    assert "_rider_offer" in src and 'a.get("kind") == "trend_rider"' in src
    assert "offer_rider" in inspect.getsource(LiveEngine._rider_offer)


def test_rider_exit_style_needs_rider_signals(tmp_path, monkeypatch):
    bad = {"books": {"best": B.cfg()["books"]["best"],
                     "x": {**B.cfg()["books"]["rider"], "pick": {"focus": True}}}}
    monkeypatch.setattr(B, "cfg", lambda: bad)
    bs = B.Books("upstox", Store(tmp_path / "t.db"), dict(MAIN), None, log=lambda m: None)
    assert "x" not in bs.books and "pick.rider" in bs.errors["x"] and "best" in bs.books
