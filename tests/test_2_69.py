"""2.69 (Mandeep, 12 Nov 2026): every paper account added up and an Exit / Exit all in every account; seconds candles
on the main chart; the Live page's prices streamed as fast as the chart's. Paper only; the app's own prices only."""
import datetime as dt
import types
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from live import books as B
from live import secbars as SBM
from live import stream as ST
from live.clock import IST
from live.paper import PaperBook
from live.store import Store

ROOT = Path(__file__).resolve().parents[1]


def _t(h, m, s=0, day=11):
    return dt.datetime(2026, 11, day, h, m, s, tzinfo=IST).timestamp()


class Inst:
    def __init__(self, ltp, ltt=None, cp=None):
        self.ltp, self.bid, self.ask, self.ltt, self.t, self.cp = ltp, ltp, ltp, ltt, None, cp


def test_seconds_candles_from_prices():
    sb = SBM.SecBars()
    for k, p in enumerate([100, 101, 99, 102, 103, 98, 104]):          # one price every 4 s from 09:15:00
        sb.add("NIFTY", _t(9, 15) + 4 * k, p)
    sb.add("NIFTY", _t(9, 15) + 1, 500)                                 # out of order: ignored
    rows = sb.rows("NIFTY", 15)                                          # 0-12 s, 16-24 s
    assert [r[0] for r in rows] == [_t(9, 15), _t(9, 15, 15)]
    assert rows[0][1:] == [100, 102, 99, 102] and rows[1][1:] == [103, 104, 98, 104]
    c = sb.forming("NIFTY", "15s")
    assert c["time"] == int(_t(9, 15, 15) + 19800) and [c["open"], c["high"], c["low"], c["close"]] == [103, 104, 98, 104]
    d = sb.candles("NIFTY", "5s")
    assert d["intraday"] and len(d["candles"]) == 5 and d["candles"][-1]["close"] == 104     # 0-4, 8, 12, 16, 20-24 s
    sb.add("NIFTY", _t(9, 15, day=12), 200)                              # a new day starts a new buffer
    assert sb.rows("NIFTY", 15) == [[_t(9, 15, day=12), 200, 200, 200, 200]]


def _engine(running=True):
    st = {"NSE_INDEX|Nifty 50": Inst(124.5, ltt=int(_t(9, 39, 30) * 1000), cp=120.0),
          "NSE_INDEX|Nifty Bank": Inst(600.0, ltt=int(_t(9, 39, 31) * 1000), cp=600.0)}
    uni = types.SimpleNamespace(underlyings={"NIFTY": types.SimpleNamespace(key="NSE_INDEX|Nifty 50"),
                                             "BANKNIFTY": types.SimpleNamespace(key="NSE_INDEX|Nifty Bank")})
    return types.SimpleNamespace(running=running, state=types.SimpleNamespace(get=st.get), uni=uni,
                                 charts=types.SimpleNamespace(today_bars=lambda s: []))


def test_engine_sample_and_stream_frame_for_seconds(monkeypatch):
    sb = SBM.SecBars()
    monkeypatch.setattr(SBM, "SB", sb)
    e = _engine()
    sb.sample(e, dt.datetime.fromtimestamp(_t(9, 39, 31), IST))
    assert sb.buf["NIFTY"][-1] == (_t(9, 39, 30), 124.5) and sb.buf["BANKNIFTY"][-1][1] == 600.0
    f = ST.frame(e, "NIFTY", "15s")
    assert f["candle"]["time"] == int(_t(9, 39, 30) + 19800) and f["candle"]["close"] == 124.5 and "NIFTY" in sb.watch
    assert ST.prices(e, ["NIFTY", "BANKNIFTY", "NOPE"]) == {"NIFTY": [124.5, 3.75, _t(9, 39, 30)],
                                                             "BANKNIFTY": [600.0, 0.0, _t(9, 39, 31)]}
    assert ST.prices(_engine(running=False), ["NIFTY"]) == {}


