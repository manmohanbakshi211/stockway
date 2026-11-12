"""2.72 (Mandeep, 12 Nov 2026: "ek baar sabhi jagah puri app mein check karlo aisi delays dobara na aaye"): the network
calls the analysis loop used to wait for run in a background worker; the Lab rules' history is fetched before the first
scan; every part of the analysis pass is timed and a slow pass names its slowest parts; prices falling behind Upstox
raise a warning; new alerts and paper changes are pushed to the pages. Nothing about the analysis itself changes."""
import collections
import datetime as dt
import threading
import time
import types

from fastapi import FastAPI
from fastapi.testclient import TestClient

from live import engine as E
from live import paper as PP
from live import stream as ST
from live.clock import IST
from live.store import Store


def _eng():
    e = object.__new__(E.LiveEngine)
    e.timers = collections.defaultdict(float)
    e.clock = types.SimpleNamespace(ts=time.time, now=lambda: dt.datetime.now(IST))
    e.status, e.watch = {}, {"stalled": False, "since": None}
    e._perm_err, e._prof, e._loop_hist = None, {}, collections.deque(maxlen=300)
    e.logs = []
    e.log = e.logs.append
    e.telegram = types.SimpleNamespace(sent=[], send=lambda m: e.telegram.sent.append(m))
    return e


