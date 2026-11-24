"""3.08 Stockway Pro v2 (Mandeep, 25 Nov 2026: "okay apne suggestions ke saath V2 banao pura ek sath complete"):
the chart-level exit (lab/chart_exit.py), the Vote Lab (live/provotes.py), Pro v2's entry checks and ranking
(live/pro2.py), the 09:20-09:30 early breakout (live/early.py) and the three paper accounts. v1 is the control and must
not change."""
import json

import numpy as np

from lab import chart_exit as X
from live import books as B
from live import early as E
from live import pro, pro2, provotes, registry
from live.store import Store
from tests.test_books import MAIN, Inst, _ms, _t, DAY

T0 = _t(DAY, 10, 0).timestamp()


def _plan(role="solo", runner="bar5", t1=102.0, t2=104.0, level=99.5, **c):
    return X.ChartPlan(100.0, 1, 99.0, 1.0, 50.0, T0, t1, t2, level, role, {**X.DEFAULT, "runner": runner, **c})


def _feed(p, path, opt=None, step=60):
    """One tick a minute from 10:01; returns (reason, tick index) of the first exit."""
    for i, s in enumerate(path):
        why = p.check(s, opt if opt is not None else 50.0 + (s - 100.0) * 5, T0 + step * (i + 1))
        if why:
            return why, i
    return None, None


# ---------------------------------------------------------------------- the chart-level exit
def test_targets_are_the_next_zones_at_least_a_quarter_atr_away():
    named = [("Yesterday's high", 100.1), ("R1", 101.0), ("Last week's high", 103.0)]
    assert X.targets(100.0, 1, 1.0, 99.0, named) == (101.0, 103.0)          # 100.1 is closer than 0.25 ATR: skipped
    t1, t2 = X.targets(100.0, 1, 1.0, 99.0, [])                               # no level: 2R, then as far again
    assert (t1, t2) == (102.0, 104.0)
    assert X.targets(100.0, -1, 1.0, 101.0, [("S1", 98.5), ("Yesterday's low", 97.0)]) == (98.5, 97.0)


def test_named_levels_use_finished_days_and_closed_bars_only():
    daily = {"prev": {"h": 110.0, "l": 100.0, "c": 105.0},
             "d_ohlc": [[f"2026-10-{d:02d}", 1, 100 + d, 90 + d, 95 + d, 0] for d in range(1, 26)] + [[DAY, 1, 999, 1, 5, 0]]}
    bars = {"t": [T0 - 900, T0 - 600, T0 - 300, T0], "h": [101, 102, 103, 150], "l": [99, 98, 97, 50]}
    nm = dict(X.named_levels(daily, bars, T0, DAY))
    assert nm["Yesterday's high"] == 110.0 and round(nm["R1"], 2) == 110.0 and nm["Last week's high"] == 125.0
    assert nm["20-day high"] == 125.0                                           # today's row (999) is not a finished day
    assert max(v for k, v in X.named_levels(daily, bars, T0, DAY) if k == "Day high") == 103.0   # the forming bar is out


def test_stop_is_on_a_close_and_failed_breakout_before_t1():
    p = _plan(level=None)
    assert _feed(p, [99.6, 98.5, 99.4, 99.5, 99.6]) == (None, None)            # a wick to 98.5 below the 99 stop: not out
    p = _plan(level=None)
    assert _feed(p, [99.5, 99.2, 98.9, 98.8, 98.7, 98.8])[0] == "stop"          # the 5-minute close below 99
    p = _plan()                                                                # level 99.5: a close back below it
    assert _feed(p, [99.8, 99.6, 99.4, 99.3, 99.2, 99.3])[0] == "failed"
    p = _plan(stop_on="1m")                                                    # the early trades: 1-minute closes
    assert _feed(p, [99.8, 99.3, 99.2]) == ("failed", 2)


def test_no_move_after_six_bars_on_the_wrong_side():
    p = _plan(level=None, nomove_bars=2)
    path = [100.1] * 5 + [99.9] * 5 + [99.8]
    assert _feed(p, path)[0] == "no move"


