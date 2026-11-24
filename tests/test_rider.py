"""2.84 Trend Rider v1: the indicators (lab/rider.py), the decision, and the live scan (live/rider.py)."""
import datetime as dt
import json
import types

import numpy as np

from lab import rider as R
from live.rider import Rider


def _bars(closes, wick=0.4, vol=1000.0, start=1795000000.0):
    c = np.asarray(closes, float)
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) + wick
    l = np.minimum(o, c) - wick
    return {"t": start + 300 * np.arange(len(c)), "o": o, "h": h, "l": l, "c": c, "v": np.full(len(c), vol)}


def _trend_up(n=24):
    return _bars(100 + np.arange(n) * 1.0 + np.tile([0.0, 0.3, -0.2], n)[:n])


def _chop(n=24):
    return _bars(100 + np.tile([0.0, 1.0, 0.0, -1.0], n)[:n])


def test_trend_day_score_is_high_on_a_trend_and_low_in_a_chop():
    c = R.cfg()
    up, ch = _trend_up(), _chop()
    a_up, a_ch = R.atr(up["h"], up["l"], up["c"]), R.atr(ch["h"], ch["l"], ch["c"])
    t1 = R.trend_day(up, a_up, {}, c["trend_day"]["weights"])
    t2 = R.trend_day(ch, a_ch, {}, c["trend_day"]["weights"])
    assert t1["dir"] == 1 and t1["score"] > 60
    assert t2["score"] < 40 and t1["score"] > t2["score"] + 25


def test_a_fresh_break_of_the_day_high_is_found_and_graded():
    b = _bars([100, 100.5, 100.2, 100.6, 100.4, 100.5, 100.3, 101.8])
    a = R.atr(b["h"], b["l"], b["c"])
    bo = R.breakout(b, a, {"pdh": 104.0}, R.cfg()["breakout"])
    assert bo and bo["dir"] == 1 and bo["level_name"] in ("day high", "opening range") and 0 < bo["quality"] <= 100
    flat = _bars([100, 100.2, 100.1, 100.2, 100.1])
    assert R.breakout(flat, R.atr(flat["h"], flat["l"], flat["c"]), {}, R.cfg()["breakout"]) is None


def test_the_chase_meter_rises_when_the_move_is_stretched():
    calm = _bars([100, 100.4, 100.1, 100.5, 100.2, 100.6, 100.3, 100.7])
    run = _bars([100, 100.2, 100.1, 100.3, 101.5, 103, 105, 108], wick=0.2)
    a1, a2 = R.atr(calm["h"], calm["l"], calm["c"]), R.atr(run["h"], run["l"], run["c"])
    assert R.chase(run, a2, 1, {}) > R.chase(calm, a1, 1, {}) + 20


def test_move_vs_decay_uses_theta_spread_and_charges():
    opt = {"ltp": 100.0, "bid": 99.0, "ask": 101.0, "delta": 0.5, "theta": -30.0, "lot": 75}
    r = R.move_vs_decay(10.0, opt, 45, 60, 60.0)
    move = 10.0 * (45 / 5) ** 0.5 * (0.5 + 0.6)
    want = 0.5 * move / (30 * 45 / 375 + 1.0 + 60 / 75)
    assert abs(r - round(want, 3)) < 1e-9
    assert R.move_vs_decay(10.0, None, 45, 60, 60.0) is None


def test_tradability_blocks_wide_spreads_and_lots_beyond_the_capital():
    c = R.cfg()["tradability"]
    ok = R.tradability({"ltp": 100, "bid": 99.5, "ask": 100.5, "lot": 50}, {}, c, 500000)
    wide = R.tradability({"ltp": 100, "bid": 95, "ask": 105, "lot": 50}, {}, c, 500000)
    big = R.tradability({"ltp": 100, "bid": 99.5, "ask": 100.5, "lot": 50}, {}, c, 10000)
    ban = R.tradability({"ltp": 100, "bid": 99.5, "ask": 100.5, "lot": 50}, {"ban": True}, c, 500000)
    assert ok["score"] > 60 and wide["score"] < 20 and big["score"] == 0 and ban["score"] == 0


def test_the_decision_passes_a_clean_breakout_and_says_why_it_fails_otherwise():
    c = R.cfg()
    b = _bars(list(100 + np.arange(14) * 0.6) + [109.5], wick=0.3)
    opt = {"ltp": 120.0, "bid": 119.5, "ask": 120.5, "delta": 0.55, "theta": -20.0, "lot": 50}
    r = R.readings(b, {"pdh": 130.0}, c, None, opt, opt, 500000)
    d = R.decide(b, r, 40.0, c, opt)
    assert d["dir"] == 1 and d["stop"] is not None and d["stop"] < float(b["c"][-1])
    assert d["go"] or d["fails"]                                     # either it trades or it says why not
    late = R.decide(b, r, 300.0, c, opt)
    assert not late["go"] and "outside the time windows" in late["fails"]


def test_a_broken_settings_file_falls_back_to_the_defaults(tmp_path, monkeypatch):
    p = tmp_path / "buy_setup.json"
    p.write_text("{not json")
    monkeypatch.setattr(R, "RULES", p)
    assert R.cfg()["windows"] == R.DEFAULTS["windows"]
    p.write_text(json.dumps({"chase": {"max": 50}}))
    assert R.cfg()["chase"]["max"] == 50 and R.cfg()["chase"] is not R.DEFAULTS["chase"]


class _Charts:
    def __init__(self, bars):
        self.bars = bars

    def five_min(self, sym, day):
        return self.bars.get(sym)

    def context(self, sym, m):
        return {"pdh": 130.0, "pdl": 90.0}

    def _rate(self, key, s):
        return True


