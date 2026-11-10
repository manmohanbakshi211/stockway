"""2.51: the chart's live stream (Mandeep: "connect the websocket for charting, we need tick-by-tick and price at the
same time"). An open chart gets each new price from the app over a websocket; it reads the app's own prices only."""
import datetime as dt
import types
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from live import stream as ST
from live.clock import IST

ROOT = Path(__file__).resolve().parents[1]


def _t(h, m, s=0):
    return dt.datetime(2026, 11, 11, h, m, s, tzinfo=IST).timestamp()


def _rows():
    rows = []
    for k in range(25):                                   # 09:15 .. 09:39, one bar a minute
        t = _t(9, 15) + k * 60
        rows.append([t, 100 + k, 101 + k, 99 + k, 100.5 + k, 10])
    return rows


def test_forming_candle_matches_the_chart_bars():
    c = ST.forming(_rows(), "5m")                         # last bar 09:39 -> the 09:35 candle
    assert c["time"] == int(_t(9, 35) + 19800) and c["open"] == 120 and c["close"] == 124.5 and c["high"] == 125
    assert c["low"] == 119 and c["volume"] == 50
    c15 = ST.forming(_rows(), "15m")                      # 09:30 candle
    assert c15["time"] == int(_t(9, 30) + 19800) and c15["open"] == 115
    c30 = ST.forming(_rows(), "30m")                      # 30-min candles start at 09:15 (NSE), not 09:00 / 09:30
    assert c30["time"] == int(_t(9, 15) + 19800) and c30["open"] == 100 and c30["volume"] == 250
    assert ST.forming(_rows(), "1D") is None and ST.forming([], "5m") is None
    from live.mtf import resample
    r = resample(_rows(), 5)[-1]                          # the chart's own resampling gives the same candle
    assert [r[1], r[2], r[3], r[4], r[5]] == [c["open"], c["high"], c["low"], c["close"], c["volume"]]


class Inst:
    def __init__(self, ltp, ltt=None, t=None, cp=None):
        self.ltp, self.ltt, self.t, self.cp = ltp, ltt, t, cp


def _engine(running=True):
    st = {"NSE_INDEX|Nifty 50": Inst(124.5, ltt=int(_t(9, 39, 30) * 1000), t=_t(9, 39, 31), cp=120.0)}
    uni = types.SimpleNamespace(underlyings={"NIFTY": types.SimpleNamespace(key="NSE_INDEX|Nifty 50")})
    return types.SimpleNamespace(running=running, state=types.SimpleNamespace(get=st.get), uni=uni,
                                 charts=types.SimpleNamespace(today_bars=lambda s: _rows()))


def test_frame_has_price_exchange_time_and_candle():
    f = ST.frame(_engine(), "NIFTY", "5m")
    assert f["p"] == 124.5 and f["tt"] == _t(9, 39, 30) and f["chg_pct"] == 3.75 and f["candle"]["close"] == 124.5
    assert ST.frame(_engine(running=False), "NIFTY", "5m") is None
    assert ST.frame(_engine(), "UNKNOWN", "5m") is None
    assert "candle" not in ST.frame(_engine(), "NIFTY", "1D")


def test_websocket_sends_prices_and_switches_symbol(monkeypatch):
    from webapp import live_api
    e = _engine()
    monkeypatch.setattr(live_api, "engine", lambda: e)
    monkeypatch.setattr(ST, "cfg", lambda: {"every_ms": 50, "max_streams": 2, "heartbeat_s": 0.3, "reconcile_s": 10})
    app = FastAPI()
    app.include_router(live_api.router)
    with TestClient(app).websocket_connect("/ws/ticks") as ws:
        ws.send_json({"sym": "nifty", "tf": "5m"})
        got = ws.receive_json()
        while "hb" in got:
            got = ws.receive_json()
        assert got["sym"] == "NIFTY" and got["p"] == 124.5 and got["candle"]["time"] == int(_t(9, 35) + 19800)
        assert ST.OPEN["n"] == 1
        hb = ws.receive_json()                              # nothing changed: only a heartbeat
        assert hb.get("hb") and hb["running"] is True
    import time
    time.sleep(0.2)
    assert ST.OPEN["n"] == 0


def test_stream_never_connects_to_a_broker():
    src = (ROOT / "live" / "stream.py").read_text()
    for bad in ("upstox", "UpstoxFeed", "dhan", "requests", "websocket.create", "place_order", "order"):
        assert bad not in src.lower().split('"""', 2)[-1], bad   # past the docstring: no broker, no orders
