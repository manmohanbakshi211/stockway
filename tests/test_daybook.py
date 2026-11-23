"""2.98 Day calendar and AI day review (Mandeep, 23 Nov 2026: "ek chart calender bhi banao jisme daily day green ya red se
mark ho or us par click karke pata chale kya hua ... ai bhi daale taaki 1:30 tak ek report banaye")."""
import datetime as dt
import json
import re
import types
from pathlib import Path

import pandas as pd
import pytest

from live import daybook as D
from live.clock import IST
from live.store import Store

DAY = "2026-11-23"
ROOT = Path(__file__).resolve().parents[1]


def ts(hm, day=DAY):
    h, m = hm.split(":")
    return dt.datetime.fromisoformat(f"{day}T{int(h):02d}:{int(m):02d}:00").replace(tzinfo=IST).timestamp()


def falling(start=65000.0, mins=375, step=-3.0, t0="09:15"):
    """1-minute candles [t, o, h, l, c] falling `step` points a minute."""
    out, p = [], start
    for i in range(mins):
        t = ts(t0) + 60 * i
        out.append([t, p, p + 2, p + step - 2, p + step])
        p += step
    return out


def trade(**k):
    t = {"id": 1, "day": DAY, "sym": "BANKNIFTY", "label": "BANKNIFTY 63600 PE 23 Nov", "dir": 1, "entry": 375.0,
         "exit": 223.0, "stop": 300.0, "peak": 671.0, "qty": 90, "entry_ts": ts("09:31"), "exit_ts": ts("11:15"),
         "reason": "stop loss", "status": "closed", "net": -13790}
    t.update(k)
    return t


C = D.cfg()


# ---------------------------------------------------------------- reading the trade
def test_parse_label_finds_side_strike_and_same_day_expiry():
    a = D.parse_label("NIFTY 25650 PE 23 Nov", DAY)
    assert a == {"side": "PE", "strike": 25650.0, "expiry": "2026-11-23", "same_day": True}
    b = D.parse_label("BANKNIFTY 63600 CE 30 Dec", DAY)
    assert b["side"] == "CE" and b["expiry"] == "2026-12-30" and not b["same_day"]
    assert D.parse_label("NIFTY 25000 CE 6 Jan", "2026-12-28")["expiry"] == "2027-01-06"     # next year's expiry
    assert D.parse_label("RELIANCE", DAY)["side"] is None and D.parse_label(None, DAY)["expiry"] is None


def test_diagnose_names_why_a_loser_lost():
    px = D._Px(falling(step=-30.0))                              # Bank Nifty fell all day; we bought a PE (with the day)
    alert = {"ts": ts("09:29")}                                  # entered 2 min after the alert: a delay
    r = D.diagnose(trade(), alert, px, ts("15:29"), C, gaps=[], dup=2)
    codes = {c["code"] for c in r["chips"]}
    assert {"stop", "delay", "chase", "trend", "after", "expiry", "late", "giveback", "dup"} <= codes
    assert r["seg"] == "index" and r["side"] == "PE" and r["same_day"] and r["d_und"] == -1
    assert next(c for c in r["chips"] if c["code"] == "trend")["tone"] == "good"           # with the day's fall
    assert r["why"].startswith("Exit 21% of the premium past the stop")                     # the biggest cause first
    assert r["pct"] == pytest.approx(-40.5, abs=0.1) and r["peak_pct"] == pytest.approx(78.9, abs=0.1)


def test_diagnose_explains_a_winner_and_a_restart_gap():
    px = D._Px(falling())
    w = D.diagnose(trade(exit=453.0, reason="target", net=6880, peak=460, exit_ts=ts("09:33")), None, px, ts("15:29"), C, [], 1)
    assert w["why"].startswith("Target hit in 2 min") and not any(c["code"] == "dup" for c in w["chips"])
    gap = [(ts("09:33"), ts("09:40"))]                                     # the app was not running then
    late = D.diagnose(trade(exit_ts=ts("09:41")), None, px, ts("15:29"), C, gap, 1)
    assert "the app was not running 09:33–09:40" in next(c for c in late["chips"] if c["code"] == "late")["text"]


def test_diagnose_decay_when_price_went_our_way_but_the_option_lost():
    up = falling(step=+2.0)                                              # the index rose; a CE bought on expiry day lost
    r = D.diagnose(trade(label="NIFTY 26000 CE 23 Nov", sym="NIFTY", entry=100.0, exit=60.0, stop=None, peak=None,
                         reason="time exit", net=-3000), None, D._Px(up), ts("15:29"), C, [], 1)
    assert any(c["code"] == "decay" for c in r["chips"]) and r["d_und"] == 1