def test_two_lots_sell_one_at_t1_and_the_runner_trails():
    t1 = _plan(role="t1")
    assert _feed(t1, [100.5, 101.0, 102.1]) == ("t1", 2)
    run = _plan(role="run")
    why, i = _feed(run, [100.5, 101.0, 102.1, 102.5, 103.0, 103.5, 103.8, 104.1])
    assert why == "t2" and run.hit1 and run.stop >= 100.0                     # stop at the entry level, sold at T2
    ch = _plan(role="run", runner="chand", chand_atr=1.0)
    why, _ = _feed(ch, [100.5, 102.1, 103.0, 105.0, 106.0, 104.9])             # no T2: trails 1 ATR behind the best
    assert why == "trail" and ch.stop == 105.0
    em = _plan(role="run", runner="ema10")
    _feed(em, [100.5, 102.1] + [102.5 + 0.1 * k for k in range(20)])
    assert em.hit1 and em.stop > 100.0 and em.ema is not None                 # follows the 10 EMA of 5-minute closes


def test_emergency_lock_time_and_round_trip():
    p = _plan()
    assert p.check(100.2, 32.0, T0 + 10) == "premium stop"                     # -36% on the premium, any tick
    lk = _plan(lock_premium_pct=20, lock_keep_pct=5)
    assert lk.check(100.5, 61.0, T0 + 10) is None and lk.check(100.4, 52.0, T0 + 20) == "profit lock"
    two = _plan(role="run", lock_premium_pct=20, lock_keep_pct=5)                # the lock is for a 1-lot trade only
    assert two.check(100.5, 61.0, T0 + 10) is None and two.check(100.4, 52.0, T0 + 20) is None
    late = _plan()
    assert late.check(100.2, 50.0, _t(DAY, 15, 10).timestamp()) == "time"
    q = X.ChartPlan.from_dict(json.loads(json.dumps(_plan(role="run").to_dict())))
    assert q.role == "run" and q.t1 == 102.0 and q.c["runner"] == "bar5"
    assert set(X.REASONS) >= {"stop", "failed", "no move", "t1", "t2", "trail", "premium stop", "profit lock", "time"}


# ---------------------------------------------------------------------- the Vote Lab
def _bars(c, o=None, h=None, l=None, v=None):
    c = np.asarray(c, float)
    o = np.asarray(o if o is not None else np.r_[c[0], c[:-1]], float)
    h = np.asarray(h if h is not None else np.maximum(o, c) + 0.1, float)
    l = np.asarray(l if l is not None else np.minimum(o, c) - 0.1, float)
    return {"o": o, "h": h, "l": l, "c": c, "v": np.asarray(v if v is not None else np.ones(len(c)), float)}


def test_votes_registry_and_stretch_and_pause():
    nm = provotes.names()
    assert {"as_vwap2_stretch", "gh_pause", "ema_stack_5_10", "cd_engulf", "as_supertrend"} <= set(nm)
    assert {v["source"] for v in nm.values()} == {"EMA videos", "Ghanshyam", "Aseem 51 + PDFs", "Candles"}
    flat = [100.0] * 10 + [100.05, 100.1, 100.0, 100.05]
    stretched = provotes.compute(_bars(flat + [103.0]), 1, 100.2, 1.0, {"h": 101, "l": 98, "c": 100})
    assert stretched["as_vwap2_stretch"] == -1                                  # far above VWAP + 2 sigma
    calm = provotes.compute(_bars([99.0, 101.0, 99.0, 101.0, 99.0, 101.0, 100.6]), 1, 100.5, 1.0)
    assert calm["as_vwap2_stretch"] == 0
    pause = provotes.compute(_bars([99.0, 99.5, 100.3, 100.35, 100.4, 100.45, 100.9]), 1, 100.5, 1.0)
    assert pause["gh_pause"] == 1                                                # closes just under the level, then the break
    assert provotes.compute(_bars([100.0]), 1, 100.0, 1.0) == {}                # too few bars: no view
    assert provotes.compute(_bars([1.0, 2.0, 3.0]), 1, 2.0, None) == {}         # no ATR: no view


