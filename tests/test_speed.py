"""2.38 speed: the Lab runs in its own low-priority process, screens go before the 5-year Dhan download, settings are
parsed once until they change, and every screen call is timed for the System page."""
import json
import threading
import time

from lab import dhan_budget as B, proc


def test_lab_job_runs_in_a_child_process_and_logs_back(tmp_path):
    import os
    lines = []
    out = proc.run("echo", tmp_path, log=lines.append, n=3)
    assert out["done"] == 3 and out["pid"] != os.getpid() and lines == ["line 0", "line 1", "line 2"]
    assert proc.LAST["how"] == "child process" and proc.LAST["job"] == "echo"


def test_stop_reaches_the_child_and_a_missing_child_falls_back(tmp_path, monkeypatch):
    import os
    flag = threading.Event()
    threading.Timer(1.5, flag.set).start()
    lines = []
    out = proc.run("echo", tmp_path, log=lines.append, should_stop=flag.is_set, n=200, sleep=0.1)
    assert out.get("stopped") and "stopped" in lines and len(lines) < 100
    monkeypatch.setattr(proc.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("no fork here")))
    lines = []
    out = proc.run("echo", tmp_path, log=lines.append, n=2)
    assert out["pid"] == os.getpid() and proc.LAST["how"] == "app process" and any("could not start" in x for x in lines)


def test_a_failing_lab_job_is_reported(tmp_path):
    import pytest
    with pytest.raises(RuntimeError):
        proc.run("factory", tmp_path / "empty", log=lambda m: None, n_random=10, n_mutate=5)     # no history: fails cleanly


def test_lab_round_in_a_child_gives_the_same_result(tmp_path):
    import datetime as dt
    import numpy as np
    import pandas as pd
    from lab import factory as F
    rng = np.random.default_rng(5)
    rows = []
    for k in range(80):
        day = dt.date(2025, 1, 1) + dt.timedelta(days=k)
        for bar in range(2, 20):
            base = {f: float(rng.normal()) for f in F.FEATURES}
            base["tod"] = (bar + 1) * 5.0
            for d in (1, -1):
                pnl = rng.normal(-200, 800) + (900 if d == 1 and base["ret_b3"] > 1.0 else 0)
                rows.append({"sym": "NIFTY", "date": day, "bar": bar, **base, "dir": d, "premium": 8000,
                             **{"pnl_" + r: pnl for r in set(F.EXITS) | set(F.STYLES)}})
    for name in ("a", "b"):
        f = F.cache_path(tmp_path / name)
        f.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_parquet(f, index=False)
    seed = [{"dir": 1, "conds": [["ret_b3", ">", 1.0]], "win": [10, 200], "exit": "std"}]
    F.run(tmp_path / "a", n_random=0, n_mutate=0, log=lambda m: None, extra=seed)
    proc.run("factory", tmp_path / "b", log=lambda m: None, n_random=0, n_mutate=0, extra=seed)
    ra, rb = F.load(tmp_path / "a")["rules"], F.load(tmp_path / "b")["rules"]
    assert ra and set(ra) == set(rb)
    for k in ra:
        assert all((ra[k][p].get("n"), ra[k][p].get("total")) == (rb[k][p].get("n"), rb[k][p].get("total")) for p in ("train", "valid", "hold"))


def test_screens_go_before_the_download():
    stamps, stop = [], threading.Event()

    def bg():
        while not stop.is_set():
            B.wait(priority=False)
            stamps.append(time.time())
    ts = [threading.Thread(target=bg, daemon=True) for _ in range(3)]
    for t in ts:
        t.start()
    time.sleep(0.8)
    waits = []
    for _ in range(4):
        t = time.time()
        B.wait(priority=True)
        waits.append(time.time() - t)
        stamps.append(time.time())
    stop.set()
    for t in ts:
        t.join(timeout=3)
    gaps = [b - a for a, b in zip(sorted(stamps), sorted(stamps)[1:])]
    assert max(waits) < 2.5 / B.RATE                       # a screen never waits behind the download's queue
    assert min(gaps) > 0.9 / B.RATE and B.gave_way() >= 1   # the shared pace still holds


def test_settings_are_parsed_once_until_they_change(tmp_path, monkeypatch):
    from radar import config as C
    f = tmp_path / "user_settings.json"
    monkeypatch.setattr(C, "USER_SETTINGS", f)
    f.write_text(json.dumps({"ai.daily_limit": 7}))
    a = C.load_config()
    assert a["ai"]["daily_limit"] == 7
    a["ai"]["daily_limit"] = 99                           # a caller changing its copy changes nothing else
    assert C.load_config()["ai"]["daily_limit"] == 7
    time.sleep(0.01)
    f.write_text(json.dumps({"ai.daily_limit": 11}))
    assert C.load_config()["ai"]["daily_limit"] == 11     # a new setting is seen at once
    t = time.time()
    for _ in range(50):
        C.load_config()
    assert (time.time() - t) / 50 < 0.01


def test_screen_calls_are_timed():
    from webapp import perf
    perf.record("GET", "/api/live/candles/NIFTY", "tf=5m", 2300, 200)
    perf.record("GET", "/api/live/candles/BANKNIFTY", "", 40, 200)
    perf.record("POST", "/api/ai/review", "", 9000, 200)          # asking Claude is slow by nature: not a slow screen
    v = perf.view()
    row = next(x for x in v["slowest"] if x["call"] == "GET /api/live/candles")
    assert row["n"] >= 2 and row["max_ms"] >= 2300
    assert v["slow"][0]["path"] == "/api/live/candles/NIFTY?tf=5m" and not any("/api/ai" in x["path"] for x in v["slow"])
