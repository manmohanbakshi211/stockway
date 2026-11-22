"""2.86: Trend Rider's sub-charts and side card (Mandeep, 22 Nov 2026: "candle chart ke niche jo sub charts hain system
unpar sab par sabkuch apply karre or analyse kare taaki main par dependent na ho or main chart clean rahe ... main chart
ke side mein jo bhi analysis ya text hain vo bhi subchart ki reading se system lele")."""
import re
import types
from pathlib import Path

from fastapi.testclient import TestClient

from live.chartdata import IST_SHIFT
from webapp import live_api
from webapp.server import app

JS = (Path(__file__).resolve().parents[1] / "webapp" / "static" / "live.js").read_text()


def _rider(series, read):
    cfgv = {"trend_day": {"min": 50}, "breakout": {"min_quality": 55}, "chase": {"max": 65},
            "agreement": {"min_families": 3}, "move_vs_decay": {"min": 1.0}, "tradability": {"min": 40},
            "windows": [[5, 60], [120, 240]]}
    return types.SimpleNamespace(view=lambda sym: {"read": read, "series": series, "config": cfgv})


def test_rider_api_gives_the_chart_rows_and_the_same_latest_reading(monkeypatch):
    rows = [{"t": 1795060500.0, "tds": 75.0, "vwap_dist": -0.72, "bq": 68.7, "bo_dir": -1, "chase_up": 19.7, "chase_dn": 38.0,
             "ag_up": 1, "ag_dn": 3, "mvd_up": 3.6, "mvd_dn": 3.7, "trad_up": 94.0, "trad_dn": 94.2, "flow": 0.2, "rs": None,
             "go": -1}]
    read = {"t": 1795060500.0, "tod": 15, "readings": {"trend_day": {"score": 75.0}}, "decision": {"go": True, "dir": -1}}
    e = types.SimpleNamespace(rider=_rider(rows, read), running=True)
    monkeypatch.setattr(live_api, "engine", lambda: e)
    r = TestClient(app).get("/api/live/rider/nifty").json()
    assert r["ready"] and r["sym"] == "NIFTY" and r["running"]
    assert r["series"][0]["time"] == int(rows[0]["t"]) + IST_SHIFT          # on the chart's IST axis
    assert r["series"][0]["tds"] == 75.0 and r["read"]["decision"]["dir"] == -1   # the card reads the same numbers
    assert r["limits"] == {"trend_day": 50, "breakout": 55, "chase": 65, "families": 3, "mvd": 1.0, "trad": 40,
                           "windows": [[5, 60], [120, 240]]}


def test_rider_api_explains_an_empty_answer(monkeypatch):
    e = types.SimpleNamespace(rider=_rider([], None), running=False)
    monkeypatch.setattr(live_api, "engine", lambda: e)
    r = TestClient(app).get("/api/live/rider/SBIN").json()
    assert r["ready"] is False and "not running" in r["message"]
    monkeypatch.setattr(live_api, "engine", lambda: types.SimpleNamespace(running=True))
    assert TestClient(app).get("/api/live/rider/SBIN").json()["ready"] is False


def test_chart_page_has_the_rider_sub_charts_off_the_main_pane():
    for k in ("rd_trend", "rd_bo", "rd_flow", "rd_opt", "rd_ag"):
        assert f'["{k}", "Trend Rider' in JS
    assert '"rd_ag", "rd_opt"].includes(k)' in JS                 # agreement + option shown by default
    # every rider series is created on a sub-chart pane (the candles' pane 0 stays clean)
    made = re.findall(r"S\.rd_\w+ = (?:hist|ln)\((.*?)\);", JS)
    assert len(made) == 12 and all("pane" in m for m in made)
    assert "/api/live/rider/" in JS and 'id="ch-rider"' in JS and "riderCard(r)" in JS
    assert "chBarTime(x.time)" in JS                               # each reading on the bar it falls in, any timeframe
