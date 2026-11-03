"""2.33: health monitor: gaps (Mac asleep / app closed), scanner down, recording stuck, errors, holidays, summary."""
import collections
import datetime as dt
import types

from live import health as H
from live.clock import IST


class _Store(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)

    def put(self, k, v):
        self[k] = v


def _engine(tmp_path, running=True, status=None):
    sent = []
    e = types.SimpleNamespace(store=_Store(), telegram=types.SimpleNamespace(send=sent.append), log=lambda m: None,
                              data_dir=tmp_path, running=running, status=status or {"state": "running"},
                              cfg=lambda: {"live": {"source": "upstox", "auto_start": True}}, manual_stop_day=None,
                              recorder=types.SimpleNamespace(rows=0, counts={}), logs=collections.deque(maxlen=400),
                              source="upstox", bench=None)
    return e, sent


def _at(h, m, day=dt.date(2026, 11, 4)):
    return dt.datetime.combine(day, dt.time(h, m), IST)


def test_holidays_and_market_overlap():
    c = dict(H.DEFAULTS, holidays={"2026": ["2026-11-10"]})
    assert H.is_trading_day(dt.date(2026, 11, 4), c) and not H.is_trading_day(dt.date(2026, 11, 10), c)
    assert not H.is_trading_day(dt.date(2026, 11, 7), c)                      # Saturday
    a, b = _at(10, 0).timestamp(), _at(10, 30).timestamp()
    assert H.market_overlap(a, b, c) == 1800
    assert H.market_overlap(_at(16, 0).timestamp(), _at(9, 0, dt.date(2026, 11, 5)).timestamp(), c) == 0   # overnight
    assert H.market_overlap(_at(9, 0, dt.date(2026, 11, 10)).timestamp(), _at(15, 0, dt.date(2026, 11, 10)).timestamp(), c) == 0


def test_a_sleeping_mac_is_reported_once_and_saved(tmp_path, monkeypatch):
    e, sent = _engine(tmp_path)
    h = H.Health(e)
    monkeypatch.setattr(H.time, "time", lambda: _at(10, 0).timestamp())
    h.tick(_at(10, 0))
    monkeypatch.setattr(H.time, "time", lambda: _at(10, 40).timestamp())       # 40 minutes later: the Mac was asleep
    h.tick(_at(10, 40))
    assert any("not running from 10:00 to 10:40" in s and "40 market minutes" in s for s in sent)
    g = e.store["health:gaps:2026-11-04"]
    assert len(g) == 1 and g[0]["market_mins"] == 40
    n = len(sent)
    monkeypatch.setattr(H.time, "time", lambda: _at(10, 41).timestamp())
    h.tick(_at(10, 41))
    assert len(sent) == n                                                        # nothing new
    h2 = H.Health(e)                                                             # app restarted later
    assert h2.last == e.store["health:last_seen"]


def test_scanner_down_and_recording_stuck(tmp_path, monkeypatch):
    e, sent = _engine(tmp_path, running=False, status={"state": "login_needed", "message": "Log in with Upstox"})
    h = H.Health(e)
    for m in (20, 21, 22, 24):
        monkeypatch.setattr(H.time, "time", lambda m=m: _at(9, m).timestamp())
        h.tick(_at(9, m))
    assert sum("live scanner is not running" in s and "Log in with Upstox" in s for s in sent) == 1
    e2, sent2 = _engine(tmp_path, running=True)
    h2 = H.Health(e2)
    for m in (30, 31, 33, 36):
        monkeypatch.setattr(H.time, "time", lambda m=m: _at(10, m).timestamp())
        h2.tick(_at(10, m))
    assert any("recording has not grown since 10:30" in s for s in sent2)


def test_no_nagging_on_a_holiday_or_weekend(tmp_path, monkeypatch):
    e, sent = _engine(tmp_path, running=False, status={"state": "stopped"})
    h = H.Health(e)
    sat = dt.date(2026, 11, 7)
    for m in (20, 25, 30):
        monkeypatch.setattr(H.time, "time", lambda m=m: _at(10, m, sat).timestamp())
        h.tick(_at(10, m, sat))
    assert not sent


def test_error_burst_and_the_daily_summary(tmp_path, monkeypatch):
    e, sent = _engine(tmp_path)
    h = H.Health(e)
    monkeypatch.setattr(H.time, "time", lambda: _at(11, 0).timestamp())
    for i in range(15):
        e.logs.append(f"11:00:{i:02d}  Option chain RELIANCE failed: timeout")
    h.tick(_at(11, 0))
    assert any("many problems" in s and "Option chain RELIANCE failed" in s for s in sent)
    e.recorder.counts = {"chains": 400, "news": 90, "candles": 1200}
    monkeypatch.setattr(H.time, "time", lambda: _at(15, 41).timestamp())
    h.tick(_at(15, 41))
    s = [x for x in sent if x.startswith("📊")]
    # the jump from 11:00 to 15:41 is itself a gap (the monitor didn't run): the summary says so
    assert len(s) == 1 and "400 option chains" in s[0] and "Gaps: 1 (270 market minutes lost)" in s[0]
    monkeypatch.setattr(H.time, "time", lambda: _at(15, 42).timestamp())
    h.tick(_at(15, 42))
    assert len([x for x in sent if x.startswith("📊")]) == 1                    # once a day


def test_keep_awake_only_in_market_hours_on_a_mac(tmp_path, monkeypatch):
    started = []

    class P:
        def __init__(self, args, **k):
            started.append(args)
            self.alive = True

        def poll(self):
            return None if self.alive else 0

        def terminate(self):
            self.alive = False
    monkeypatch.setattr(H.sys, "platform", "darwin")
    monkeypatch.setattr(H.subprocess, "Popen", P)
    monkeypatch.setattr(H.subprocess, "run", lambda *a, **k: types.SimpleNamespace(stdout="Now drawing from 'AC Power'"))
    e, sent = _engine(tmp_path)
    h = H.Health(e)
    for hh, mm in ((8, 30), (9, 0), (9, 1), (16, 0)):
        monkeypatch.setattr(H.time, "time", lambda hh=hh, mm=mm: _at(hh, mm).timestamp())
        h.tick(_at(hh, mm))
        if (hh, mm) == (8, 30):
            assert not started
        if (hh, mm) == (9, 1):
            assert len(started) == 1 and started[0][0] == "caffeinate" and "-w" in started[0]
    assert h.awake is None and h.state["keep_awake"].startswith("off")
    assert not any("battery" in s for s in sent)
