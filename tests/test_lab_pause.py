"""2.80: automatic Lab work pauses in market hours: a new job waits, a running child is frozen and continues after
(nothing lost), Stop still works while frozen, runs started by hand are never paused."""
import datetime as dt
import threading
import time

import pytest

from lab import proc


def _run_bg(**kw):
    out, logs = {}, []

    def go():
        try:
            out["r"] = proc.run("echo", "data", log=logs.append, **kw)
        except Exception as e:
            out["e"] = e
    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t, out, logs


@pytest.mark.skipif(not hasattr(__import__("signal"), "SIGSTOP"), reason="needs SIGSTOP")
def test_a_running_job_is_frozen_and_continues_after():
    flag = [False]
    t, out, logs = _run_bg(pause=lambda: flag[0], n=8, sleep=0.4)
    deadline = time.time() + 30
    while not any(x.startswith("line 1") for x in logs) and time.time() < deadline:
        time.sleep(0.05)
    flag[0] = True                                    # the market opens
    time.sleep(2.5)
    assert any("paused for market hours" in x for x in logs) and proc.STATE.get("paused")
    n_frozen = sum(x.startswith("line") for x in logs)
    time.sleep(2.0)
    assert sum(x.startswith("line") for x in logs) == n_frozen       # nothing ran while frozen
    flag[0] = False                                   # the market closed
    t.join(30)
    assert out.get("r", {}).get("done") == 8 and any("continuing after the close" in x for x in logs)
    assert not proc.STATE and not proc._FROZEN


def test_a_new_job_waits_and_stop_still_works(monkeypatch):
    real = time.sleep
    monkeypatch.setattr(proc.time, "sleep", lambda s: real(min(s, 0.05)))
    stop = [False]
    t, out, logs = _run_bg(pause=lambda: True, should_stop=lambda: stop[0], n=2)
    time.sleep(0.5)
    assert any("waiting for the market to close" in x for x in logs) and proc.STATE.get("waiting")
    stop[0] = True
    t.join(10)
    assert "Stopped while waiting" in str(out.get("e")) and not proc.STATE


def test_runs_by_hand_are_never_paused():
    r = proc.run("echo", "data", log=lambda m: None, n=2)          # no pause given: as before
    assert r["done"] == 2


def test_the_engine_pauses_only_in_an_nse_session(monkeypatch):
    from live import engine as EN
    from live.clock import IST

    class E:
        cfg = staticmethod(lambda: {"lab": {}})
    at = {}

    class FakeDT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return at["now"]
    monkeypatch.setattr(EN.dt, "datetime", FakeDT)
    f = EN.LiveEngine.lab_paused
    for when, want in ((dt.datetime(2026, 11, 23, 8, 59), False), (dt.datetime(2026, 11, 23, 9, 0), True),
                       (dt.datetime(2026, 11, 23, 15, 34), True), (dt.datetime(2026, 11, 23, 15, 35), False),
                       (dt.datetime(2026, 11, 22, 11, 0), False)):              # a Sunday: never
        at["now"] = when.replace(tzinfo=IST)
        assert f(E()) is want, when
    E.cfg = staticmethod(lambda: {"lab": {"pause_in_market": False}})
    at["now"] = dt.datetime(2026, 11, 23, 11, 0, tzinfo=IST)
    assert f(E()) is False
