"""2.81: two slow parts of the analysis loop moved off it (22 Nov audit: the scorecard took 2-6 s every 5 minutes, the
5-minute setup / Lab rules scan 5-8 s at each bar close). Same results: the scorecard reads the same rows in the same
order (only the columns it uses); the scan is the same scan of the same bar, its alerts raised by the loop in order."""
import datetime as dt
import json
import random
import threading
import time

from live import record as R
from live.chartdata import ChartData
from live.engine import IST, LiveEngine
from live.store import Store


def _outcomes(tmp_path, n=400):
    st = Store(tmp_path / "live.db")
    R.Tracker(st, "upstox")                     # creates the outcomes table
    rnd = random.Random(7)
    kinds = ["setup_orb", "factory", "pattern_flag", "candle_hammer", "oi_wall"]
    with st.lock:
        for i in range(n):
            st.db.execute(
                "INSERT INTO outcomes(alert_id, source, day, ts, sym, kind, direction, severity, is_index, o15, o60, r15, result, features)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (i + 1, "upstox" if i % 7 else "sim", "2026-11-19", 1795000000 + rnd.random() * 1e5, "NIFTY", rnd.choice(kinds),
                 rnd.choice(["up", "down"]), rnd.randint(1, 3), rnd.randint(0, 1), rnd.uniform(-30, 30), rnd.uniform(-40, 40),
                 rnd.uniform(-1, 1), rnd.choice(["hit", "miss", "flat", None]), json.dumps({"f": "x" * 900})))
        st.db.commit()
    return st


def test_the_scorecard_reads_only_its_columns_and_gives_the_same_result(tmp_path, monkeypatch):
    st = _outcomes(tmp_path)
    assert "features" not in R.SCORECARD_COLS and all(c in R.SCORECARD_COLS for c in ("kind", "result", "o15", "o60", "r15"))
    lean = {src: R.scorecard(st, src) for src in ("upstox", "sim")}
    monkeypatch.setattr(R, "SCORECARD_COLS", "*")         # the old query: every column
    full = {src: R.scorecard(st, src) for src in ("upstox", "sim")}
    assert lean == full and lean["upstox"] and lean["sim"]


def _bare_engine(source="upstox"):
    e = LiveEngine.__new__(LiveEngine)
    e.source, e.status, e._last, e.stats = source, {}, {}, {}
    e.logs = []
    e.log = e.logs.append
    e.cfg = lambda: {"live": {}}
    e._prof = {}
    return e


def test_the_scorecard_refresh_runs_in_its_own_thread_once_at_a_time(monkeypatch):
    e = _bare_engine()
    e.store = object()
    seen = []

    def slow(store, source):
        seen.append(threading.current_thread().name)
        time.sleep(0.3)
        return {("factory",): {"n": 1}}
    monkeypatch.setattr("live.engine.scorecard", slow)
    assert e._refresh_stats_bg() is True
    assert e._refresh_stats_bg() is False                 # still running: not started twice
    assert e.stats == {}                                   # the loop went on at once
    for _ in range(50):
        if e.stats:
            break
        time.sleep(0.05)
    time.sleep(0.05)
    assert e.stats == {("factory",): {"n": 1}} and seen == ["stats-worker"] and e.status["stats_ms"] >= 250
    assert e._refresh_stats_bg() is True                   # finished: the next refresh may start


class _Charts:
    def __init__(self):
        self.calls, self.thread = 0, None

    def due(self, now):
        return self.calls == 0

    def scan(self, now, emit):
        self.calls += 1
        self.thread = threading.current_thread().name
        for i in range(3):
            time.sleep(0.05)
            emit({"sym": "NIFTY", "kind": f"setup_{i}", "n": i})


def test_the_scan_runs_in_its_own_thread_and_the_loop_raises_its_alerts_in_order():
    e = _bare_engine()
    e.charts = _Charts()
    raised = []
    e._emit_safe = lambda a, now, lc: raised.append((a["n"], now, threading.current_thread().name))
    now = dt.datetime(2026, 11, 19, 13, 10, 2, tzinfo=IST)
    assert e._scan_in_background()
    assert e._scan_bg(now, {"x": 1}) is True
    assert e._scan_bg(now, {"x": 1}) is False              # busy (and nothing new is due): never two scans at once
    e._scan_thread.join(5)
    assert e.charts.thread == "scan-worker" and e.charts.calls == 1 and raised == []    # found, not raised yet
    assert e._scan_drain() == 3
    assert raised == [(0, now, threading.current_thread().name), (1, now, threading.current_thread().name),
                      (2, now, threading.current_thread().name)]        # by the loop's thread, in order, with the scan's time
    assert e.status["scan_ms"] >= 100 and e._scan_drain() == 0


def test_end_of_day_waits_for_a_running_scan_and_raises_what_it_found():
    e = _bare_engine()
    e.charts = _Charts()
    raised = []
    e._emit_safe = lambda a, now, lc: raised.append(a["n"])
    e._scan_bg(dt.datetime(2026, 11, 19, 15, 25, 1, tzinfo=IST), {})
    e._scan_wait()
    assert raised == [0, 1, 2]


def test_practice_runs_and_the_setting_keep_the_scan_inside_the_loop():
    assert _bare_engine("upstox")._scan_in_background() is True
    assert _bare_engine("replay")._scan_in_background() is False      # a replay stays step by step
    assert _bare_engine("sim")._scan_in_background() is False
    e = _bare_engine("upstox")
    e.cfg = lambda: {"live": {"scan_in_background": False}}
    assert e._scan_in_background() is False


def test_due_matches_the_scans_own_test():
    c = ChartData.__new__(ChartData)
    c.last_slot = None
    day = dt.date(2026, 11, 19)
    at = lambda h, m, s=0: dt.datetime(2026, 11, 19, h, m, s, tzinfo=IST)
    assert not c.due(at(9, 10)) and not c.due(at(9, 29, 59))          # before 09:30 (first three bars)
    assert c.due(at(9, 30, 1))
    c.last_slot = int((at(9, 30, 1).timestamp() - c._session_start(day)) // 300)
    assert not c.due(at(9, 34, 59)) and c.due(at(9, 35, 0))           # the next bar
