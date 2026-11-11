"""2.70 (Mandeep, 12 Nov 2026): MindGame, the buyer / seller scene read as ACTIVITY (no fixed points) on every timeframe,
several behaviours at once, one short message (AI when ready, else the system's), every reading recorded and graded;
the timeframe charts are 5m / 15m / 1H / 4H (replaces 75m) / 1D / 1W, each with its own scene beside it. On trial:
no alert, no paper trade, market data only."""
import datetime as dt
import json
import types
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from live import mindgame as MG
from live import mtf as MTFM
from live import mtfcandles as MC
from live.clock import IST
from live.store import Store

ROOT = Path(__file__).resolve().parents[1]
DAY = dt.date(2026, 11, 11)


def _bars(ohlc, day=DAY, mins=5):
    t0 = dt.datetime.combine(day, dt.time(9, 15), IST).timestamp()
    return [[t0 + i * mins * 60, float(o), float(h), float(l), float(c), 0.0] for i, (o, h, l, c) in enumerate(ohlc)]


def _noise(n, base=1000.0, seed=1, amp=6):
    rng = np.random.default_rng(seed)
    out, p = [], base
    for _ in range(n):
        o = p
        c = o + rng.uniform(-amp, amp)
        out.append((o, max(o, c) + 3, min(o, c) - 3, c))
        p = c
    return out


LATE = dt.datetime.combine(DAY, dt.time(15, 29), IST).timestamp()


def _ids(r, side=None):
    return {it["id"] for it in r["items"] if side is None or it["side"] == side}


def test_bullets_file_is_activity_only_and_on_trial():
    ok, later, bad = MG.bullets()
    assert bad == [] and len(ok) == 13 and [b["id"] for b in later] == ["heavyweight"]
    assert all(b["shadow"] and not b["alerts"] for b in ok + later)
    raw = json.loads((ROOT / "rules" / "mindgame.json").read_text())
    assert raw["valid_from"] and {b["n"] for b in raw["bullets"]} == set(range(1, 15))


def test_consolidation_then_a_break_traps_the_other_side():
    base = _noise(40, amp=14)
    p = base[-1][3]
    tight = [(p + d, p + d + 2, p + d - 2, p + d + 1) for d in (0, 1, -1, 2, 0, -2, 1, 0, 2, -1, 0, 1)]
    r = MG.read(_bars(base + tight), "5m", LATE)
    sq = next(it for it in r["items"] if it["id"] == "range_trap")
    assert sq["side"] == 0 and sq["lo"] < sq["hi"] and "Consolidating" in sq["now"] and r["phase"].startswith("consolidating")
    hi = max(x[1] for x in tight)
    brk = (p + 1, hi + 22, p, hi + 20)
    r2 = MG.read(_bars(base + tight + [brk]), "5m", LATE)
    sq2 = next(it for it in r2["items"] if it["id"] == "range_trap")
    assert sq2["side"] == 1 and "Broke up" in sq2["now"] and r2["phase"] == "range broke up"


def _flat_then(rest_or_run):
    base = [(1000, 1008, 994, 1002), (1002, 1020, 998, 1004)] + _noise(28, 1004, seed=3, amp=4)
    base = [(o, min(h, 1019), l, min(c, 1015)) for o, h, l, c in base]
    base[1] = (1002, 1020, 998, 1004)                                        # resistance 1020
    return base + rest_or_run


def test_rest_at_resistance_then_a_strong_break_is_a_real_breakout():
    rest = [(1012, 1018, 1010, 1015), (1015, 1019, 1012, 1016), (1016, 1018, 1013, 1017), (1017, 1019, 1014, 1018)]
    brk = [(1018, 1038, 1016, 1035)]
    r = MG.read(_bars(_flat_then(rest + brk)), "5m", LATE)
    it = next(x for x in r["items"] if x["id"] == "rest_break")
    assert it["side"] == 1 and "rested" in it["now"] and it["level"] == 1019.0 and "trap_run" not in _ids(r)


def test_a_straight_run_through_resistance_is_a_trap_risk():
    run = [(960 + 7 * k, 960 + 7 * k + 8, 960 + 7 * k - 2, 960 + 7 * k + 7) for k in range(7)]   # 960 -> 1009, all green
    brk = [(1009, 1030, 1007, 1028)]
    dip = [(1000, 1002, 962, 965), (965, 968, 958, 961)]
    r = MG.read(_bars(_flat_then(dip + run + brk)), "5m", LATE)
    it = next(x for x in r["items"] if x["id"] == "trap_run")
    assert it["side"] == -1 and "straight" in it["now"] and "rest_break" not in _ids(r)


