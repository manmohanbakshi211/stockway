"""3.00 (Mandeep, 24 Nov 2026: "pages load mein time lagta hai jab app start bhi rahe or live market ... kabhi kabhi paper
trade ki screen number freeze ho jaate hain"). Measured on the Mac (2.99, holiday): Paper calls 10.7-16 s in the first
25 s after the 00:44 start (the scenario engine's daily test, ~80 s of the app's core); 49 Yahoo download threads stuck;
/api/version 0.65 s a call (git started for every call); scorecard 1.5-2 s and the day page 1-1.4 s on every call; page
answers encoded to JSON on the event loop that also streams live prices."""
import datetime as dt
import json
import math
import threading
import time
import types

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- JSON made in the worker thread
def test_answers_are_encoded_in_the_worker_thread_with_the_same_bytes():
    from webapp.fastjson import FastJSONRoute, render
    seen = {}
    r = APIRouter(route_class=FastJSONRoute)

    @r.get("/a")
    def a(x: int = 1) -> dict:
        seen["thread"] = threading.current_thread().name
        return {"x": x, "t": (1, 2), "d": dt.datetime(2026, 11, 24, 9, 15), "s": [3], 5: "five", "u": "₹ ok", "f": 0.1 + 0.2}

    @r.get("/nan")
    def nan():
        return {"bad": math.nan}

    @r.get("/resp")
    def resp():
        return JSONResponse({"kept": True}, status_code=202)

    @r.get("/async")
    async def as_():
        return {"ok": 1}
    app = FastAPI()
    app.include_router(r)
    c = TestClient(app, raise_server_exceptions=False)
    got = c.get("/a?x=3")
    from fastapi.encoders import jsonable_encoder
    old = JSONResponse(jsonable_encoder({"x": 3, "t": (1, 2), "d": dt.datetime(2026, 11, 24, 9, 15), "s": [3], 5: "five",
                                         "u": "₹ ok", "f": 0.1 + 0.2})).body
    assert got.content == old and got.headers["content-type"] == "application/json"     # byte for byte as before
    assert seen["thread"] != threading.main_thread().name
    assert c.get("/nan").status_code == 500                     # as before: NaN is not JSON
    assert c.get("/resp").status_code == 202 and c.get("/resp").json() == {"kept": True}
    assert c.get("/async").json() == {"ok": 1}
    wrapped = {rt.path: getattr(rt.endpoint, "_fastjson", False) for rt in r.routes}
    assert wrapped == {"/a": True, "/nan": True, "/resp": True, "/async": False}
    assert render({"a": [1, {"b": None}]}) == b'{"a":[1,{"b":null}]}'


def test_the_app_and_the_live_router_use_it():
    from webapp import live_api, server
    from webapp.fastjson import FastJSONRoute
    assert live_api.router.route_class is FastJSONRoute and server.app.router.route_class is FastJSONRoute
    alerts = [rt for rt in live_api.router.routes if getattr(rt, "path", "") == "/api/live/alerts"]
    assert alerts and getattr(alerts[0].endpoint, "_fastjson", False)
    sysr = [rt for rt in server.app.routes if getattr(rt, "path", "") == "/api/system"]
    assert sysr and getattr(sysr[0].endpoint, "_fastjson", False)


# ---------------------------------------------------------------- the version check
def test_version_check_never_waits_for_git_or_github(monkeypatch):
    from webapp import server as S
    calls = {"disk": 0, "remote": 0}
    monkeypatch.setattr(S, "app_version", lambda: calls.__setitem__("disk", calls["disk"] + 1) or {"name": "3.00", "full": "abc", "commit": "abc", "date": "d"})
    started = threading.Event()

    def slow_remote():
        calls["remote"] += 1
        started.set()
        time.sleep(0.5)
        S._VERSION_CHECK.update(at=time.time(), result="abc", busy=False)
    monkeypatch.setattr(S, "_remote_head", slow_remote)
    monkeypatch.setattr(S, "RUNNING_VERSION", {"name": "3.00", "full": "abc"})
    S._DISK_VERSION.update(at=0.0, v=None)
    S._VERSION_CHECK.update(at=0.0, result=None, busy=False)
    t0 = time.time()
    v1 = S.version_check()
    v2 = S.version_check()
    assert time.time() - t0 < 0.3 and v1["state"] == "checking" and calls["disk"] == 1
    started.wait(2)
    time.sleep(0.7)
    assert S.version_check()["state"] == "latest" and calls == {"disk": 1, "remote": 1}
    js = (ROOT / "webapp" / "static" / "app.js").read_text()
    assert "checking:" in js and 'v.state === "checking"' in js