def _wait(cond, t=5.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_network_work_runs_in_the_background_once_and_a_login_error_reaches_the_loop():
    e = _eng()
    gate, ran = threading.Event(), []

    def slow(sym):
        gate.wait(5)
        ran.append(sym)
    t0 = time.perf_counter()
    assert e._bg("option_chain:NIFTY", slow, "NIFTY") is True
    assert e._bg("option_chain:NIFTY", slow, "NIFTY") is False            # already queued: not twice
    assert time.perf_counter() - t0 < 0.05                                  # the loop never waits
    gate.set()
    assert _wait(lambda: ran == ["NIFTY"] and not e._bg_pending)
    assert e._bg("option_chain:NIFTY", slow, "NIFTY") is True              # done: can be queued again
    assert _wait(lambda: len(ran) == 2)

    def expired():
        raise PermissionError("Upstox login expired. Log in again.")
    e._bg("seed:x", expired)
    assert _wait(lambda: e._perm_err is not None)
    e.stop_evt = threading.Event()
    e.source = "sim"
    e.cfg = lambda: {"live": {}}
    try:
        E.LiveEngine._loop(e)
        raised = False
    except PermissionError:
        raised = True
    assert raised and e._perm_err is None


class Inst:
    def __init__(self, ltp, t):
        self.ltp, self.t = ltp, t


def test_a_new_option_streams_at_once_and_its_rest_quote_never_overwrites_a_newer_price():
    e = _eng()
    subs, applied = [], []

    class Rest:
        def quotes(self, keys):
            time.sleep(0.3)                                                  # a slow REST answer
            return {k: {"last_price": 90.0, "prev_close_price": 80.0} for k in keys}
    e.rest = Rest()
    e.opt_subs = set()
    e.feed = types.SimpleNamespace(subscribe=lambda keys, mode: subs.append((tuple(keys), mode)))
    insts = {}
    e.state = types.SimpleNamespace(get=insts.get, set_baselines=lambda b: applied.append(("base", b)),
                                    apply=lambda ticks, ts: applied.append(("ticks", ticks)))
    t0 = time.perf_counter()
    e._keep_live("OPT1")
    assert time.perf_counter() - t0 < 0.1 and subs == [(("OPT1",), "option_greeks")] and "OPT1" in e.opt_subs
    insts["OPT1"] = Inst(101.0, time.time() + 1)                            # the live feed's price arrives first
    assert _wait(lambda: len(applied) == 2)
    assert applied[0][0] == "base" and applied[0][1]["OPT1"]["cp"] == 80.0
    assert applied[1] == ("ticks", [])                                      # the older REST price is not applied


def test_slow_pass_names_its_slowest_parts_and_delay_warnings():
    e = _eng()
    e._prof = {"Analysis of every symbol": 1.9, "Setup / Lab rules scan": 0.6, "Paper trades": 0.01}
    e._loop_done(2.6)
    assert e.status["loop_ms"] == 2600 and e.status["slow_parts"]["parts"][0] == ["Analysis of every symbol", 1900]
    assert any("Slow analysis pass: 2.6 s" in m for m in e.logs)
    e._loop_done(0.3)
    assert e.status["loop_ms"] == 300
    e.feed = types.SimpleNamespace(lag_ms=7000.0)
    e._delay_watch()
    assert not e.telegram.sent                                               # not yet: 30 s first
    e.watch["lag_since"] -= 31
    e._delay_watch()
    assert e.status["warning"].startswith("Live prices are 7 s behind") and len(e.telegram.sent) == 1
    e._delay_watch()
    assert len(e.telegram.sent) == 1                                         # once
    e.feed.lag_ms = 800.0
    e._delay_watch()
    assert "warning" not in e.status and "on time again" in e.telegram.sent[-1]


def test_guard_times_only_the_loop_thread():
    e = _eng()
    out = {}

    def run():
        e._guard("Paper trades", time.sleep, 0.02)
        out["p"] = dict(e._prof)
    th = threading.Thread(target=run, name="live-engine")
    th.start()
    th.join()
    assert out["p"]["Paper trades"] >= 0.015
    e._prof = {}
    e._guard("Paper fast exits", time.sleep, 0.01)                         # another thread: not counted in the pass
    assert e._prof == {}


def test_paper_version_bumps_on_open_and_close(tmp_path):
    st = Store(tmp_path / "p.db")
    b = PP.PaperBook(st, "upstox", {"capital": 500000, "auto": True}, lambda m: None)
    now = dt.datetime(2026, 11, 12, 10, 0, tzinfo=IST)
    px = {"O1": types.SimpleNamespace(ltp=100.0, bid=99.9, ask=100.1, t=now.timestamp(), ltt=None)}
    state = types.SimpleNamespace(get=px.get)
    v0 = PP.VER[0]
    tid = b._open(state, now, sym="NIFTY", kind="manual", opt={"key": "O1", "label": "N", "side": "CE", "lot": 65},
                  entry=100.0, ltp=100.0, lots=1, direction=1, manual=True)
    assert PP.VER[0] == v0 + 1
    b.close_manual(tid, state, now)
    assert PP.VER[0] == v0 + 2


def test_events_websocket_pushes_new_alerts_and_paper_changes(monkeypatch):
    from webapp import live_api
    e = types.SimpleNamespace(alerts_mem=collections.deque([{"id": 5, "sym": "NIFTY", "title": "old"}]))
    monkeypatch.setattr(live_api, "engine", lambda: e)
    monkeypatch.setattr(ST, "cfg", lambda: {**ST.DEFAULTS, "events_ms": 100, "heartbeat_s": 0.3})
    app = FastAPI()
    app.include_router(live_api.router)
    with TestClient(app).websocket_connect("/ws/events") as ws:
        ws.send_json({"since": 5})
        time.sleep(0.2)
        e.alerts_mem.appendleft({"id": 6, "sym": "NIFTY", "title": "new one", "severity": 3})
        got = ws.receive_json()
        while "hb" in got:
            got = ws.receive_json()
        assert [a["id"] for a in got["alerts"]] == [6]
        PP.VER[0] += 1
        got = ws.receive_json()
        while "hb" in got:
            got = ws.receive_json()
        assert got["pv"] == PP.VER[0]
    assert ST.OPEN.get("e", 0) == 0


def test_lab_rules_history_is_fetched_before_the_first_scan():
    from lab import factory
    from live.chartdata import ChartData
    cd = object.__new__(ChartData)
    tf = sorted(factory.TF_FACTS)[0]
    calls = []
    cd._tf_hist = lambda sym, day: calls.append(sym) or [1]
    cd._bench_rules = lambda: []
    cd._bench_rules_stocks = lambda: []
    stocks = [types.SimpleNamespace(symbol="SBIN"), types.SimpleNamespace(symbol="TCS")]
    cd.e = types.SimpleNamespace(factory_rules=lambda: [{"conds": [[tf, ">", 0]], "syms": [factory.STOCKS]}],
                                 factory_rules_5y=lambda: [], uni=types.SimpleNamespace(stocks=stocks))
    assert cd.warm(dt.date(2026, 11, 12)) == 4 and calls == ["NIFTY", "BANKNIFTY", "SBIN", "TCS"]
    cd.e.factory_rules = lambda: [{"conds": [["rsi", ">", 50]], "syms": ["NIFTY"]}]
    calls.clear()
    assert cd.warm(dt.date(2026, 11, 12)) == 0 and calls == []              # no rule needs it: nothing fetched


def test_the_loop_no_longer_calls_the_network_directly():
    src = (E.__file__ and open(E.__file__).read())
    loop = src[src.index("    def _loop(self):"):src.index("    def _loop_done(")]
    assert "self.chain(u.symbol" not in loop and "self._next_expiry_chain(sym)" not in loop and "self.rest." not in loop
    rec = src[src.index("    def _recenter(self):"):src.index("    def _paper_watch(")]
    assert "self.rest.quotes" not in rec