def test_lows_holding_under_resistance_and_buyers_leaving():
    hold = [(1012, 1018, 1008, 1015), (1015, 1019, 1010, 1016), (1016, 1019, 1011, 1017), (1017, 1019, 1012, 1018), (1018, 1019.5, 1013, 1018)]
    r = MG.read(_bars(_flat_then(hold)), "5m", LATE)
    assert 1 in {it["side"] for it in r["items"] if it["id"] == "holding"}
    up = [(980 + 6 * k, 980 + 6 * k + 7, 980 + 6 * k - 1, 980 + 6 * k + 6) for k in range(6)]     # up-move, green candles
    red = [(1016, 1017, 1000, 1001)]                                                       # closes below the last green low (1009)
    r2 = MG.read(_bars(_noise(30, 980) + up + red), "5m", LATE)
    it = next(x for x in r2["items"] if x["id"] == "leaving")
    assert it["side"] == -1 and "Buyers are leaving" in it["next"]


def test_several_behaviours_at_once_and_a_failing_check_is_skipped(monkeypatch):
    rest = [(1012, 1018, 1010, 1015), (1015, 1019, 1012, 1016), (1016, 1018, 1013, 1017), (1017, 1019, 1014, 1018)]
    rows = _bars(_flat_then(rest + [(1018, 1038, 1016, 1035)]))
    good = MG.read(rows, "5m", LATE, {"pdc": 990.0})
    assert {"rest_break", "yclose"} <= _ids(good)                    # 2+ at once, each in isolation
    monkeypatch.setitem(MG.DETECT, "level_break", lambda x: 1 / 0)
    bad = MG.read(rows, "5m", LATE, {"pdc": 990.0})
    assert "rest_break" not in _ids(bad) and "yclose" in _ids(bad) and any(f.startswith("level_break") for f in bad["failed"])


def test_checks_are_measured_in_the_symbols_own_candle_not_points():
    rest = [(1012, 1018, 1010, 1015), (1015, 1019, 1012, 1016), (1016, 1018, 1013, 1017), (1017, 1019, 1014, 1018)]
    ohlc = _flat_then(rest + [(1018, 1038, 1016, 1035)])
    a = MG.read(_bars(ohlc), "5m", LATE)
    scaled = [tuple(v * 25 for v in x) for x in ohlc]                  # the same activity on a 25x bigger index
    b = MG.read(_bars(scaled), "5m", LATE)
    assert [(i["id"], i["side"]) for i in a["items"]] == [(i["id"], i["side"]) for i in b["items"]]
    assert b["unit"] == pytest.approx(a["unit"] * 25, rel=1e-3)


def test_forming_candle_is_left_out_on_small_timeframes():
    rows = _bars(_noise(40))
    t_last = rows[-1][0]
    r = MG.read(rows, "5m", t_last + 60)                               # the last candle is still forming
    assert r["at"] == rows[-2][0] and not r["forming"]
    assert MG.is_forming("1D", dt.datetime.combine(DAY, dt.time(9, 15), IST).timestamp(), LATE)


def test_record_graded_right_wrong_and_trust():
    st = Store(":memory:")
    bk = MG.Book(st, "upstox")
    rows = _bars(_noise(40))
    r = {"tf": "5m", "at": rows[-1][0], "close": 1000.0, "unit": 10.0, "forming": False,
         "items": [{"id": "rest_break", "side": 1, "now": "a", "next": "b"}, {"id": "trap_run", "side": -1, "now": "c", "next": "d"},
                   {"id": "range_trap", "side": 0, "now": "e", "next": "f"}]}
    assert bk.observe("NIFTY", "5m", r) == 2                           # information-only bullets are not graded
    assert bk.observe("NIFTY", "5m", {**r, "at": r["at"] + 300}) == 0   # still active: not a new reading
    t = r["at"]
    fut = [[t + 300, 1000, 1004, 997, 1003, 0], [t + 600, 1003, 1013, 1001, 1012, 0]]   # up 1.3 units, down 0.3 first
    assert bk.grade("NIFTY", "5m", fut) == 2
    s = bk.stats()
    assert s["rest_break"]["right"] == 1 and s["trap_run"]["wrong"] == 1 and s[("rest_break", "5m")]["right_pct"] == 100
    assert s["rest_break"]["status"] == "on trial"
    tab = {x["id"]: x for x in bk.table()}
    assert tab["rest_break"]["n"] == 1 and tab["heavyweight"]["later"] and not any(x["alerts"] for x in tab.values())