def test_votes_never_change_the_pro_score():
    base = {"breakout": 1, "trend": 1, "vwap": 1, "structure": 1, "flow": -1}
    w = {"breakout": 1.0, "trend": 1.0, "vwap": 1.0, "structure": 1.0, "flow": 1.0}
    x = provotes.as_pro_votes({"as_vwap2_stretch": -1, "gh_pause": 1, "_cpr_w": 0.3})
    assert x == {"x_as_vwap2_stretch": -1, "x_gh_pause": 1}
    assert pro.score({**base, **x}, w) == pro.score(base, w)


# ---------------------------------------------------------------------- Pro v2's entry checks
def _sig(sym="S1", seg="stock", score=90.0, votes=None, trigger="day high", stop=495.0, key="O1", d="up"):
    w = ["breakout", "trend", "strength", "structure"] if seg == "stock" else ["breakout", "trend", "structure"]
    return {"sym": sym, "kind": "trend_rider", "severity": 2, "direction": d, "title": f"{sym} Trend Rider", "und_key": "UND",
            "price": 500.0, "is_index": seg == "index",
            "option": {"key": key, "label": f"{sym} 500 CE", "side": "CE", "lot": 100},
            "chart_named": [("R1", 504.0), ("Last week's high", 510.0)],
            "data": {"rider": {"stop": stop, "atr": 2.0, "level": 499.0, "trigger": trigger, "ema10": 498.0},
                     "pro": {"score": score, "label": "strong", "groups": ["a", "b", "c"], "seg": seg, "with": w,
                             "against": [], "votes": votes or {}}}}


def _main_pass(st, sym, ts, d="up", verdict="PASS"):
    st.add_alert({"ts": ts, "day": DAY, "source": "upstox", "sym": sym, "kind": "volume_surge", "direction": d,
                  "severity": 3, "title": "t", "detail": "", "price": 500.0, "data": {"risk": {"verdict": verdict}}})


def test_stocks_need_main_pass_and_no_stretch_index_does_not(tmp_path):
    st = Store(tmp_path / "t.db")
    now = _t(DAY, 10, 0)
    pick = {"rider": True, "pro": True, "v2": True, "segment": "stock", "need_with": ["strength"], "not_with": ["flow"]}
    assert pro2.check(pick, _sig(), now, st, "upstox#pro2_stocks", "upstox").startswith("not confirmed")
    _main_pass(st, "S1", now.timestamp() - 600, verdict="BLOCK")
    assert pro2.check(pick, _sig(), now, st, "upstox#pro2_stocks", "upstox").startswith("not confirmed")
    _main_pass(st, "S1", now.timestamp() - 1200)                                 # 20 minutes ago: too old
    assert pro2.check(pick, _sig(), now, st, "upstox#pro2_stocks", "upstox").startswith("not confirmed")
    _main_pass(st, "S1", now.timestamp() - 600)
    assert pro2.check(pick, _sig(), now, st, "upstox#pro2_stocks", "upstox") is None
    assert "VWAP" in pro2.check(pick, _sig(votes={"x_as_vwap2_stretch": -1}), now, st, "upstox#pro2_stocks", "upstox")
    ix = {"rider": True, "pro": True, "v2": True, "segment": "index"}
    assert pro2.check(ix, _sig(sym="NIFTY", seg="index", votes={"x_as_vwap2_stretch": -1}), now, st, "upstox#pro2_index", "upstox") is None
    flow = _sig(); flow["data"]["pro"]["with"] = flow["data"]["pro"]["with"] + ["flow"]
    assert "flow" in pro2.check(pick, flow, now, st, "upstox#pro2_stocks", "upstox")       # strict: flow already with it
    mm = {**pick, "midmorning": {"on": True}}
    late = _t(DAY, 10, 15)
    _main_pass(st, "S1", late.timestamp() - 60)
    assert "fresh break" in pro2.check(mm, _sig(trigger="retest of opening range"), late, st, "upstox#x", "upstox")
    assert pro2.check(mm, _sig(trigger="day high"), late, st, "upstox#x", "upstox") is None
    assert pro2.rank(_sig(votes={"x_gh_pause": 1})) == 95.0 and pro2.rank(_sig()) == 90.0


