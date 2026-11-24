"""3.06 (Mandeep, 24 Nov 2026: "haan ye bhi add karo but ek relevant page mein or system chain mein best jagah place karke
connect karo"): corporate actions of the F&O stocks, from NSE's website with NSE's official MCP server as the backup,
on alerts (event risk), the Market context page, the alert's bigger-picture line and the Day calendar."""
import datetime as dt
import json
import types
from pathlib import Path

import pytest

from live import corpact as C
from live.clock import IST
from live.store import Store

ROOT = Path(__file__).resolve().parents[1]
TODAY = dt.date(2026, 11, 25)
SITE = [{"symbol": "INFY", "series": "EQ", "subject": "Interim Dividend - Rs 26 Per Share", "exDate": "25-Nov-2026", "recDate": "25-Nov-2026"},
        {"symbol": "BEL", "subject": "Bonus 1:2", "exDate": "26-Nov-2026", "recDate": "26-Nov-2026"},
        {"symbol": "TCS", "subject": "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share", "exDate": "15-Dec-2026"},
        {"symbol": "NOTFNO", "subject": "Dividend - Rs 1 Per Share", "exDate": "25-Nov-2026"},
        {"symbol": "SBIN", "subject": "Dividend - Rs 2 Per Share", "exDate": "-"}]
MCP_RELIANCE = {"symbol": "RELIANCE", "count": 2, "actions": [
    {"exDate": "2024-10-28", "actionType": "BONUS", "purpose": "Bonus 1:1", "adjustmentFactor": 0.5},
    {"exDate": "2026-06-05", "actionType": "DIVIDEND", "purpose": "Dividend - Rs 6 Per Share", "adjustmentFactor": 1.0}]}


@pytest.mark.parametrize("purpose,kind,ratio,amount,factor", [
    ("Dividend - Rs 8 Per Share", "dividend", None, 8.0, None),
    ("Annual General Meeting/Special Dividend - Rs 8 Per Share /Dividend - Rs 20 Per Share", "dividend", None, 28.0, None),
    ("Interim Dividend - Re 0.80 Per Share", "dividend", None, 0.8, None),
    ("Bonus 1:1", "bonus", "1:1", None, 0.5),
    ("Bonus 1:2", "bonus", "1:2", None, 0.666667),
    ("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share", "split", "10:2", None, 0.2),
    ("Rights 1:5 @ Premium Rs 100/-", "rights", "1:5", None, None),
    ("Buy Back", "buyback", None, None, None),
    ("Demerger", "demerger", None, None, None)])
def test_nse_words_are_read(purpose, kind, ratio, amount, factor):
    c = C.classify(purpose)
    assert (c["kind"], c["ratio"], c["amount"], c["factor"]) == (kind, ratio, amount, factor)


def test_website_rows_and_mcp_rows_parse_the_same_way():
    rows = C.parse_nse(SITE, {"INFY", "BEL", "TCS", "SBIN"})
    assert [(r["sym"], r["ex_date"], r["kind"]) for r in rows] == [("INFY", "2026-11-25", "dividend"), ("BEL", "2026-11-26", "bonus"),
                                                                    ("TCS", "2026-12-15", "split")]   # not F&O / no date: out
    assert C.parse_nse({"data": SITE[:1]})[0]["amount"] == 26.0
    m = C.parse_mcp("reliance", MCP_RELIANCE)
    assert m[0] == {"sym": "RELIANCE", "ex_date": "2024-10-28", "purpose": "Bonus 1:1", "record_date": None, "kind": "bonus",
                    "ratio": "1:1", "amount": None, "factor": 0.5}


def test_mcp_client_speaks_json_rpc_over_http():
    sent = []

    def post(body, headers):
        sent.append((body, dict(headers)))
        if body.get("method") == "initialize":
            return types.SimpleNamespace(text='{"jsonrpc":"2.0","id":1,"result":{}}', headers={"mcp-session-id": "S1"})
        if body.get("method") == "tools/call":
            txt = json.dumps(MCP_RELIANCE)
            return types.SimpleNamespace(text="event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "id": 2, "result": {
                "content": [{"type": "text", "text": txt}]}}), headers={})
        return types.SimpleNamespace(text="", headers={})
    rows = C.mcp_actions("RELIANCE", "2021-11-25", "2026-11-25", url="https://x/mcp", post=post)
    assert len(rows) == 2 and rows[0]["factor"] == 0.5
    assert sent[2][0]["params"] == {"name": "get_corporate_actions", "arguments": {"symbol": "RELIANCE", "fromDate": "2021-11-25",
                                                                                   "toDate": "2026-11-25"}}
    assert sent[2][1]["Mcp-Session-Id"] == "S1" and "order" not in json.dumps([b for b, _ in sent]).lower()