def test_seconds_candles_api_and_price_stream(monkeypatch):
    from webapp import live_api
    sb = SBM.SecBars()
    monkeypatch.setattr(SBM, "SB", sb)
    e = _engine()
    monkeypatch.setattr(live_api, "engine", lambda: e)
    app = FastAPI()
    app.include_router(live_api.router)
    cl = TestClient(app)
    r0 = cl.get("/api/live/candles/NIFTY?tf=15s").json()                          # no prices yet: a clear answer
    assert r0["candles"] == [] and "scanner" in r0["message"]
    sb.add("NIFTY", _t(9, 20), 100.0)
    sb.add("NIFTY", _t(9, 20, 20), 101.0)
    r = cl.get("/api/live/candles/NIFTY?tf=15s").json()
    assert r["tf"] == "15s" and [c["close"] for c in r["candles"]] == [100.0, 101.0]
    assert cl.get("/api/live/candles/NIFTY?tf=15s&before=1").json()["candles"] == []
    assert cl.get("/api/live/candles/NIFTY?tf=7s").status_code == 400
    monkeypatch.setattr(ST, "cfg", lambda: {"every_ms": 50, "max_streams": 2, "heartbeat_s": 0.3, "reconcile_s": 10,
                                            "prices_ms": 100, "max_price_streams": 2})
    with cl.websocket_connect("/ws/prices") as ws:
        ws.send_json({"syms": ["nifty", "BANKNIFTY"]})
        got = ws.receive_json()
        while "hb" in got:
            got = ws.receive_json()
        assert got["px"]["NIFTY"][0] == 124.5 and got["px"]["BANKNIFTY"][0] == 600.0
        assert "hb" in ws.receive_json()                                         # nothing changed: only a heartbeat


MAIN = {"capital": 500000, "risk_pct": 1.0, "max_daily_loss_pct": 3.0, "target_pct": 20, "stop_pct": 15,
        "max_hold_min": 60, "exit_time": "15:15", "no_entry_after": "14:45", "min_severity": 2, "max_open": 5,
        "max_trades_day": 10, "max_lots": 5, "kinds": "all", "auto": True}


def _alert(i, key):
    return {"id": i, "sym": f"S{i}", "kind": "orb", "severity": 3, "direction": "up", "title": "t", "und_key": "UND",
            "option": {"key": key, "label": f"S{i} 100 CE", "side": "CE", "lot": 100},
            "data": {"focus": True, "score": 90, "track": {"decided": 30, "hit_rate": 60, "avg_o60": 2.0},
                     "risk": {"verdict": "PASS", "lots": 1}}}


def test_every_account_added_up_and_exit_buttons(tmp_path, monkeypatch):
    from webapp import live_api
    st = Store(tmp_path / "t.db")
    now = dt.datetime(2026, 11, 9, 10, 0, tzinfo=IST)
    ms = int(now.timestamp() * 1000) - 1000
    px = {"O1": Inst(10.0, ms), "O2": Inst(10.0, ms), "UND": Inst(500.0, ms)}
    state = types.SimpleNamespace(get=px.get)
    main = PaperBook(st, "upstox", dict(MAIN), log=lambda m: None)
    bs = B.Books("upstox", st, dict(MAIN), None, log=lambda m: None)
    assert main.try_open(_alert(1, "O1"), state, now)
    assert bs.consider(_alert(2, "O2"), state, now)["best"] == "taken"
    px["O1"], px["O2"] = Inst(12.0, ms), Inst(11.0, ms)
    e = types.SimpleNamespace(paper=main, books=bs, source="upstox", state=state, store=st, running=True,
                              clock=types.SimpleNamespace(now=lambda: now), cfg=lambda: {"live": {"source": "upstox", "paper": MAIN}})
    monkeypatch.setattr(live_api, "engine", lambda: e)
    app = FastAPI()
    app.include_router(live_api.router)
    cl = TestClient(app)
    tot = cl.get("/api/live/paper/total").json()
    ids = {a["id"] for a in tot["accounts"]}
    assert {"main", "best"} <= ids and tot["total"]["open"] == len([t for t in st.trades() if t["status"] == "open"])
    assert tot["total"]["net"] == round(sum(a["booked"] + a["open_pnl"] for a in tot["accounts"] if "error" not in a), 2)
    assert tot["total"]["open_pnl"] > 0
    bt = next(t for t in st.trades(source="upstox#best") if t["status"] == "open")
    assert cl.post(f"/api/live/paper/books/best/{bt['id']}/close").json()["reason"] == "closed by you"
    assert next(t for t in st.trades(source="upstox#best") if t["id"] == bt["id"])["status"] == "closed"
    assert cl.post(f"/api/live/paper/books/best/{bt['id']}/close").status_code == 404
    assert cl.post("/api/live/paper/books/nope/close_all").status_code == 404
    for bid in bs.books:                                       # every other account can be emptied at once
        assert cl.post(f"/api/live/paper/books/{bid}/close_all").json()["ok"]
    assert all(t["status"] == "closed" for t in st.trades() if t["source"] != "upstox")
    assert any(t["status"] == "open" for t in st.trades(source="upstox"))                 # the main account untouched
    after = cl.get("/api/live/paper/total").json()["total"]
    assert after["trades"] >= 1


def test_new_code_never_places_orders():
    for f in ("live/secbars.py",):
        src = (ROOT / f).read_text().lower()
        for bad in ("place_order", "modify_order", "cancel_order", "requests.", "upstoxfeed"):
            assert bad not in src, (f, bad)
