"""2.66.1 (Mandeep, 11 Nov 2026: "mac system is lagging and app is loading slow"): the live metrics read the
per-instrument deques while the feed thread appends to them ("deque mutated during iteration" 15 times on 11 Nov),
and the evening Lab jobs run in macOS background mode."""
import threading

from lab import proc
from live import analytics
from live.state import Bars, Inst as InstrumentState


def test_reads_survive_a_feed_thread_appending():
    inst, bars = InstrumentState("X"), Bars()
    stop = threading.Event()

    def feed():
        m = 0
        while not stop.is_set():
            m += 60
            inst.flow.append([m, 1.0, 1.0, 1])
            inst.hist.append((m, 1.0, 1, 1, 1))
            bars.update(m, 100.0 + (m % 7), m)

    t = threading.Thread(target=feed, daemon=True)
    t.start()
    try:
        import datetime as dt
        for _ in range(20000):
            inst.flow_window(5, 10 ** 9)
            inst.ago(3)
            bars.closes(20)
            analytics.opening_range(bars, dt.date(2026, 11, 11), dt.timezone.utc)
    finally:
        stop.set()
        t.join(2)


def test_lab_child_lowers_its_priority_and_uses_mac_background_mode(monkeypatch):
    calls = []
    monkeypatch.setattr(proc.os, "nice", lambda n: calls.append(("nice", n)))
    monkeypatch.setattr(proc.os, "setpriority", lambda *a: calls.append(("prio",) + a), raising=False)
    monkeypatch.setattr(proc.sys, "platform", "darwin")
    proc.lower_priority(10)
    assert calls[0] == ("nice", 10) and calls[1][0] == "prio" and calls[1][3] == 0x1000
    calls.clear()
    monkeypatch.setattr(proc.sys, "platform", "linux")
    proc.lower_priority(10)
    assert calls == [("nice", 10)]


def test_lab_child_still_runs():
    out = proc.run("echo", ".", log=lambda m: None, x=1)
    assert out