def test_combined_message_is_short_and_ai_replaces_it_when_ready(monkeypatch):
    base = _noise(40, amp=14)
    p = base[-1][3]
    tight = [(p + d, p + d + 2, p + d - 2, p + d + 1) for d in (0, 1, -1, 2, 0, -2, 1, 0, 2, -1, 0, 1)]
    r5 = MG.read(_bars(base + tight), "5m", LATE)
    r1 = MG.read(_bars(base + tight, mins=1), "1m", LATE)
    reads = {"1m": r1, "5m": r5}
    comb = MG.combine(reads)
    lines = MG.system_message(reads, comb)
    assert 2 <= len(lines) <= 7 and all(len(x) < 240 for x in lines) and lines[-1].startswith("Watch")

    class FakeAI:
        calls = []

        def enabled(self):
            return True

        def ask(self, prompt, max_tokens, auto=True, budget=None, task=None):
            self.calls.append((budget, task))
            return "- Buyers and sellers balanced in a tight range\n- Watch the break"
    ai, m = FakeAI(), MG.MindAI()
    assert m.message(ai, "NIFTY", reads, comb) is None                  # the system's message first, AI asked in the background
    m.wait("NIFTY")
    assert m.message(ai, "NIFTY", reads, comb).startswith("- Buyers")
    assert ai.calls == [(("mindgame", 60), "mindgame")]
    assert m.message(None, "NIFTY", reads, comb) is None                 # no AI: the system's message


def test_mindgame_ai_has_its_own_allowance(monkeypatch):
    from live import notify
    a = notify.AI(lambda: {"ai": {"provider": "claude", "api_key": "x"}})
    monkeypatch.setattr(a, "_run", lambda *x: "ok")
    assert a.route("mindgame")["tier"] == "fast"
    assert a.ask("p", budget=("mindgame", 1), task="mindgame") == "ok"
    assert a.ask("p", budget=("mindgame", 1), task="mindgame") is None and "mindgame" in a.last_error
    assert a.budgets.get("tier:fast", 0) == 0                            # the news radar's allowance untouched
    assert a.ask("p", task="news") == "ok" and a.budgets["tier:fast"] == 1


def test_four_hours_and_weekly_charts():
    assert "4H" in MTFM.TFS and "1W" in MTFM.TFS and MTFM.ORDER == ["1W", "1D", "4H", "1H", "15m", "5m"] and "75m" not in MTFM.ORDER
    one = [[dt.datetime.combine(DAY, dt.time(9, 15), IST).timestamp() + 900 * k, 100 + k, 101 + k, 99 + k, 100.5 + k, 1] for k in range(25)]
    four = MTFM.resample(one, 240)
    assert [dt.datetime.fromtimestamp(r[0], IST).strftime("%H:%M") for r in four] == ["09:15", "13:15"]
    assert four[0][2] == 101 + 15 and four[1][1] == 100 + 16
    days = [dt.date(2026, 1, 5) + dt.timedelta(days=k) for k in range(400) if (dt.date(2026, 1, 5) + dt.timedelta(days=k)).weekday() < 5]
    rng = np.random.default_rng(4)
    px = 50000 * np.exp(np.cumsum(rng.normal(0, 0.01, len(days))))
    rows = [[dt.datetime.combine(d, dt.time(9, 15), IST).timestamp(), p, p * 1.01, p * 0.99, p, 0] for d, p in zip(days, px)]

    class E:
        data_dir = "/tmp"
        metrics = {}
        charts = types.SimpleNamespace(today_bars=lambda s: [])

        def cfg(self):
            return {}
    m = MTFM.MTF(E())
    m.long_rows = lambda sym, base, days: (rows, "test")
    wk = m.bars("NIFTY", "1W")
    assert len(wk) == len({d.isocalendar()[:2] for d in days}) and wk[0][2] == max(r[2] for r in rows[:5])
    p = m.panel("NIFTY", "1W", None)
    names = {x["name"]: x["price"] for x in p["levels"]}
    assert names["All-time high"] == pytest.approx(max(r[2] for r in rows), rel=1e-6)
    assert "52-week high" in names and "52-week low" in names
    assert any(n.startswith("Strongest") for n in names)


