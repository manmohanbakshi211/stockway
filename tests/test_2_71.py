"""2.71 (Mandeep, 12 Nov 2026): "paper trading is not fetching the options price as the real movement ... data from all
the sources as fast as possible, websockets where it can be". The Upstox feed reads in its own process (it was reset
every 10-15 minutes when the analysis kept the shared reader busy); open paper trades are checked against their exits
every 0.25 s; pages get live quotes over a websocket; the option chain shows streamed strikes live. Market data only."""
import asyncio
import datetime as dt
import json
import threading
import time
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from live import feedproc as FP
from live import stream as ST
from live import upstox_feed_pb2 as pb
from live.clock import IST
from live.paper import PaperBook
from live.store import Store

ROOT = Path(__file__).resolve().parents[1]


def _msg(prices: dict) -> bytes:
    fr = pb.FeedResponse()
    fr.type = 1
    fr.currentTs = int(time.time() * 1000)
    for k, p in prices.items():
        g = fr.feeds[k].firstLevelWithGreeks
        g.ltpc.ltp = p
        g.ltpc.ltt = int(time.time() * 1000)
        g.firstDepth.bidP, g.firstDepth.askP = p - 0.05, p + 0.05
    return fr.SerializeToString()


class FakeUpstox:
    """A local websocket server speaking the Upstox V3 feed (binary JSON subscriptions in, protobuf out)."""

    def __init__(self):
        self.subs, self.conns, self.port = [], 0, None
        self.ready = threading.Event()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run, daemon=True).start()
        assert self.ready.wait(10)

    def _run(self):
        import websockets
        asyncio.set_event_loop(self.loop)

        async def handler(ws):
            self.conns += 1
            async for m in ws:
                d = json.loads(m if isinstance(m, str) else m.decode())
                self.subs.append((self.conns, d["method"], sorted(d["data"]["instrumentKeys"]), d["data"].get("mode")))
                if d["method"] == "sub":
                    await ws.send(_msg({k: 100.0 + i for i, k in enumerate(d["data"]["instrumentKeys"])}))

        async def main():
            async with websockets.serve(handler, "127.0.0.1", 0) as srv:
                self.port = srv.sockets[0].getsockname()[1]
                self.ready.set()
                await asyncio.Future()
        self.loop.run_until_complete(main())