# ---------------------------------------------------------------- the month and the day
def _store(tmp_path):
    st = Store(tmp_path / "live.db")
    aid = st.add_alert({"ts": ts("09:30"), "day": DAY, "source": "upstox", "sym": "BANKNIFTY", "kind": "orb_break",
                        "direction": "down", "severity": 3, "title": "ORB down", "detail": "", "data": {}})
    base = {"alert_id": aid, "day": DAY, "sym": "BANKNIFTY", "kind": "orb_break", "label": "BANKNIFTY 63600 PE 23 Nov",
            "side": "BUY", "lot": 30, "lots": 3, "qty": 90, "entry_ts": ts("09:31"), "entry": 375.0, "stop": 300.0,
            "target": 455.0, "status": "closed", "dir": 1}
    st.add_trade({**base, "source": "upstox", "exit_ts": ts("09:33"), "exit": 453.0, "peak": 460.0, "reason": "target",
                  "net": 6880.0, "costs": 120.0})
    st.add_trade({**base, "source": "upstox#trail", "exit_ts": ts("11:15"), "exit": 223.0, "peak": 671.0,
                  "reason": "stop loss", "net": -13790.0, "costs": 110.0})
    st.add_trade({**base, "source": "upstox", "day": "2026-11-20", "label": "NIFTY 25900 CE 25 Nov", "sym": "NIFTY",
                  "alert_id": None, "net": 4000.0, "exit": 120.0, "exit_ts": ts("10:00", "2026-11-20"),
                  "entry_ts": ts("09:40", "2026-11-20")})
    st.add_trade({**base, "source": "sim", "net": -99999.0})                 # practice: never on the calendar
    return st


def test_calendar_adds_every_account_and_marks_days(tmp_path):
    st = _store(tmp_path)
    (tmp_path / "dayreview").mkdir()
    (tmp_path / "dayreview" / f"{DAY}_1330.json").write_text("{}")
    c = D.calendar(st, tmp_path, "upstox", "2026-11", today={DAY: {"main": 500}})
    days = {d["day"]: d for d in c["days"]}
    assert set(days) == {"2026-11-20", DAY}
    d = days[DAY]
    assert d["net"] == 6880 - 13790 + 500 and d["n"] == 2 and d["won"] == 1 and d["expiry"] and d["reviews"] == ["1330"]
    assert d["accounts"]["main"]["net"] == 7380 and d["accounts"]["trail"]["net"] == -13790 and d["open_pnl"] == 500
    assert c["total"]["green"] == 1 and c["total"]["red"] == 1 and c["total"]["net"] == 4000 - 6410
    assert D.calendar(st, tmp_path, "upstox", "2026-10")["days"] == []


def test_build_one_day_from_the_recording(tmp_path):
    st = _store(tmp_path)
    rec = tmp_path / "recordings"
    rec.mkdir()
    rows = []
    for s, p0 in (("BANKNIFTY", 65000.0), ("NIFTY", 26000.0), ("FINNIFTY", 29000.0)):
        for t, o, h, l, c in falling(start=p0, step=-p0 * 0.0003):
            rows.append({"sym": s, "t": t, "o": o, "h": h, "l": l, "c": c, "v": 0})
    pd.DataFrame(rows).to_parquet(rec / f"upstox_{DAY}_bars.parquet")
    b = D.build(st, tmp_path, "upstox", DAY)
    assert b["net"] == 6880 - 13790 and b["n"] == 2 and [m["sym"] for m in b["market"]] == ["NIFTY", "BANKNIFTY", "FINNIFTY"]
    tr = {t["account"]: t for t in b["trades"]}
    assert tr["main"]["why"].startswith("Target hit") and tr["trail"]["account_name"]
    assert tr["trail"]["kind_name"] and tr["main"]["alert"] == "ORB down" and tr["main"]["alert_at"] == "09:30"
    les = {x["code"]: x for x in b["lessons"]}
    assert les["giveback"]["extra"] == round((671 - 223) * 90) and les["late"]["extra"] == round((300 - 223) * 90)
    assert les["dup"]["n"] == 2
    part = {"BANKNIFTY": [[ts("13:00"), 1, 1, 1, 1]]}                     # after a restart: live candles only from 13:00
    assert D.build(st, tmp_path, "upstox", DAY, live_bars=part)["market"][1]["open"] == 65000.0   # the recording wins
    mv = [m for m in b["missed"] if m["what"] == "move"]
    assert mv and all(m["move_pct"] < 0 for m in mv)
    assert any(len(m["syms"]) > 1 for m in mv)                         # the same window on several indices: one line


