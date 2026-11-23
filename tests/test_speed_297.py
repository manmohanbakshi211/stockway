"""2.97 (Mandeep, 23 Nov 2026, 10:27: the Live page and Paper trades still 2-4 s after 2.96): a read-only look at where the
running app's time goes, and the side bar's paper figure shared by every open page."""
import inspect
import threading
import time

from webapp import sampler


def _spin(stop):
    x = 0
    while not stop.is_set():
        x += sum(i * i for i in range(2000))


def test_the_look_finds_the_busy_thread_and_leaves_out_the_waiting_one():
    stop = threading.Event()
    busy = threading.Thread(target=_spin, args=(stop,), name="busy-worker", daemon=True)
    idle = threading.Thread(target=stop.wait, name="idle-worker", daemon=True)
    busy.start()
    idle.start()
    try:
        w = sampler.look(2, every_ms=5)
    finally:
        stop.set()
    rows = {r["thread"]: r for r in w["rows"]}
    assert "busy-worker" in rows and rows["busy-worker"]["busy_pct"] > 50
    assert any("_spin" in t["code"] for t in rows["busy-worker"]["top"])
    assert "idle-worker" not in rows
    assert w["samples"] > 100 and w["busy_threads_avg"] >= 1


def test_one_look_at_a_time_and_wired_to_the_system_page():
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("a", sampler.look(2)))
    th.start()
    time.sleep(0.3)
    assert sampler.look(2) == {"busy": True, "note": "A look is already running."}
    th.join()
    from webapp import live_api, server
    assert '@app.get("/api/debug/where")' in inspect.getsource(server)
    assert 'memo.memo("paper_mini", None, _paper_mini_now, ttl=2)' in inspect.getsource(live_api.live_paper_mini)
    js = open("webapp/static/live.js").read()
    assert "function wherePanel(w)" in js and 'api("/api/debug/where?secs=20")' in js and 'id="sy-where"' in js
