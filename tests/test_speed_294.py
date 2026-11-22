"""2.94 (Mandeep, 23 Nov 2026: the System page's Speed panel, "check the speed one"): slow screens no longer redo the
same work; the answers are the same as before."""
import datetime as dt
import gzip
import os
import time
from pathlib import Path

import pandas as pd

from live import bench as B
from webapp import memo


def test_memo_recomputes_only_when_a_file_changes_and_survives_a_restart(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("a")
    calls = []

    def fn():
        calls.append(1)
        return {"v": f.read_text()}
    disk = tmp_path / "cache" / "x.json"
    assert memo.memo("t1", memo.stamp(f), fn, disk=disk) == {"v": "a"}
    assert memo.memo("t1", memo.stamp(f), fn, disk=disk) == {"v": "a"} and len(calls) == 1
    memo.forget("t1")                                            # a restart: memory gone, the disk copy answers
    assert memo.memo("t1", memo.stamp(f), fn, disk=disk) == {"v": "a"} and len(calls) == 1
    time.sleep(0.01)
    f.write_text("bb")
    os.utime(f, None)
    assert memo.memo("t1", memo.stamp(f), fn, disk=disk) == {"v": "bb"} and len(calls) == 2
    assert memo.memo("t2", None, fn, ttl=0) and memo.memo("t2", None, fn, ttl=0) and len(calls) == 4   # ttl 0 = always
    assert memo.stamp(tmp_path / "missing")[0][1:] == [None, None]


def _bench(tmp_path, days=("2026-11-04", "2026-11-05", "2026-11-06"), n=300):
    b = B.BenchDB(tmp_path / "bench.db")
    rows = []
    for d in days:
        for i in range(n):
            rows.append((1.0, d, "upstox", "SBIN", f"k{i % 7}", "15m" if i % 3 else None, ["buy", "sell"][i % 2],
                         ["range", "quiet"][i % 2], (i % 11) * 100 - 500.0, 15000, 3000, None, i % 2, f"name {i % 7}"))
    with b.lock:
        b.db.executemany("INSERT INTO bench_trades(ts, day, source, sym, kind, variant, style, regime, net, cost_lot, risk_lot, "
                         "need_lot, is_index, name) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        b.db.commit()
    return b


def _plain(rows):
    return sorted((tuple(sorted(r.items())) for r in rows), key=str)


def test_board_days_come_from_disk_after_a_restart_and_match(tmp_path):
    b = _bench(tmp_path)
    con = b.reader()
    assert B.days_with_trades(con, "upstox", "2026-10-01", "2026-11-23") == ["2026-11-04", "2026-11-05", "2026-11-06"]
    assert B.days_with_trades(con, "upstox", "2026-11-05", "2026-11-06") == ["2026-11-05"]
    con.close()
    first = B.day_groups(b, "upstox", 60, "2026-11-23")
    files = sorted((tmp_path / "bench_cache").glob("*.json.gz"))
    assert len(files) == 3
    B._DAYS.clear()                                              # a restart
    hits = []
    real_get = B._disk_get
    B._disk_get = lambda *a: hits.append(a[3]) or real_get(*a)
    try:
        again = B.day_groups(b, "upstox", 60, "2026-11-23")
        from_disk = [real_get(b, b.reader(), "upstox", d) is not None for d in hits]
    finally:
        B._disk_get = real_get
    assert _plain(again) == _plain(first) and hits == ["2026-11-04", "2026-11-05", "2026-11-06"] and all(from_disk)
    # a voided day (2.88 moves its trades to another source) is read again, not taken from the stale copy
    with b.lock:
        b.db.execute("UPDATE bench_trades SET source='upstox|void' WHERE day='2026-11-05'")
        b.db.commit()
    B._DAYS.clear()
    after = B.day_groups(b, "upstox", 60, "2026-11-23")
    assert {r["day"] for r in after} == {"2026-11-04", "2026-11-06"}
    assert _plain(after) == _plain([r for r in first if r["day"] != "2026-11-05"])


def test_old_cache_files_are_dropped(tmp_path):
    b = _bench(tmp_path, days=("2026-11-04",), n=20)
    d = tmp_path / "bench_cache"
    d.mkdir()
    (d / "upstox__2024-01-02.json.gz").write_bytes(gzip.compress(b"{}"))
    B._DAYS.clear()
    B.day_groups(b, "upstox", 60, "2026-11-23")
    assert not (d / "upstox__2024-01-02.json.gz").exists() and (d / "upstox__2026-11-04.json.gz").exists()


def test_status_reads_the_model_and_market_files_only_when_they_change(tmp_path, monkeypatch):
    from webapp import server
    monkeypatch.setattr(server, "VIEW_CACHE", tmp_path / "vc")
    memo.forget("status_")
    cache = tmp_path / "cache"
    cache.mkdir()
    idx = pd.date_range("2026-01-01", periods=5, freq="D")
    pd.DataFrame({"close": range(5)}, index=idx).to_parquet(cache / "mkt_nifty.parquet")
    c = {"cache_dir": str(cache), "tickers": {"nifty": "^NSEI", "vix": "^INDIAVIX"}, "model_dir": str(tmp_path / "m")}
    reads = []
    real = server.pd.read_parquet
    monkeypatch.setattr(server.pd, "read_parquet", lambda f, *a, **k: reads.append(f) or real(f, *a, **k))
    one = server._market_files(c)
    two = server._market_files(c)
    assert one == two and len(reads) == 1
    assert one[0]["days"] == 5 and one[0]["to"] == "2026-01-05" and one[1]["days"] == 0
    time.sleep(0.01)
    pd.DataFrame({"close": range(7)}, index=pd.date_range("2026-01-01", periods=7, freq="D")).to_parquet(cache / "mkt_nifty.parquet")
    assert server._market_files(c)[0]["days"] == 7 and len(reads) == 2
    loads = []
    monkeypatch.setattr(server.model, "load_bundle", lambda cc: loads.append(1) or None)
    assert server._model_meta(c) is None and server._model_meta(c) is None and len(loads) == 1


def test_system_page_checks_and_journeys_are_not_redone_every_call():
    import inspect
    from webapp import server
    src = inspect.getsource(server.system_view)
    assert 'memo.memo("static_checks"' in src and 'memo.memo("lab_updated"' in src
    assert "memo.memo(\"vault_view\"" in inspect.getsource(server._vault_view)
    assert "memo.memo(f\"paths|{days}\"" in inspect.getsource(server.paths_view)
    w = inspect.getsource(server._warm_views)
    assert '"pytest" in sys.modules' in w and "_warm_board" in w and "paths_view(60)" in w


def test_speed_panel_names_the_slow_piece_and_marks_start_up_slowness(monkeypatch):
    from webapp import perf
    monkeypatch.setattr(perf, "_slow", perf.collections.deque(maxlen=60))
    monkeypatch.setattr(perf, "_parts", {})
    monkeypatch.setattr(perf, "START", time.time() - 30)
    perf.record("GET", "/api/status", "", 2400, 200)              # 30 s after the start
    m = perf.Marks("system")
    time.sleep(0.02)
    m("checks (live)")
    v = perf.view()
    assert v["slow"][0]["startup"] and v["slow_last_hour"] == 1 and v["slow_last_hour_running"] == 0
    assert v["parts"][0]["part"] == "system: checks (live)" and v["parts"][0]["avg_ms"] >= 15
    monkeypatch.setattr(perf, "START", time.time() - 600)
    perf.record("GET", "/api/live/board", "regime=auto", 1800, 200)
    v = perf.view()
    assert not v["slow"][0]["startup"] and v["slow_last_hour_running"] == 1