def test_v2_accounts_rank_a_bar_and_open_two_parts_with_the_chart_exit(tmp_path):
    st = Store(tmp_path / "t.db")
    bs = B.Books("upstox", st, dict(MAIN), None, log=lambda m: None)
    assert {"pro2_index", "pro2_stocks", "pro2_stocks_rider", "pro2_early"} <= set(bs.books) and bs.has_chart() and bs.has_early()
    bs.specs["pro2_stocks"] = {**bs.specs["pro2_stocks"], "exit": {"runner": "ema10", "split_at_t1": True}}   # the two-part way
    bs.rank_ok = True
    now = _t(DAY, 10, 0)
    for s in ("S1", "S2"):
        _main_pass(st, s, now.timestamp() - 300)
    state = {"O1": Inst(10.0, _ms(DAY, 9, 59)), "O2": Inst(10.0, _ms(DAY, 9, 59)), "UND": Inst(500.0, _ms(DAY, 9, 59))}
    a1, a2 = _sig("S1", score=86.0, key="O1"), _sig("S2", score=92.0, key="O2")
    assert bs.offer_rider(a1, state, now)["pro2_stocks"] == "queued"
    assert bs.offer_rider(a2, state, now)["pro2_stocks"] == "queued"
    assert st.trades(source="upstox#pro2_stocks") == []                         # nothing before the bar's flush
    out = bs.flush_rider(state, now)
    assert [x[0] for x in out["pro2_stocks"]] == ["S2", "S1"]                    # the better score first
    tr = sorted(st.trades(source="upstox#pro2_stocks"), key=lambda t: (t["sym"], t["mode"]))
    assert [(t["sym"], t["mode"]) for t in tr] == [("S1", "leg:run"), ("S1", "leg:t1"), ("S2", "leg:run"), ("S2", "leg:t1")]
    pl = json.loads(tr[1]["plan"])
    assert pl["style"] == "chart" and pl["role"] == "t1" and pl["t1"] == 504.0 and pl["t2"] == 510.0 and pl["stop"] == 495.0
    assert json.loads(tr[0]["plan"])["role"] == "run" and tr[0]["hold_max"] == 400
    assert bs.pending == {}
    one = st.trades(source="upstox#pro2_stocks_rider")                      # the challenger: Trend Rider's exit, 2 lots
    assert len(one) == 2 and {t["lots"] for t in one} == {2} and {json.loads(t["plan"])["style"] for t in one} == {"rider"}


def test_all_lots_ride_by_default(tmp_path):
    st = Store(tmp_path / "t.db")
    bs = B.Books("upstox", st, dict(MAIN), None, log=lambda m: None)
    now = _t(DAY, 10, 0)
    _main_pass(st, "S1", now.timestamp() - 300)
    state = {"O1": Inst(10.0, _ms(DAY, 9, 59)), "UND": Inst(500.0, _ms(DAY, 9, 59))}
    assert bs.offer_rider(_sig("S1", key="O1"), state, now)["pro2_stocks"] == "taken"
    tr = st.trades(source="upstox#pro2_stocks")
    p = json.loads(tr[0]["plan"])
    assert len(tr) == 1 and tr[0]["lots"] == 2 and p["role"] == "solo" and p["c"]["runner"] == "ema10" and not p["c"]["split_at_t1"]


def test_one_lot_when_the_money_covers_only_one(tmp_path):
    st = Store(tmp_path / "t.db")
    bs = B.Books("upstox", st, {**MAIN, "capital": 1500}, None, log=lambda m: None)
    now = _t(DAY, 10, 0)
    state = {"O1": Inst(10.0, _ms(DAY, 9, 59)), "UND": Inst(500.0, _ms(DAY, 9, 59))}
    out = bs.offer_rider(_sig("NIFTY", seg="index"), state, now)               # rank_ok off: opened at once
    assert out["pro2_index"] == "taken"
    tr = st.trades(source="upstox#pro2_index")
    assert len(tr) == 1 and tr[0]["lots"] == 1 and json.loads(tr[0]["plan"])["role"] == "solo"
    assert out["pro_index"] == "taken"                                         # v1 (the control) still takes it its own way
    assert json.loads(st.trades(source="upstox#pro_index")[0]["plan"])["style"] == "rider"


