"""The web API (read-only, empty states, data from the journal / research / costs) and the static app."""
import json

from fastapi.testclient import TestClient

from tanu.journal.store import Journal
from tanu.ui.api import create_app


def _row(**k):
    r = {"account": "shadow", "setup": "sharp_move_trend__time_30__all_days", "sym": "NIFTY", "day": "2026-11-13", "d": 1,
         "side": "CE", "strike": 25000.0, "entry_t": 1794560000.0, "exit_t": 1794561800.0, "entry": 100.0, "exit": 120.0,
         "lots": 1, "lot": 65, "qty": 65, "charges": 60.0, "pnl_rs": 1240.0, "pct": 19.1, "reason": "time",
         "mfe_pct": 25.0, "mae_pct": -3.0, "minutes": 30, "und_entry": 25010.0, "und_exit": 25080.0,
         "state": {"day": "trend", "rel": 1.4}}
    r.update(k)
    return r


def test_empty_data_gives_clear_empty_states(tmp_path):
    c = TestClient(create_app(tmp_path))
    t = c.get("/api/today").json()
    assert t["headline"] == "No paper trades recorded yet." and t["accounts"] == {} and t["tradeable"]["count"] == 0
    assert c.get("/api/journal").json() == {"days": [], "trades": []}
    s = c.get("/api/setups").json()
    assert s["ideas_tried"] == 0 and all(x["best"] is None for x in s["setups"])
    assert c.get("/api/system").status_code == 200
    assert c.get("/api/trade", params={"account": "a", "setup": "s", "sym": "N", "day": "d", "d": 1, "entry_t": 1}).status_code == 404


def test_screens_read_journal_research_and_costs(tmp_path):
    j = Journal(tmp_path / "store" / "journal.db")
    j.trade(_row())
    j.trade(_row(entry_t=1794562000.0, pnl_rs=-500.0, pct=-7.7, mfe_pct=12.0, exit=92.0))
    r = tmp_path / "research" / "search_x"
    r.mkdir(parents=True)
    (r / "report.json").write_text(json.dumps({"t_bar": 3.24, "k": 42, "setups": [
        {"setup": "sharp_move_trend__time_30__all_days", "learn": {"n": 300, "avg_pct": -0.7}, "hold": {"n": 68, "avg_pct": 1.7},
         "passes": False, "why_not": ["learn average not above 0"]}]}))
    (tmp_path / "research" / "ledger.jsonl").write_text(json.dumps({"id": "a"}) + "\n" + json.dumps({"id": "b"}) + "\n" + json.dumps({"id": "random_baseline__x__y"}) + "\n")
    (tmp_path / "costs").mkdir()
    (tmp_path / "costs" / "tradeable_2026-11-12.json").write_text(json.dumps({"day": "2026-11-12", "items": [
        {"sym": "BANKNIFTY", "tradeable": True, "cost": {"total_pct": 1.68}}, {"sym": "IDEA", "tradeable": False}]}))
    c = TestClient(create_app(tmp_path))
    t = c.get("/api/today").json()
    assert t["accounts"]["shadow"]["trades"] == 2 and "Sniper is waiting" in t["headline"]
    assert t["tradeable"] == {"day": "2026-11-12", "count": 1, "of": 2, "cheapest": [{"sym": "BANKNIFTY", "round_trip_pct": 1.68}]}
    s = c.get("/api/setups").json()
    sm = next(x for x in s["setups"] if x["id"] == "sharp_move_trend")
    assert s["ideas_tried"] == 2 and sm["best"]["hold"]["avg_pct"] == 1.7 and sm["variants_tested"] == 1
    jr = c.get("/api/journal").json()
    assert jr["day"] == "2026-11-13" and len(jr["trades"]) == 2 and jr["review"]["accounts"]["shadow"]["green_then_red"] == 1
    tr = c.get("/api/trade", params={"account": "shadow", "setup": _row()["setup"], "sym": "NIFTY", "day": "2026-11-13",
                                     "d": 1, "entry_t": 1794560000.0}).json()
    assert tr["trade"]["pnl_rs"] == 1240.0 and tr["underlying"] == [] and tr["option"] == []


def test_static_app_is_served_and_has_no_outside_links(tmp_path):
    c = TestClient(create_app(tmp_path))
    html = c.get("/").text
    assert "Paper only" in html and "/static/app.js" in html
    for f in ("app.js", "app.css"):
        body = c.get("/static/" + f).text
        assert "http://" not in body and "https://" not in body          # works offline, nothing leaves the machine