def test_missed_lists_strong_alerts_that_were_not_traded(tmp_path):
    st = _store(tmp_path)
    aid = st.add_alert({"ts": ts("12:00"), "day": DAY, "source": "upstox", "sym": "NIFTY", "kind": "vwap_reclaim",
                        "direction": "up", "severity": 4, "title": "t", "detail": "", "data": {"skip": "max trades"}})
    with st.lock:
        st.db.executescript("CREATE TABLE IF NOT EXISTS outcomes (alert_id INTEGER PRIMARY KEY, source TEXT, day TEXT, "
                            "kind TEXT, result TEXT, o60 REAL)")
        st.db.execute("INSERT INTO outcomes VALUES (?,?,?,?,?,?)", (aid, "upstox", DAY, "vwap_reclaim", "hit", 35.0))
        st.db.commit()
    m = D.missed(st, "upstox", DAY, {}, [], C)
    assert len(m) == 1 and m[0]["what"] == "alert" and "+35%" in m[0]["text"] and "(max trades)" in m[0]["text"]


# ---------------------------------------------------------------- the AI review
REPLY = {"market": "Bank Nifty fell 2.3%.", "right": ["ORB PE hit target"], "wrong": ["Late exit"], "missed": ["11:15 fall"],
         "could_take": ["PE at 11:15"], "trades": [{"id": 2, "note": "gave back +79%"}, {"id": "x", "note": "bad"}],
         "learn": ["lock profit at +50%"], "training": ["add minutes since a 1% move"],
         "ideas": [{"name": "no chase", "fact": "rel_vol", "op": ">", "v": 2, "why": "w"},
                   {"name": "bad", "fact": "nope", "op": ">", "v": 1}], "next": "No same-day buys after 09:45"}


def test_parse_keeps_known_facts_and_trade_notes():
    r = D.parse("Sure:\n" + json.dumps(REPLY), {"rel_vol"})
    assert r["trade_notes"] == {2: "gave back +79%"} and [i["fact"] for i in r["ideas"]] == ["rel_vol"]
    assert r["next"].startswith("No same-day") and D.parse("not json", {"x"}) is None and D.parse(None, set()) is None


def _engine(tmp_path, reply, enabled=True, telegram=None):
    asked = []

    def ask(p, n=0, auto=True, task=None):
        asked.append((task, auto, p))
        return reply
    e = types.SimpleNamespace(ai=types.SimpleNamespace(enabled=lambda: enabled, ask=ask),
                              telegram=types.SimpleNamespace(send=(telegram.append if telegram is not None else lambda m: None)))
    return e, asked


def _book():
    return {"day": DAY, "market": [], "net": -6910, "n": 2, "won": 1, "open": 0, "accounts": [], "lessons": [], "missed": [],
            "gaps": [], "trades": [{"id": 2, "account_name": "Trailing exit", "sym": "BANKNIFTY", "seg": "index", "side": "PE",
                                    "same_day": True, "kind_name": "ORB", "kind": "orb_break", "alert": "a", "alert_at": "09:30",
                                    "entry_at": "09:31", "exit_at": "11:15", "entry": 375, "exit": 223, "pct": -40.5,
                                    "peak_pct": 78.9, "net": -13790, "reason": "stop loss", "chips": [{"text": "late"}]}]}


def test_review_writes_the_report_and_puts_ideas_on_trial(tmp_path, monkeypatch):
    from live import analyst
    monkeypatch.setattr(analyst, "known_facts", lambda d: ["rel_vol", "z5"])
    monkeypatch.setattr(D, "cfg", lambda: {**C, "telegram": True})
    sent = []
    e, asked = _engine(tmp_path, json.dumps(REPLY), telegram=sent)
    out = D.review(e, tmp_path, DAY, "1330", book=_book(), log=lambda m: None)
    assert asked[0][0] == "day_review" and asked[0][1] is False and "still open" in asked[0][2]
    assert "paper only" in asked[0][2] and '"id": 2' in asked[0][2]
    assert out["report"]["learn"] == ["lock profit at +50%"] and out["ideas_added"] == 1
    saved = json.loads((tmp_path / "dayreview" / f"{DAY}_1330.json").read_text())
    assert saved["report"]["trade_notes"] == {"2": "gave back +79%"} and D.reviews(tmp_path, DAY)[0]["slot"] == "1330"
    ideas = json.loads((tmp_path / analyst.IDEAS).read_text())["singles"]
    assert ideas[0]["source"] == "AI day review"
    assert sent and "Day review" in sent[0] and "−₹6,910" in sent[0]