def _engine(bars):
    e = types.SimpleNamespace()
    e.charts = _Charts(bars)
    e.metrics = {s: {"spot": float(b["c"][-1]), "is_index": s == "NIFTY"} for s, b in bars.items()}
    e.daily, e.state, e.uni, e.paper = {}, None, None, None
    e.logs = []
    e.log = e.logs.append
    opt = {"key": "K", "label": "X 100 CE", "side": "CE", "strike": 100, "expiry": "2026-11-26", "lot": 50,
           "ltp": 120.0, "ask": 120.5, "iv": 0.15, "delta": 0.55, "cost_per_lot": 6025}
    e._suggest = lambda sym, d: dict(opt)
    return e


def test_the_live_scan_keeps_readings_and_series_and_respects_the_limits(monkeypatch):
    b = _bars(list(100 + np.arange(14) * 0.6) + [109.5], wick=0.3)
    e = _engine({"NIFTY": b, "SBIN": b})
    rd = Rider(e)
    monkeypatch.setattr(R, "decide", lambda bars, r, tod, c_=None, opt=None, **kw: {"go": True, "dir": 1, "trigger": "day high",
                                                                                "level": 108.0, "stop": 107.5, "risk_pct": 10.0,
                                                                                "fails": [], "why": ["test"]})
    got = []
    now = dt.datetime(2026, 11, 19, 10, 0, 5)
    assert rd.on_bar(now, len(b["c"]), got.append) == 2
    assert {a["sym"] for a in got} == {"NIFTY", "SBIN"} and all(a["kind"] == "trend_rider" for a in got)
    a = got[0]
    assert a["data"]["setup_levels"]["stop"] == 107.5 and a["data"]["setup_levels"]["target"] > a["price"]
    assert rd.read["SBIN"]["decision"]["go"] and len(rd.series["SBIN"]) == 13     # 3.07: 12 earlier bars read again
    assert rd.on_bar(now + dt.timedelta(minutes=5), len(b["c"]), got.append) == 0      # cooldown (15 min)
    rd.on_bar(now + dt.timedelta(minutes=35), len(b["c"]), got.append)
    assert rd.on_bar(now + dt.timedelta(minutes=70), len(b["c"]), got.append) == 0     # two a day per symbol
    assert len(rd.series["SBIN"]) == 13                                                # the same bar is one row
    v = rd.view("SBIN")
    assert v["read"]["decision"]["dir"] == 1 and v["series"] and v["config"]["windows"]


def test_a_bad_symbol_is_skipped_and_logged():
    e = _engine({"NIFTY": _trend_up()})
    e.charts.five_min = lambda sym, day: (_ for _ in ()).throw(ValueError("bad bars"))
    rd = Rider(e)
    assert rd.on_bar(dt.datetime(2026, 11, 19, 10, 0, 5), 9, lambda a: None) == 0
    assert any("Trend Rider NIFTY skipped" in m for m in e.logs)


def test_a_restart_in_the_day_warms_up_without_signals_and_keeps_todays_limits(monkeypatch, tmp_path):
    """3.07 (Mandeep, 24 Nov: after the 09:37 and 10:22 restarts on 23 Nov Pro saw nothing until 10:10; in 6 replayed
    days 74% of Pro's signals came between 09:15 and 10:15)."""
    from live.store import Store
    from live.trials import Trials
    b = _bars(list(100 + np.arange(14) * 0.6) + [109.5], wick=0.3)
    e = _engine({"NIFTY": b, "SBIN": b})
    e.store, e.source = Store(tmp_path / "t.db"), "upstox"
    Trials(e.store, "upstox", log=lambda m: None)
    now = dt.datetime(2026, 11, 19, 10, 0, 5)
    with e.store.lock:                                   # SBIN fired at 09:50 before the restart
        e.store.db.execute("INSERT INTO trial_alerts(ts, day, source, sym, kind, direction, title, data) VALUES (?,?,?,?,?,?,?,?)",
                           (now.timestamp() - 600, "2026-11-19", "upstox", "SBIN", "trend_rider", "up", "t", "{}"))
        e.store.db.commit()
    monkeypatch.setattr(R, "decide", lambda bars, r, tod, c_=None, opt=None, **kw: {"go": True, "dir": 1, "trigger": "day high",
                                                                                "level": 108.0, "stop": 107.5, "risk_pct": 10.0,
                                                                                "fails": [], "why": ["test"]})
    calls = []
    real = R.readings
    monkeypatch.setattr(R, "readings", lambda *a, **k: (calls.append(a[4:6]), real(*a, **k))[1])
    rd = Rider(e)
    got = []
    assert rd.on_bar(now, len(b["c"]), got.append) == 1
    assert [a["sym"] for a in got] == ["NIFTY"]                       # SBIN fired 10 min ago: the cool-down still holds
    assert rd.warm_info["bars"] == 12 and rd.warm_info["signals_back"] == 1 and len(rd.series["SBIN"]) == 13
    assert sum(1 for x in calls if x == (None, None)) == 24          # earlier bars read without any option (those are now)
    assert any("Trend Rider warmed up" in m for m in e.logs)
    late = Rider(e)                                                   # the day's earlier candles are still filling
    half = {k: v[:5] for k, v in b.items()}
    e.charts.five_min = lambda sym, day: half
    out = []
    assert late.on_bar(now, len(b["c"]), out.append) == 0 and not out and late.warmed is None
    assert any("waiting for today's earlier candles" in m for m in e.logs)
    for _ in range(3):
        late.on_bar(now, len(b["c"]), out.append)
    assert late.warmed == now.date()                                  # never waits more than warm_wait_bars bars
    early = Rider(_engine({"NIFTY": b}))
    assert early.on_bar(now.replace(hour=9, minute=31), 4, lambda a: None) >= 0 and early.warmed == now.date() and not early.warm_info