def test_refresh_website_first_then_mcp_then_keep_what_it_had(tmp_path):
    fo = {"INFY", "BEL", "TCS", "RELIANCE"}
    j = C.refresh(tmp_path, fo, site=lambda a, b: SITE, today=TODAY, log=lambda m: None)
    assert j["source"] == "nse" and len(j["rows"]) == 3 and j["window"] == ["2025-11-20", "2027-01-24"]
    calls = []

    def bad_site(a, b):
        raise OSError("403")

    def mcp(s, a, b):
        calls.append(s)
        return C.parse_mcp(s, MCP_RELIANCE) if s == "RELIANCE" else []
    j = C.refresh(tmp_path, fo, site=bad_site, today=TODAY, log=lambda m: None, mcp=mcp, sleep=lambda s: None)
    assert j["source"] == "nse_mcp" and sorted(calls) == sorted(fo)
    kinds = {(r["sym"], r["ex_date"]) for r in j["rows"]}
    assert ("RELIANCE", "2026-06-05") in kinds and ("BEL", "2026-11-26") in kinds          # upcoming ones from before kept
    assert ("RELIANCE", "2024-10-28") not in kinds                                         # older than past_days

    def dead(s, a, b):
        raise OSError("down")
    logs = []
    j2 = C.refresh(tmp_path, fo, site=bad_site, today=TODAY, log=logs.append, mcp=dead, sleep=lambda s: None)
    assert j2["error"] and len(j2["rows"]) == len(j["rows"]) and "not refreshed" in logs[0]


def test_near_on_day_words_and_view(tmp_path):
    C.refresh(tmp_path, {"INFY", "BEL", "TCS"}, site=lambda a, b: SITE, today=TODAY, log=lambda m: None)
    n = C.near(tmp_path, "BEL", TODAY)
    assert n[0]["days"] == 1 and C.words(n[0])["level"] == "high" and "strikes and lot size" in C.words(n[0])["warn"]
    i = C.near(tmp_path, "INFY", TODAY)[0]
    w = C.words(i, 1300.0)
    assert i["days"] == 0 and w["what"] == "Ex-dividend today ₹26 (~2.0%)" and w["level"] == "medium" and "opens about ₹26 lower" in w["warn"]
    assert C.words(i, 5000.0)["level"] == "low" and C.words(i, 5000.0)["warn"] is None              # 0.5%: only a note
    assert C.near(tmp_path, "TCS", TODAY) == [] and C.near(tmp_path, "TCS", TODAY, days=20)[0]["kind"] == "split"
    assert set(C.on_day(tmp_path, "2026-11-25")) == {"INFY"}
    v = C.view(tmp_path, TODAY, {"INFY", "BEL", "TCS"})
    assert [r["sym"] for r in v["upcoming"]] == ["INFY", "BEL", "TCS"] and v["source"] == "nse"


def test_event_risk_warns_on_alerts_of_that_stock(tmp_path):
    from live import events as EV
    C.refresh(tmp_path, {"BEL", "INFY"}, site=lambda a, b: SITE, today=TODAY, log=lambda m: None)
    u = types.SimpleNamespace(is_index=False, near_expiry=lambda d: dt.date(2026, 12, 29))
    e = types.SimpleNamespace(store={}, data_dir=tmp_path, metrics={"INFY": {"spot": 1300.0}}, context=None, world=None,
                              uni=types.SimpleNamespace(underlyings={"BEL": u, "INFY": u, "SBIN": u}))
    g = EV.EventEngine(e)
    now = dt.datetime(2026, 11, 25, 10, 0, tzinfo=IST)
    b = g.risk("BEL", now)
    assert b["level"] == "high" and any("Bonus 1:2 ex-date tomorrow" in i["what"] for i in b["items"]) and b["facts"]["ev_corp_d"] == 1
    assert any("strikes and lot size" in w for w in b["warnings"])
    assert any(i["what"].startswith("Ex-dividend today ₹26") for i in g.risk("INFY", now)["items"])
    assert "ev_corp_d" not in g.risk("SBIN", now)["facts"]


def test_day_calendar_marks_a_trade_on_its_ex_date(tmp_path):
    from live import daybook as D
    C.refresh(tmp_path, {"INFY"}, site=lambda a, b: SITE, today=TODAY, log=lambda m: None)
    st = Store(tmp_path / "t.db")
    st.add_trade({"source": "upstox", "day": "2026-11-25", "sym": "INFY", "label": "INFY 1300 PE 29 Dec", "status": "closed",
                  "net": 900.0, "entry": 20.0, "exit": 32.0, "qty": 400, "entry_ts": 1.0, "exit_ts": 2.0})
    t = D.build(st, tmp_path, "upstox", "2026-11-25")["trades"][0]
    assert any(c["code"] == "corp" and "Dividend ex-date today ₹26" in c["text"] for c in t["chips"])


def test_wired_into_context_pages_and_system(tmp_path):
    from live.context import Context
    from live import selfcheck
    c = Context.__new__(Context)
    c.data_dir, c.log, c.fo_symbols, c.errors, c.updated = tmp_path, (lambda m: None), {"INFY", "BEL"}, {}, {}
    c.nse = types.SimpleNamespace(corp_actions=lambda a, b: SITE)
    assert c.corp_refresh() is True
    import time
    for _ in range(50):
        if not getattr(c, "_corp_busy", False):
            break
        time.sleep(0.05)
    assert [r["sym"] for r in C.load(tmp_path)["rows"]] == ["INFY", "BEL"] and c.corp_refresh() is False   # fresh: not again
    js = (ROOT / "webapp" / "static" / "live.js").read_text()
    assert "function corpBox(v)" in js and "${corpBox(c.corp)}" in js and "b.corp" in js and "NSE MCP (official)" in js
    assert any(x["id"] == "corp_actions" and x["ok"] for x in selfcheck.static_checks())
    src = (ROOT / "live" / "corpact.py").read_text().lower()
    assert "place_order" not in src and "/orders" not in src
