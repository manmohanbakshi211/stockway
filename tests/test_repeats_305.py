"""3.05: one alert per stock every 30 minutes on the Alerts page (all still saved, graded and paper traded)."""
import datetime as dt
import types

from live import repeats as R
from live.store import Store


def _a(i, sym, mins, sev=2, direction="up", focus=False, kind="sharp_move"):
    return {"id": i, "sym": sym, "ts": 1_800_000_000 + mins * 60, "severity": sev, "direction": direction, "kind": kind,
            "data": {"focus": focus}}


def test_repeats_one_per_stock_per_window_unless_stronger_or_focus():
    al = [_a(1, "TCS", 0), _a(2, "TCS", 10, kind="volume_surge"), _a(3, "TCS", 12, sev=3), _a(4, "TCS", 20, sev=3),
          _a(5, "TCS", 25, focus=True), _a(6, "INFY", 5), _a(7, "TCS", 45), _a(8, "TCS", 50, direction="neutral", kind="iv_spike"),
          _a(9, "TCS", 100, direction="neutral", kind="iv_spike"), _a(10, "TCS", 115, direction="neutral", kind="iv_spike")]
    c = {"on": True, "window_min": 30, "neutral_window_min": 60, "keep_focus": True}
    assert R.mark(list(reversed(al)), c) == {2, 4, 7, 9}   # another type, same stock / not stronger; neutral: its own hour
    assert R.mark(al, {**c, "on": False}) == set()
    assert R.cfg()["window_min"] == 30 and R.cfg()["on"] is True


def test_alerts_page_hides_repeats_but_keeps_every_alert(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from webapp import live_api
    st = Store(tmp_path / "t.db")
    day = "2026-11-23"
    for i, m in enumerate((0, 10, 20, 40)):
        st.add_alert({"ts": 1_795_400_000 + m * 60, "day": day, "source": "upstox", "sym": "TCS", "kind": "sharp_move",
                      "direction": "up", "severity": 2, "title": "t", "detail": "", "price": 1.0, "data": {"focus": False}})
    e = types.SimpleNamespace(store=st, source="upstox", cfg=lambda: {"live": {"source": "upstox"}}, paper=None,
                              clock=types.SimpleNamespace(now=lambda: dt.datetime(2026, 11, 24, 12, 0)))
    monkeypatch.setattr(live_api, "engine", lambda: e)
    monkeypatch.setattr(live_api, "_alerts_fp", lambda e_, d: None)
    app = FastAPI()
    app.include_router(live_api.router)
    cl = TestClient(app)
    d = cl.get(f"/api/live/alerts?day={day}&view=all").json()
    assert len(d["alerts"]) == 2 and d["repeats"]["hidden"] == 2 and d["total"] == 4 and d["counts"]["all"] == 2
    d = cl.get(f"/api/live/alerts?day={day}&view=all&repeats=1").json()
    assert len(d["alerts"]) == 4 and d["repeats"]["shown"] is True
    assert len(cl.get(f"/api/live/alerts?day={day}&view=all&sym=TCS").json()["alerts"]) == 4     # one stock: all of it
    assert len(st.alerts(day=day)) == 4                                                          # nothing deleted