def test_review_without_ai_still_keeps_the_day(tmp_path, monkeypatch):
    from live import analyst
    monkeypatch.setattr(analyst, "known_facts", lambda d: [])
    sent = []
    e, asked = _engine(tmp_path, None, enabled=False, telegram=sent)
    out = D.review(e, tmp_path, DAY, "manual", book=_book(), log=lambda m: None)
    assert out["report"] is None and "AI is off" in out["note"] and not asked and not sent
    e2, _ = _engine(tmp_path, "rate limit", enabled=True)
    assert "No readable AI answer" in D.review(e2, tmp_path, DAY, "1545", book=_book(), log=lambda m: None)["note"]
    assert {r["slot"] for r in D.reviews(tmp_path, DAY)} == {"manual", "1545"}


def test_review_sheet_has_no_keys_or_accounts_data(tmp_path):
    s = json.dumps(D.compact(_book(), C))
    assert not re.search(r"token|password|pin|totp|api_key|secret", s, re.I)


# ---------------------------------------------------------------- the 13:30 trigger
def _eng(tmp_path):
    from live.engine import LiveEngine
    e = types.SimpleNamespace(brief_done=set(), data_dir=tmp_path)
    return lambda now, src="upstox": LiveEngine._day_review_slot(e, now, src), e


def test_review_runs_at_1330_once_and_a_late_start_runs_only_the_latest(tmp_path):
    due, e = _eng(tmp_path)
    at = lambda hm: dt.datetime.fromisoformat(f"{DAY}T{hm}:00").replace(tzinfo=IST)
    assert due(at("13:29")) is None
    assert due(at("13:30")) == "1330" and due(at("13:45")) is None                # once
    assert due(at("15:45")) == "1545" and due(at("16:00")) is None
    due2, _ = _eng(tmp_path / "b")
    assert due2(at("16:10")) == "1545" and due2(at("16:20")) is None             # started late: only the latest slot
    due3, _ = _eng(tmp_path / "c")
    assert due3(at("13:31"), "sim") is None and due3(at("13:31"), "replay") is None  # practice: never
    sat = dt.datetime(2026, 11, 21, 13, 31, tzinfo=IST)
    assert due3(sat) is None                                                       # no session, no review
    d4 = tmp_path / "d"
    (d4 / "dayreview").mkdir(parents=True)
    (d4 / "dayreview" / f"{DAY}_1330.json").write_text("{}")
    due4, _ = _eng(d4)
    assert due4(at("13:40")) is None                                               # already written (restart): not again


# ---------------------------------------------------------------- the app
def test_api_and_page_are_wired(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from webapp import live_api
    st = _store(tmp_path)
    started = []
    e = types.SimpleNamespace(store=st, data_dir=tmp_path, paper=None, _real_source=lambda: "upstox",
                              session_day=lambda: dt.date(2026, 11, 24),
                              day_book=lambda day, open_pnl=None: D.build(st, tmp_path, "upstox", day),
                              day_review_run=lambda day, slot: started.append((day, slot)))
    monkeypatch.setattr(live_api, "engine", lambda: e)
    app = FastAPI()
    app.include_router(live_api.router)
    cl = TestClient(app)
    c = cl.get("/api/live/calendar?month=2026-11").json()
    assert [d["day"] for d in c["days"]] == ["2026-11-20", DAY] and c["times"] == C["times"]
    assert cl.get("/api/live/calendar?month=bad").status_code == 400
    d = cl.get(f"/api/live/day/{DAY}").json()
    assert d["net"] == -6910 and len(d["trades"]) == 2 and d["reviews"] == [] and d["reviewing"] is False
    assert cl.get("/api/live/day/2026-1-1").status_code == 400
    assert cl.post(f"/api/live/day/{DAY}/review").json() == {"started": True}
    import time
    for _ in range(50):
        if started:
            break
        time.sleep(0.02)
    assert started == [(DAY, "manual")]
    live = (ROOT / "webapp" / "static" / "live.js").read_text()
    app_js = (ROOT / "webapp" / "static" / "app.js").read_text()
    assert "calendar: pageCalendar" in live and '["calendar", "Day calendar"]' in app_js
    assert 'href="#/calendar"' in (ROOT / "webapp" / "static" / "index.html").read_text()


def test_day_review_is_a_bullet_and_market_data_only():
    j = json.loads((ROOT / "rules" / "day_review.json").read_text())
    assert j["times"] == ["13:30", "15:45"] and j["valid_from"] and "chase_pct" in j
    assert json.loads((ROOT / "rules" / "ai_models.json").read_text())["tasks"]["day_review"] == "deep"
    src = (ROOT / "live" / "daybook.py").read_text()
    assert not re.search(r"place_order|modify_order|cancel_order|/orders|funds|holdings|positions", src)
    assert "40000" not in src and "40K" not in src