def test_chart_trade_part_sells_at_t1_in_the_paper_book(tmp_path):
    st = Store(tmp_path / "t.db")
    bs = B.Books("upstox", st, dict(MAIN), None, log=lambda m: None)
    bs.specs["pro2_index"] = {**bs.specs["pro2_index"], "exit": {"split_at_t1": True}, "lots": {"want": 2}}
    now = _t(DAY, 10, 0)
    state = {"O1": Inst(10.0, _ms(DAY, 9, 59)), "UND": Inst(500.0, _ms(DAY, 9, 59))}
    bs.offer_rider(_sig("NIFTY", seg="index"), state, now)
    bk = bs.books["pro2_index"]
    state = {"O1": Inst(13.0, _ms(DAY, 10, 2)), "UND": Inst(504.5, _ms(DAY, 10, 2))}
    closed = bk.update(state, _t(DAY, 10, 2))
    assert [(c["reason"]) for c in closed] == ["target 1"]
    left = bk.open_trades()
    assert len(left) == 1 and json.loads(left[0]["plan"])["hit1"] and json.loads(left[0]["plan"])["stop"] >= 500.0


# ---------------------------------------------------------------------- the 09:20-09:30 early breakout
def _rows(closes, first=(100.0, 101.0, 99.8, 100.9), vol=1.0):
    """1-minute bars from 09:15: the first five make the first 5-minute candle (o, h, l, c), then `closes`."""
    o1, h1, l1, c1 = first
    t0 = _t(DAY, 9, 15).timestamp()
    fc = [o1 + (c1 - o1) * k / 4 for k in range(5)]
    rows = []
    for k, c in enumerate(fc):
        rows.append([t0 + 60 * k, fc[k - 1] if k else o1, h1 if k == 3 else max(c, fc[k - 1] if k else o1) + 0.01,
                     l1 if k == 1 else min(c, fc[k - 1] if k else o1) - 0.01, c, vol])
    p = c1
    for j, c in enumerate(closes):
        rows.append([t0 + 60 * (5 + j), p, max(p, c) + 0.01, min(p, c) - 0.01, c, vol])
        p = c
    return np.asarray(rows, float)


def test_early_breakout_needs_a_fresh_break_through_a_level():
    c = {**pro2.cfg()["early"], "level_names": []}
    lv = [("Yesterday's high", 101.2)]
    prev = {"h": 101.2, "l": 98.0, "c": 99.5}
    j = E.judge(_rows([101.0, 101.4]), 1, prev, lv, c, idx_move=0.3, flow=0.4,
                opt={"ltp": 50.0, "bid": 49.8, "ask": 50.2}, datr=2.0)
    assert j and j["level"] == 101.0 and j["through"] == "Yesterday's high" and j["stop"] == 100.4
    assert j["label"] == "strong" and all(j["checks"].values())
    assert E.judge(_rows([101.0, 101.4]), 1, prev, [("R1", 103.0)], c) is None          # no level crossed
    assert E.judge(_rows([101.3, 101.4]), 1, prev, lv, c) is None                       # broke a minute earlier: not fresh
    assert E.judge(_rows([101.3, 101.4]), 1, prev, lv, c, tf="5m") is not None          # the 5-minute trigger: still beyond
    weak = E.judge(_rows([101.0, 101.4]), 1, prev, lv, c, idx_move=-0.5, flow=-0.2, datr=2.0)
    assert weak and weak["label"] == "weak"