def _wait(cond, t=20.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_feed_process_streams_ticks_resubscribes_and_restarts():
    srv = FakeUpstox()
    got, status = [], []
    f = FP.ProcFeed("TEST", got.extend, status.append, ws_url=f"ws://127.0.0.1:{srv.port}")
    f.subscribe(["NSE_FO|1", "NSE_FO|2"], "option_greeks")              # before start: sent once connected
    f.start()
    try:
        assert _wait(lambda: len(got) >= 2), status
        assert {t["key"] for t in got} == {"NSE_FO|1", "NSE_FO|2"} and got[0]["bid"] is not None
        assert f.connected and f.msgs >= 1 and f.view()["mode"] == "process" and f.view()["alive"]
        assert f.proc.pid != __import__("os").getpid()                     # its own process
        f.subscribe(["NSE_EQ|X"], "full")
        assert _wait(lambda: any(t["key"] == "NSE_EQ|X" for t in got))
        assert f.counts() == {"full": 1, "option_greeks": 2, "ltpc": 0}
        old = f.proc.pid
        f.proc.kill()                                                     # the process dies: a new one, same subscriptions
        assert _wait(lambda: f.proc is not None and f.proc.pid != old and f.connected, 30), status
        assert _wait(lambda: any(c == srv.conns and m == "sub" for c, m, _, _ in srv.subs))
        again = {k for c, m, ks, _ in srv.subs if c == srv.conns and m == "sub" for k in ks}
        assert again == {"NSE_FO|1", "NSE_FO|2", "NSE_EQ|X"}
        assert any("starting it again" in s for s in status)
    finally:
        f.stop()
    assert _wait(lambda: not f.proc.is_alive(), 10)


def test_falls_back_to_the_in_process_reader(monkeypatch):
    from live import upstox as U
    started = []
    monkeypatch.setattr(U.UpstoxFeed, "start", lambda self: started.append(self))
    f = FP.ProcFeed("TEST", lambda t: None, lambda m: None)
    f.subscribe(["K1"], "ltpc")
    monkeypatch.setattr(f, "_spawn", lambda: 1 / 0)
    f.start()
    assert f.mode == "thread" and started and started[0].subs["ltpc"] == {"K1"}
    f.subscribe(["K2"], "full")
    assert started[0].subs["full"] == {"K2"} and f.counts()["full"] == 1


class Inst:
    def __init__(self, ltp, bid=None, ask=None, t=None, ltt=None, oi=None, vol=None):
        self.ltp, self.bid, self.ask, self.t, self.ltt, self.oi, self.vol, self.cp = ltp, bid, ask, t, ltt, oi, vol, None


def _book(tmp_path):
    st = Store(tmp_path / "p.db")
    logs = []
    pb_ = PaperBook(st, "upstox", {"capital": 500000, "auto": True}, logs.append)
    return st, pb_, logs


def _open(st, pb_, key="O1", entry=100.0):
    now = dt.datetime(2026, 11, 12, 10, 0, tzinfo=IST)
    px = {key: Inst(entry, entry - 0.1, entry + 0.1, t=now.timestamp(), ltt=int(now.timestamp() * 1000)), "U": Inst(25000.0)}
    state = types.SimpleNamespace(get=px.get)
    opt = {"key": key, "label": "NIFTY 25000 CE", "side": "CE", "lot": 65}
    tid = pb_._open(state, now, sym="NIFTY", kind="manual", opt=opt, entry=entry, ltp=entry, lots=1, direction=1, manual=True,
                    und_key="U")
    return tid, px, state, now


def test_fast_exit_check_writes_only_what_changed_and_never_closes_twice(tmp_path, monkeypatch):
    st, pb_, logs = _book(tmp_path)
    tid, px, state, now = _open(st, pb_)
    t = next(x for x in st.trades() if x["id"] == tid)
    writes = []
    orig = st.update_trade
    monkeypatch.setattr(st, "update_trade", lambda i, **f: (writes.append(f), orig(i, **f)))
    pb_.update(state, now, fast=True)                      # nothing moved: no write
    assert writes == []
    px["O1"] = Inst(101.0, 100.9, 101.1)
    pb_.update(state, now, fast=True)
    assert writes == [{"peak": 101.0}]                     # the new peak only (the trail needs it), not the price
    px["O1"] = Inst(t["target"] + 1, t["target"] + 0.9, t["target"] + 1.1)
    res = []
    ths = [threading.Thread(target=lambda: res.append(pb_.update(state, now, fast=True))) for _ in range(4)]
    ths += [threading.Thread(target=lambda: res.append(pb_.update(state, now)))]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    assert sum(len(r) for r in res) == 1 and sum("target" in m for m in logs) == 1
    assert next(x for x in st.trades() if x["id"] == tid)["reason"] == "target"


def test_engine_watcher_settings_and_wiring():
    from live import engine as E
    assert E.LIVE_DEFAULTS["feed_reader"] == "process" and E.LIVE_DEFAULTS["paper_watch_ms"] == 250
    src = (ROOT / "live" / "engine.py").read_text()
    assert "def _paper_watch" in src and 'name="paper-watch"' in src and "books.update_fast" in src
    assert src.count("ProcFeed(") == 1


def test_quotes_and_the_live_chain():
    st = {"O1": Inst(12.5, 12.4, 12.6, t=1000.0, ltt=999000, oi=5000, vol=900), "O2": Inst(8.0, t=500.0)}
    e = types.SimpleNamespace(running=True, state=types.SimpleNamespace(get=st.get, last_tick=1000.0))
    assert ST.quotes(e, ["O1", "NOPE"]) == {"O1": [12.5, 12.4, 12.6, 5000, 999.0]}
    assert ST.quotes(types.SimpleNamespace(running=False, state=None), ["O1"]) == {}
    chain = {"rows": [{"strike": 100, "CE": {"key": "O1", "ltp": 11.0, "cp": 10.0, "oi": 10}, "PE": {"key": "O2", "ltp": 7.0}}]}
    live = ST.live_chain(chain, e.state)
    ce, pe = live["rows"][0]["CE"], live["rows"][0]["PE"]
    assert ce["live"] and ce["ltp"] == 12.5 and ce["oi"] == 5000 and ce["chg_pct"] == 25.0
    assert "live" not in pe and pe["ltp"] == 7.0                           # 500 s old: the snapshot stays
    assert chain["rows"][0]["CE"]["ltp"] == 11.0 and live["live_n"] == 1   # the cached chain is not changed


def test_quotes_websocket(monkeypatch):
    from webapp import live_api
    st = {"O1": Inst(12.5, 12.4, 12.6, t=1.0, ltt=1000)}
    e = types.SimpleNamespace(running=True, state=types.SimpleNamespace(get=st.get))
    monkeypatch.setattr(live_api, "engine", lambda: e)
    monkeypatch.setattr(ST, "cfg", lambda: {**ST.DEFAULTS, "quotes_ms": 100, "heartbeat_s": 0.3})
    app = FastAPI()
    app.include_router(live_api.router)
    cl = TestClient(app)
    with cl.websocket_connect("/ws/quotes") as ws:
        ws.send_json({"keys": ["O1", "O9"]})
        got = ws.receive_json()
        while "hb" in got:
            got = ws.receive_json()
        assert got["q"]["O1"][:3] == [12.5, 12.4, 12.6] and "O9" not in got["q"]
        st["O1"] = Inst(13.0, 12.9, 13.1, t=2.0, ltt=2000)
        got = ws.receive_json()
        while "hb" in got:
            got = ws.receive_json()
        assert got["q"]["O1"][0] == 13.0
    assert ST.OPEN.get("q", 0) == 0


def test_new_code_never_places_orders():
    for f in ("live/feedproc.py",):
        src = (ROOT / f).read_text().lower()
        for bad in ("place_order", "modify_order", "cancel_order", "/order", "funds", "holdings", "positions"):
            assert bad not in src, (f, bad)