def test_timeframe_view_gives_each_chart_its_own_scene(monkeypatch):
    class E:
        data_dir = "/tmp"
        metrics = {}
        charts = types.SimpleNamespace(today_bars=lambda s: [])
        mind = None

        def cfg(self):
            return {}
    e = E()
    m = MTFM.MTF(e)
    rng = np.random.default_rng(2)

    def lr(sym, base, days):
        n = 600
        step = 86400 if base == "D" else 60 * int(base)
        t0 = dt.datetime.combine(DAY, dt.time(9, 15), IST).timestamp() - n * step
        px = 25000 + np.cumsum(rng.normal(0, 5, n))
        return [[t0 + k * step, p, p + 4, p - 4, p + 1, 0] for k, p in enumerate(px)], "test"
    m.long_rows = lr
    e.mind = MG.MindGame(e, Store(":memory:"), "upstox")
    v = m.view("NIFTY")
    assert set(v["panels"]) == {"1W", "1D", "4H", "1H", "15m", "5m"}
    for tf, p in v["panels"].items():
        assert p["mind"]["tf"] == tf
    assert set(e.mind.big["NIFTY"]["reads"]) == set(v["panels"])


def test_candle_scanner_has_4h_and_hides_75m_on_screen():
    c = MC.cfg()
    assert 240 in c["intraday_min"] and "4H" in c["bench"] and "75m" in c["hidden"] and MC.TF_NAME[240] == "4H"
    assert 75 in c["intraday_min"]                                       # the 75m trial keeps recording (records kept)


def test_mind_api(monkeypatch):
    from webapp import live_api
    rows = _bars(_noise(400, 25000, amp=8), day=dt.date(2026, 11, 10), mins=1)

    class FakeMTF:
        def candles(self, sym, tf, before=None, limit=1500, tail=None):
            return {"candles": [{"time": int(r[0] + 19800), "open": r[1], "high": r[2], "low": r[3], "close": r[4]} for r in rows]}
    e = types.SimpleNamespace(mtf=FakeMTF(), metrics={"NIFTY": {"cp": 25010.0}}, ai=None, store=Store(":memory:"), source="upstox",
                              log=lambda m: None, mind=None)
    monkeypatch.setattr(live_api, "engine", lambda: e)
    app = FastAPI()
    app.include_router(live_api.router)
    cl = TestClient(app)
    v = cl.get("/api/live/mind/nifty").json()
    assert set(v["reads"]) == {"1m", "3m", "5m"} and v["by"] == "system" and v["message"]
    rec = cl.get("/api/live/mind_record").json()
    assert len(rec["rows"]) == 14 and rec["trust"]["min_n"] == 30


def test_engine_tick_records_in_its_own_thread():
    rows = _bars(_noise(400, 25000, amp=8), day=dt.date(2026, 11, 10), mins=1)

    class FakeMTF:
        def candles(self, sym, tf, before=None, limit=1500, tail=None):
            return {"candles": [{"time": int(r[0] + 19800), "open": r[1], "high": r[2], "low": r[3], "close": r[4]} for r in rows]}
    e = types.SimpleNamespace(mtf=FakeMTF(), metrics={}, _every=lambda *a, **k: True)
    mg = MG.MindGame(e, Store(":memory:"), "upstox", log=lambda m: None)
    mg._work(rows[-1][0] + 3600)
    assert set(mg.last) == {"NIFTY", "BANKNIFTY"} and not mg.busy


def test_new_code_never_places_orders():
    for f in ("live/mindgame.py",):
        src = (ROOT / f).read_text().lower()
        for bad in ("place_order", "modify_order", "cancel_order", "requests.", "upstoxfeed", "access_token"):
            assert bad not in src, (f, bad)


def test_no_fake_one_minute_scene_from_five_minute_candles():
    rows = _bars(_noise(300, 25000, amp=8), day=dt.date(2026, 11, 10), mins=5)

    class FakeMTF:
        def candles(self, sym, tf, before=None, limit=1500, tail=None):
            return {"candles": [{"time": int(r[0] + 19800), "open": r[1], "high": r[2], "low": r[3], "close": r[4]} for r in rows]}
    e = types.SimpleNamespace(mtf=FakeMTF(), metrics={})
    reads, _ = MG.MindGame(e, None, "upstox").small("NIFTY", ("1m", "3m", "5m"), rows[-1][0] + 3600)
    assert reads["1m"]["empty"].startswith("No 1-minute") and reads["3m"]["empty"] and not reads["5m"].get("empty")
