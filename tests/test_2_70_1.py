"""2.70.1 (Mandeep, 12 Nov 2026, KALYANKJIL 15s chart empty): opening a seconds chart starts that symbol's per-second
prices at once (before, only the indices were kept, and a stock's chart never opened its stream while empty)."""
import datetime as dt
import types

from fastapi import FastAPI
from fastapi.testclient import TestClient

from live import secbars as SBM
from live.clock import IST


class Inst:
    def __init__(self, ltp, ltt):
        self.ltp, self.bid, self.ask, self.ltt, self.t, self.cp = ltp, ltp, ltp, ltt, None, ltp


def test_a_stocks_seconds_chart_starts_collecting_on_open(monkeypatch):
    from webapp import live_api
    sb = SBM.SecBars()
    monkeypatch.setattr(SBM, "SB", sb)
    t = dt.datetime(2026, 11, 12, 12, 17, 5, tzinfo=IST).timestamp()
    st = {"NSE_EQ|KALYANKJIL": Inst(709.5, int(t * 1000))}
    e = types.SimpleNamespace(running=True, state=types.SimpleNamespace(get=st.get),
                              uni=types.SimpleNamespace(underlyings={"KALYANKJIL": types.SimpleNamespace(key="NSE_EQ|KALYANKJIL")}),
                              charts=types.SimpleNamespace(today_bars=lambda s: []))
    monkeypatch.setattr(live_api, "engine", lambda: e)
    app = FastAPI()
    app.include_router(live_api.router)
    cl = TestClient(app)
    d = cl.get("/api/live/candles/KALYANKJIL?tf=15s").json()
    assert "KALYANKJIL" in sb.watch and [c["close"] for c in d["candles"]] == [709.5]
    sb.sample(e, dt.datetime.fromtimestamp(t + 1, IST))                     # the engine now samples it every second
    st["NSE_EQ|KALYANKJIL"] = Inst(710.0, int((t + 20) * 1000))
    sb.sample(e, dt.datetime.fromtimestamp(t + 21, IST))
    assert [c["close"] for c in sb.candles("KALYANKJIL", "15s")["candles"]] == [709.5, 710.0]


def test_no_engine_still_answers(monkeypatch):
    from webapp import live_api
    sb = SBM.SecBars()
    monkeypatch.setattr(SBM, "SB", sb)
    monkeypatch.setattr(live_api, "engine", lambda: types.SimpleNamespace(running=False, state=None))
    app = FastAPI()
    app.include_router(live_api.router)
    r = TestClient(app).get("/api/live/candles/KALYANKJIL?tf=5s").json()
    assert r["candles"] == [] and "starts now" in r["message"] and "KALYANKJIL" in sb.watch