def test_early_account_takes_strong_1m_signals_only(tmp_path):
    st = Store(tmp_path / "t.db")
    bs = B.Books("upstox", st, dict(MAIN), None, log=lambda m: None)
    now = _t(DAY, 9, 22)
    state = {"O1": Inst(10.0, _ms(DAY, 9, 21)), "UND": Inst(500.0, _ms(DAY, 9, 21))}
    def sig(tf="1m", label="strong"):
        return {"sym": "S1", "kind": "early_breakout", "severity": 2, "direction": "up", "title": "S1 Early", "und_key": "UND",
                "price": 500.0, "is_index": False, "chart_named": [],
                "option": {"key": "O1", "label": "S1 500 CE", "side": "CE", "lot": 100},
                "data": {"early": {"tf": tf, "label": label, "score": 90.0, "stop": 498.0, "atr": 1.5, "level": 499.0}}}
    assert bs.offer_early(sig(tf="5m"), state, now)["pro2_early"].startswith("a 5m trigger")
    assert bs.offer_early(sig(label="weak"), state, now)["pro2_early"].startswith("early score")
    assert bs.offer_early(sig(), state, now)["pro2_early"] == "taken"
    t = st.trades(source="upstox#pro2_early")[0]
    p = json.loads(t["plan"])
    assert t["lots"] == 1 and p["role"] == "solo" and p["c"]["stop_on"] == "1m" and p["stop"] == 498.0
    # the stocks account does not buy the same stock and side again that day
    _main_pass(st, "S1", now.timestamp())
    pick = (B.cfg()["books"]["pro2_stocks"])["pick"]
    assert pro2.check(pick, _sig(), _t(DAY, 9, 35), st, "upstox#pro2_stocks", "upstox") == "already bought in the early window today"


def test_registry_rules_and_accounts():
    assert registry.is_shadow("early_breakout") and registry.is_shadow("trend_rider")
    c = pro2.cfg()
    assert c["confirm_main"]["stock"] and not c["confirm_main"]["index"] and c["no_stretch"]["vote"] == "x_as_vwap2_stretch"
    assert c["exit"]["runner"] in X.RUNNERS and c["early"]["from"] == "09:20"
    bk = B.cfg()["books"]
    for k in ("pro2_index", "pro2_stocks", "pro2_early"):
        assert bk[k]["exit_style"] == "chart" and bk[k]["section"] == "pro" and bk[k]["capital"] is None
    assert bk["pro2_stocks"]["exit"]["runner"] == "ema10" and bk["pro2_index"]["lots"]["want"] == 1
    assert bk["pro2_stocks_rider"]["exit_style"] == "rider" and bk["pro2_stocks_rider"]["pick"] == bk["pro2_stocks"]["pick"]
    for k in ("pro_index", "pro_stocks", "pro_stocks_strict"):                   # v1 untouched: the control
        assert bk[k]["exit_style"] == "rider" and not bk[k]["pick"].get("v2")


def test_v2_account_with_the_rider_exit_takes_the_lots_on_one_trade(tmp_path):
    import copy
    st = Store(tmp_path / "t.db")
    bs = B.Books("upstox", st, dict(MAIN), None, log=lambda m: None)
    sp = copy.deepcopy(bs.specs["pro2_index"])
    sp.update(exit_style="rider", lots={"want": 2})
    sp.pop("exit", None)
    bs.specs["pro2_index"] = sp
    bs.books["pro2_index"].cfg["max_lots"] = 2
    now = _t(DAY, 10, 0)
    state = {"O1": Inst(10.0, _ms(DAY, 9, 59)), "UND": Inst(500.0, _ms(DAY, 9, 59))}
    assert bs.offer_rider(_sig("NIFTY", seg="index"), state, now)["pro2_index"] == "taken"
    tr = st.trades(source="upstox#pro2_index")
    assert len(tr) == 1 and tr[0]["lots"] == 2 and json.loads(tr[0]["plan"])["style"] == "rider"


