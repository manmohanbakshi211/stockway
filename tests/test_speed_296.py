"""2.96 (Mandeep, 23 Nov 2026, 09:38: "clash bhi hai or delay bhi"): live.db commits no longer hold every screen and the
paper-exit watcher; the recording is counted at most once a minute."""
import threading
import time

from live.store import Store


def _alert(i):
    return {"ts": 1.0 + i, "day": "2026-11-23", "source": "upstox", "sym": "NIFTY", "kind": "orb", "direction": "up",
            "severity": 3, "title": f"t{i}", "detail": "d", "price": 1.0, "data": {"i": i}}


def test_live_db_is_wal_and_screens_read_without_waiting_for_the_writer(tmp_path):
    s = Store(tmp_path / "live.db")
    assert s.wal and s.rdb is not None
    assert s.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    a = s.add_alert(_alert(1))
    assert s.alerts(day="2026-11-23")[0]["id"] == a                 # a commit is seen at once by the screens' connection
    s.put("k", {"x": 1})
    assert s.get("k") == {"x": 1} and s.alert(a)["data"] == {"i": 1} and s.alert_days(5)[0]["n"] == 1
    held = threading.Event()
    done = threading.Event()

    def writer():                                                   # a long write holding the store's lock
        with s.lock:
            s.db.execute("INSERT INTO kv(k, v) VALUES ('slow', '1')")
            held.set()
            done.wait(3)
            s.db.commit()
    th = threading.Thread(target=writer)
    th.start()
    held.wait(2)
    t0 = time.time()
    rows = s.alerts(day="2026-11-23")
    trades = s.trades(status="open")
    waited = time.time() - t0
    done.set()
    th.join()
    assert waited < 1.0 and len(rows) == 1 and trades == []          # did not wait for the writer
    assert s.get("slow") == 1                                       # and sees it once committed


def test_a_store_on_a_disk_without_wal_still_works(tmp_path, monkeypatch):
    import sqlite3
    s = Store(tmp_path / "x.db")
    s.rdb = None                                                    # the fallback path: one connection, one lock
    s.put("a", 2)
    assert s.get("a") == 2 and s.alerts() == [] and s.trades() == []
    assert isinstance(s.db, sqlite3.Connection)


def test_recording_counts_are_kept_a_minute():
    from live import record
    r = record.Recorder.__new__(record.Recorder)
    calls = []
    r._health_now = lambda: calls.append(1) or {"minutes": 5}
    assert r.health() == {"minutes": 5} and r.health() == {"minutes": 5} and len(calls) == 1
    r._health = (time.time() - 61, {"minutes": 5})
    r.health()
    assert len(calls) == 2


def test_the_day_candle_save_runs_beside_the_analysis_pass():
    import inspect
    from live import engine
    src = inspect.getsource(engine.LiveEngine.run_loop) if hasattr(engine.LiveEngine, "run_loop") else inspect.getsource(engine.LiveEngine)
    assert "self._save_bars_bg()" in src and '_guard("Saving 1-minute candles"' not in src
    e = engine.LiveEngine.__new__(engine.LiveEngine)
    seen = []
    e.status, e.log = {}, seen.append
    e._save_bars = lambda: time.sleep(0.2) or seen.append("saved")
    t0 = time.time()
    e._save_bars_bg()
    e._save_bars_bg()                                               # a second call while one runs: ignored
    assert time.time() - t0 < 0.1
    e._bars_th.join(2)
    assert seen == ["saved"] and e.status["bars_save_ms"] >= 150