# ---------------------------------------------------------------- the scenario engine's daily test
def test_scenario_test_runs_in_its_own_process(tmp_path, monkeypatch):
    from lab import proc
    from live import analog
    assert "scenario" in proc.JOBS
    runs = []
    monkeypatch.setattr(proc, "run", lambda job, d, log=print, pause=None, **k: runs.append((job, pause)) or {"ok": 1})
    (tmp_path / "lab").mkdir()
    (tmp_path / "lab" / "scenario_eval.json").write_text(json.dumps({"NIFTY": {"x": 1}, "_ts": 5}))
    sc = analog.Scenarios(types.SimpleNamespace(data_dir=tmp_path))
    pause = lambda: False
    sc.run_eval(log=lambda m: None, pause=pause)
    assert runs == [("scenario", pause)] and sc.eval["NIFTY"] == {"x": 1}      # re-read from the child's file
    seen = {}
    monkeypatch.setattr(analog, "eval_job", lambda d, log=print: seen.setdefault("d", d) or {"done": True})
    assert proc._inproc("scenario", tmp_path, print, lambda: False, {}) is not None and seen["d"] == tmp_path


def test_scenario_eval_job_writes_the_same_file(tmp_path, monkeypatch):
    import numpy as np
    import pandas as pd
    from live import analog
    import lab.pattern_study as PS
    for sym in analog.SYMS:
        rows = []
        for i, d in enumerate(pd.bdate_range("2025-01-01", periods=40)):
            t0 = dt.datetime.combine(d.date(), dt.time(9, 15), analog.IST).timestamp()
            for m in range(analog.N):
                p = 100 + i + m * 0.01
                rows.append((t0 + 60 * m, p, p + 0.1, p - 0.1, p, 0))
        (tmp_path / "history" / sym).mkdir(parents=True)
        pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).to_parquet(tmp_path / "history" / sym / "minutes.parquet")
    got = {}
    monkeypatch.setattr(PS, "run", lambda libs, d, log=print: got.update({s: len(l.days) for s, l in libs.items()}))
    monkeypatch.setattr(analog, "evaluate", lambda lib, n: {"n": len(lib.days)})
    out = analog.eval_job(tmp_path, log=lambda m: None)
    assert got == {"NIFTY": 40, "BANKNIFTY": 40} and out == {}      # under 300 days: the pattern study only, as before


# ---------------------------------------------------------------- Yahoo downloads
def test_yahoo_downloads_run_on_one_thread_one_at_a_time(monkeypatch):
    from live import world
    monkeypatch.setattr(world, "_YF_LOCK", threading.Lock())     # not shared with other tests' engines
    calls, gate = [], threading.Event()

    def download(ticks, **kw):
        calls.append(kw)
        gate.wait(2)
        return "df"
    yf = types.SimpleNamespace(download=download)
    th = threading.Thread(target=lambda: calls.append(world._yf(yf, ["^GSPC"], period="5d")))
    th.start()
    time.sleep(0.1)
    with pytest.raises(RuntimeError):                            # one already running: this one is skipped
        world._yf(yf, ["^N225"], wait=0, period="5d")
    gate.set()
    th.join(3)
    assert calls[0]["threads"] is False and calls[0]["timeout"] == 20 and calls[-1] == "df"
    src = (ROOT / "live" / "world.py").read_text() + (ROOT / "live" / "context.py").read_text()
    import re
    assert not re.search(r"threads=4[,)]", src)


# ---------------------------------------------------------------- pages asked again and again
def test_scorecard_and_past_days_are_kept_until_they_change(tmp_path, monkeypatch):
    from live.store import Store
    from webapp import live_api, memo
    s = Store(tmp_path / "live.db")
    n = {"sc": 0, "day": 0}
    e = types.SimpleNamespace(store=s, data_dir=tmp_path, paper=None, clock=types.SimpleNamespace(now=lambda: dt.datetime(2026, 11, 24, 10)),
                              session_day=lambda: dt.date(2026, 11, 24),
                              scorecard_view=lambda src: n.__setitem__("sc", n["sc"] + 1) or {"rows": [], "n": n["sc"]},
                              day_book=lambda day, open_pnl=None: n.__setitem__("day", n["day"] + 1) or {"day": day, "n": n["day"]})
    monkeypatch.setattr(live_api, "engine", lambda: e)
    for k in list(memo._mem):
        memo._mem.pop(k, None)
    app = FastAPI()
    app.include_router(live_api.router)
    c = TestClient(app)
    assert c.get("/api/live/scorecard").json() == c.get("/api/live/scorecard").json() and n["sc"] == 1
    c.get("/api/live/day/2026-11-23")
    c.get("/api/live/day/2026-11-23")
    assert n["day"] == 1                                         # a finished day: worked out once
    s.add_trade({"source": "upstox", "day": "2026-11-23", "sym": "NIFTY", "status": "closed", "net": 100.0, "entry": 1.0, "exit": 2.0})
    c.get("/api/live/day/2026-11-23")
    assert n["day"] == 2                                         # its trades changed: again


def test_alerts_page_draws_150_at_a_time():
    js = (ROOT / "webapp" / "static" / "live.js").read_text()
    assert "const AL_PAGE = 150;" in js and "shown.slice(0, AL_PAGE)" in js and 'id="al-more"' in js