def test_early_musts_make_a_high_score_weak():
    c = {**pro2.cfg()["early"], "level_names": [], "min_score": 50}
    prev = {"h": 101.2, "l": 98.0, "c": 101.5}                                  # opened below y'day's close: a gap down
    j = E.judge(_rows([101.0, 101.4]), 1, prev, [("Yesterday's high", 101.2)], c, idx_move=0.3, flow=0.4,
                opt={"ltp": 50.0, "bid": 49.8, "ask": 50.2}, datr=2.0)
    assert j["checks"]["gap_same_way"] is False and j["score"] >= 50 and j["label"] == "weak" and j["missing"] == ["gap_same_way"]


def test_system_page_check():
    from live import selfcheck
    r = [x for x in selfcheck.static_checks() if x["id"] == "pro_v2"][0]
    assert r["ok"], r["detail"]


def test_pro_page_v2_segments(tmp_path, monkeypatch):
    import datetime as dt
    import types
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from webapp import live_api, memo
    st = Store(tmp_path / "t.db")
    from live.trials import Trials
    Trials(st, "upstox", log=lambda m: None)
    t0 = _t("2026-11-25", 9, 30).timestamp()
    pro_s = {"label": "strong", "score": 90, "seg": "stock", "groups": ["a", "b", "c"], "with": ["breakout", "strength"],
             "against": [], "votes": {"breakout": 1, "strength": 1, "x_gh_pause": 1}}
    with st.lock:
        for sym, kind, data in (("TCS", "trend_rider", {"pro": pro_s, "rider": {"trigger": "day high", "stop": 1.0}}),
                                ("INFY", "trend_rider", {"pro": {**pro_s, "votes": {"x_as_vwap2_stretch": -1}}, "rider": {}}),
                                ("SBIN", "early_breakout", {"early": {"tf": "1m", "label": "strong", "score": 100.0, "through": "R1",
                                                                     "checks": {"first_candle": True, "no_chase": True}, "stop": 1.0}})):
            st.db.execute("INSERT INTO trial_alerts(ts, day, source, sym, kind, direction, data) VALUES (?,?,?,?,?,?,?)",
                          (t0, "2026-11-25", "upstox", sym, kind, "up", json.dumps(data)))
        st.db.commit()
    _main_pass(st, "TCS", t0 - 300)
    _main_pass(st, "INFY", t0 - 300)
    st.add_trade({"source": "upstox#pro2_stocks", "day": "2026-11-25", "sym": "TCS", "status": "closed", "net": 900.0,
                  "entry": 10.0, "exit": 19.0, "reason": "target 1", "mode": "leg:t1"})
    e = types.SimpleNamespace(store=st, data_dir=tmp_path, source="upstox", _real_source=lambda: "upstox",
                              session_day=lambda: dt.date(2026, 11, 25))
    monkeypatch.setattr(live_api, "engine", lambda: e)
    for k in list(memo._mem):
        memo._mem.pop(k, None)
    app = FastAPI()
    app.include_router(live_api.router)
    c = TestClient(app)
    d = c.get("/api/live/pro?seg=v2stock&day=2026-11-25").json()
    assert d["book"] == "pro2_stocks" and [x["sym"] for x in d["signals"]["strong"]] == ["TCS"]
    assert [x["sym"] for x in d["signals"]["medium"]] == ["INFY"] and "VWAP" in d["signals"]["medium"][0]["gate"]
    assert d["v2"]["stocks"].startswith("strong") and "gh_pause" in d["votes"]
    cmp = {x["acct"]: x for x in d["compare"]}
    assert cmp["pro2_stocks"]["net"] == 900 and cmp["pro2_stocks"]["green"] == 1 and "trail" in cmp and "main" in cmp
    ea = c.get("/api/live/pro?seg=v2early&day=2026-11-25").json()
    assert [x["sym"] for x in ea["signals"]["strong"]] == ["SBIN"] and ea["book"] == "pro2_early"
    assert c.get("/api/live/pro?seg=v3").status_code == 400
    js = open("webapp/static/live.js").read()
    assert all(k in js for k in ('v2index: { acct: "pro2_index"', 'v2stock: { acct: "pro2_stocks"', 'v2early: { acct: "pro2_early"',
                                 "function proV2(d)", "Vote Lab"))
