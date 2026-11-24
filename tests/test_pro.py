"""3.01 Stockway Pro v1 (Mandeep, 24 Nov 2026): option buying only; every input votes together (no separate Ghanshyam /
pattern boxes) with strong checks; strong -> the Pro paper accounts (Index / Stocks), medium -> watch; after the close,
which check lined up and worked; a light model that learns only from new days. On trial: nothing else changes."""
import datetime as dt
import inspect
import json
import types

import pytest

from lab import rider_exit as X
from live import books as B
from live import pro as P
from live.store import Store
from tests.test_books import MAIN, Inst, _ms, _t, DAY

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
C = P.cfg()


# ---------------------------------------------------------------- the score
def test_rules_are_a_bullet_and_buying_only():
    j = json.loads((ROOT / "rules" / "pro.json").read_text())
    assert j["valid_from"] and j["strong"] == 85 and j["medium"] == 75 and j["min_groups"] == 3
    assert j["weights"]["forecast"] == 0 and j["weights"]["mindgame"] == 0 and j["weights"]["pro_mind"] > 0
    assert j["exit"] == {"lock_premium_pct": 50, "lock_keep_pct": 25} and j["expiry_cutoff"] == "11:00"
    books = json.loads((ROOT / "rules" / "paper_books.json").read_text())["books"]
    for bid, seg in (("pro_index", "index"), ("pro_stocks", "stock")):
        b = books[bid]
        assert b["pick"] == {"rider": True, "pro": True, "segment": seg} and b["exit_style"] == "rider"
        assert b["paper"]["max_lots"] == 1 and b.get("capital") is None and "sell" not in json.dumps(b["pick"])


def test_strong_needs_the_score_and_three_groups():
    w = C["weights"]
    price_only = {f: 1 for f in ("breakout", "trend", "momentum", "structure", "vwap", "ema")}
    s = P.score(price_only, w, C)
    assert s["score"] == 100.0 and s["groups"] == ["price"] and s["label"] == "medium"     # one group, however many
    mixed = {**price_only, "trend_day": 1, "flow": 1, "chase": -1}
    s = P.score(mixed, w, C)
    assert s["groups"] == ["day", "flow", "price"] and s["label"] == "strong" and s["score"] == pytest.approx(50 + 50 * 7.5 / 9.5, abs=0.1)
    assert P.score({"forecast": 1, "mindgame": 1}, w, C)["score"] == 50.0                  # weight 0: no say
    assert P.score({**mixed, "chase": -1, "vwap": -1, "momentum": -1, "trend": -1}, w, C)["label"] == "low"


def test_pro_votes_only_proven_mindgame_and_the_day_type():
    reads = {"5m": {"items": [{"id": "trap_run", "side": 1}, {"id": "holding", "side": 1}, {"id": "taken_back", "side": 1}]},
             "15m": {"items": [{"id": "range_trap", "side": 1}]}}
    e = types.SimpleNamespace(mind=types.SimpleNamespace(small=lambda sym, tfs, ts: (reads, {})))
    v, why = P.mind_vote(e, "NIFTY", 1, 0.0, C)
    assert v == 1 and "holding" not in why and "taken_back (turned round) against" in why       # +1 +1 -1
    v, _ = P.mind_vote(e, "NIFTY", -1, 0.0, C)
    assert v == -1
    assert P.mind_vote(types.SimpleNamespace(), "NIFTY", 1, 0.0, C) == (None, "")
    rows = [{"day": DAY.isoformat() if hasattr(DAY, "isoformat") else DAY, "sym": "NIFTY",
             "probs": {"trend": 45, "range": 40, "quiet": 15}, "usual": {"trend": 31, "range": 55, "quiet": 14}}]
    e2 = types.SimpleNamespace(charts=types.SimpleNamespace(_dt_rows=rows))
    day = rows[0]["day"]
    assert P.daytype_vote(e2, "NIFTY", day, C)[0] == 1 and P.daytype_vote(e2, "RELIANCE", day, C)[0] is None
    rows[0]["probs"] = {"trend": 25, "range": 45, "quiet": 30}
    assert P.daytype_vote(e2, "NIFTY", day, C)[0] == -1


def test_judge_keeps_every_vote_on_the_signal(tmp_path):
    e = types.SimpleNamespace(data_dir=tmp_path, uni=types.SimpleNamespace(underlyings={"NIFTY": types.SimpleNamespace(is_index=True)}))
    conf = {"with": ["breakout", "trend", "trend_day", "flow"], "against": ["strength"], "no_view": ["ghanshyam", "forecast"]}
    j = P.judge(e, "NIFTY", 1, dt.datetime(2026, 11, 25, 10, 0, tzinfo=P.IST), conf)
    assert j["seg"] == "index" and j["votes"]["strength"] == -1 and j["votes"]["ghanshyam"] == 0 and j["v"] == "pro1"
    assert j["label"] == "strong" and set(j["groups"]) == {"price", "day", "flow"}
    src = inspect.getsource(__import__("live.rider", fromlist=["x"]).Rider._one)
    assert "_pro.judge(" in src and '"pro": prj' in src


# ---------------------------------------------------------------- the Pro paper accounts
def _alert(i=1, sym="NIFTY", key="O1", label="strong", seg="index", expiry=None, direction="up"):
    return {"id": i, "sym": sym, "kind": "trend_rider", "severity": 2, "direction": direction, "title": f"{sym} Trend Rider",
            "und_key": "UND", "price": 500.0, "is_index": seg == "index",
            "option": {"key": key, "label": f"{sym} 500 {'CE' if direction == 'up' else 'PE'}", "side": "CE" if direction == "up" else "PE",
                       "lot": 75, **({"expiry": expiry} if expiry else {})},
            "data": {"rider": {"stop": 495.0 if direction == "up" else 505.0, "atr": 2.0, "trigger": "day high"},
                     "pro": {"score": 90 if label == "strong" else 80, "label": label, "seg": seg, "votes": {"breakout": 1}}}}


def _state(**px):
    ms = _ms(DAY, 9, 59)
    s = {k: Inst(v, ms) for k, v in px.items()}
    s.setdefault("UND", Inst(500.0, ms))
    return s


def test_pro_accounts_buy_only_strong_signals_of_their_segment(tmp_path):
    st = Store(tmp_path / "t.db")
    bs = B.Books("upstox", st, dict(MAIN), None, log=lambda m: None)
    assert {"pro_index", "pro_stocks"} <= set(bs.books)
    now = _t(DAY, 10, 0)
    out = bs.offer_rider(_alert(), _state(O1=40.0), now)
    assert out["pro_index"] == "taken" and out["pro_stocks"].startswith("an index") and out["rider"] == "taken"
    t = st.trades(source="upstox#pro_index")[0]
    plan = json.loads(t["plan"])
    assert plan["style"] == "rider" and plan["c"]["lock_premium_pct"] == 50 and plan["c"]["lock_keep_pct"] == 25 and t["lots"] == 1
    r = json.loads(st.trades(source="upstox#rider")[0]["plan"])
    assert r["c"]["lock_premium_pct"] is None                       # the Trend Rider account: unchanged
    out = bs.offer_rider(_alert(i=2, key="O2", label="medium"), _state(O2=40.0), now)
    assert out["pro_index"].startswith("Pro score 80 (medium)")
    out = bs.offer_rider(_alert(i=3, sym="TCS", key="O3", seg="stock"), _state(O3=40.0), now)
    assert out["pro_stocks"] == "taken" and out["pro_index"].startswith("a stock")
    assert bs.offer_rider({**_alert(i=4, key="O4"), "data": {"rider": {"stop": 495.0, "atr": 2.0}}}, _state(O4=40.0), now)["pro_index"] \
        == "no Pro reading on this signal"


def test_pro_rules_no_time_locks_expiry_by_the_decay_check(tmp_path):
    """3.04 (Mandeep, 24 Nov: "koi aisa lock nahi chahiye ki aache alert aaye toh system trade hi na le; market situation
    dekhni hai but opportunity miss nahi karni")."""
    st = Store(tmp_path / "t.db")
    day = _t(DAY, 11, 5)
    pick = {"rider": True, "pro": True, "segment": "index"}
    src = "upstox#pro_index"
    a = _alert(expiry=day.date().isoformat())
    assert P.block(pick, a, day, st, src) is None                          # strong, decay check not against: bought
    dec = {**a, "data": {**a["data"], "pro": {**a["data"]["pro"], "against": ["move_vs_decay"]}}}
    assert P.block(pick, dec, day, st, src).startswith("same-day expiry after 11:00 and the move vs decay check votes against")
    assert P.block(pick, dec, _t(DAY, 10, 55), st, src) is None
    assert P.block(pick, {**dec, "option": {**dec["option"], "expiry": "2030-01-01"}}, day, st, src) is None
    st.add_trade({"source": src, "day": day.date().isoformat(), "sym": "NIFTY", "side": "CE", "status": "closed",
                  "exit_ts": day.timestamp() - 300, "entry": 1.0, "exit": 2.0, "net": 1.0})
    assert P.block(pick, _alert(), day, st, src) is None                   # no cool-off: a fresh strong signal is bought
    old = {**P.cfg(), "expiry_needs": None, "cooloff_min": 15}            # the old rules still work if a file asks for them
    assert P.block(pick, a, day, st, src, old).startswith("same-day expiry after 11:00")
    assert P.block(pick, _alert(), day, st, src, old).startswith("cool-off: NIFTY CE closed 5 min ago")
    assert P.block(pick, _alert(direction="down", key="P1"), day, st, src, old) is None     # the other side
    assert P.block(pick, _alert(), day + dt.timedelta(minutes=11), st, src, old) is None


def test_profit_lock_keeps_part_of_a_big_move():
    T0 = _t(DAY, 10, 0).timestamp()
    p = X.RiderPlan(100.0, 1, 98.0, 1.0, 100.0, T0, {**X.DEFAULT, "lock_premium_pct": 50, "lock_keep_pct": 25})
    assert p.check(100.5, 140.0, T0 + 10) is None and p.check(101.0, 160.0, T0 + 20) is None
    q = X.RiderPlan.from_dict(p.to_dict())                          # the best price is kept across checks / restarts
    assert q.bo == 160.0 and q.check(100.8, 126.0, T0 + 30) is None
    assert q.check(100.7, 124.9, T0 + 40) == "profit lock"
    off = X.RiderPlan(100.0, 1, 98.0, 1.0, 100.0, T0, dict(X.DEFAULT))
    off.check(101.0, 160.0, T0 + 20)
    assert off.check(100.7, 110.0, T0 + 40) is None                 # default: no lock (the rider account as before)
    small = X.RiderPlan(100.0, 1, 98.0, 1.0, 100.0, T0, {**X.DEFAULT, "lock_premium_pct": 50, "lock_keep_pct": 25})
    small.check(101.0, 148.0, T0 + 20)
    assert small.check(100.7, 110.0, T0 + 40) is None               # never reached +50%: nothing to lock
    src = inspect.getsource(__import__("live.paper", fromlist=["x"]).PaperBook)
    assert '"profit lock": "profit lock"' in src and 'plan.get("exit")' in src


# ---------------------------------------------------------------- after the close
def _graded(st, rows):
    from live.trials import Trials
    Trials(st, "upstox", log=lambda m: None)
    with st.lock:
        for day, sym, votes, label, r in rows:
            st.db.execute("INSERT INTO trial_alerts(ts, day, source, sym, kind, direction, result, r_exit, data) "
                          "VALUES (?,?,?,?,?,?,?,?,?)", (1.0, day, "upstox", sym, "trend_rider", "up", "target" if r > 0 else "stop", r,
                                                         json.dumps({"pro": {"votes": votes, "label": label, "score": 90, "seg": "index"}})))
        st.db.commit()


def test_factor_report_says_which_check_lined_up(tmp_path):
    st = Store(tmp_path / "t.db")
    rows = []
    for i in range(12):
        rows.append(("2026-11-20", "NIFTY", {"breakout": 1, "flow": 1, "chase": -1}, "strong", 1.0))
        rows.append(("2026-11-20", "NIFTY", {"breakout": -1, "flow": 1, "chase": 1}, "low", -1.0))
    _graded(st, rows)
    f = P.factors(st, "upstox", 20, "2026-11-24")
    by = {r["factor"]: r for r in f["factors"]}
    assert f["n"] == 24 and by["breakout"]["verdict"] == "lined up and worked" and by["breakout"]["edge"] == 2.0
    assert by["chase"]["verdict"] == "did not help" and by["flow"]["verdict"] == "too few yet"
    assert f["tiers"]["strong"] == {"n": 12, "avg_r": 1.0, "win": 1.0}


def test_weights_learn_from_new_days_only(tmp_path, monkeypatch):
    st = Store(tmp_path / "t.db")
    monkeypatch.setattr(P, "cfg", lambda: {**C, "learn": {**C["learn"], "min_n": 10, "min_strong": 3, "forward_days": 2}})
    rows = [("2026-11-20", "NIFTY", {"breakout": 1, "trend_day": 1, "flow": 1}, "strong", 0.5) for _ in range(8)] + \
           [("2026-11-20", "NIFTY", {"breakout": -1, "trend_day": 1, "flow": 1}, "low", -0.5) for _ in range(8)]
    _graded(st, rows)
    logs = []
    s1 = P.learn(st, "upstox", tmp_path, "2026-11-20", log=logs.append)
    assert s1["challenger"]["breakout"] == pytest.approx(1.5 * 1.25) and s1["since"] == "2026-11-20"
    assert P.weights(tmp_path)["breakout"] == 1.5                   # not used yet
    s2 = P.learn(st, "upstox", tmp_path, "2026-11-21", log=logs.append)
    assert s2["challenger"] and s2["check"]["days"] == 0            # the days it learned from never count
    _graded(st, [("2026-11-24", "NIFTY", {"breakout": 1, "trend_day": 1, "flow": 1}, "strong", 0.4),
                 ("2026-11-25", "NIFTY", {"breakout": 1, "trend_day": 1, "flow": 1}, "strong", 0.6),
                 ("2026-11-25", "NIFTY", {"breakout": 1, "trend_day": 1, "flow": 1}, "strong", 0.2)])
    s3 = P.learn(st, "upstox", tmp_path, "2026-11-25", log=logs.append)
    assert s3["history"][-1]["days"] == 2 and "won" in s3["history"][-1]


# ---------------------------------------------------------------- the app
def test_engine_learns_once_a_market_day_after_the_close(tmp_path):
    from live.engine import LiveEngine
    e = types.SimpleNamespace(brief_done=set())
    at = lambda hm: dt.datetime.fromisoformat(f"2026-11-25T{hm}:00").replace(tzinfo=P.IST)
    due = lambda now, src="upstox": LiveEngine._pro_learn_due(e, now, src)
    assert not due(at("16:00")) and due(at("16:06")) and not due(at("16:30"))
    e.brief_done.clear()
    assert not due(at("16:10"), "sim") and not due(dt.datetime(2026, 11, 24, 16, 10, tzinfo=P.IST))     # holiday


def test_pro_page_and_api(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from webapp import live_api, memo
    st = Store(tmp_path / "t.db")
    _graded(st, [("2026-11-25", "NIFTY", {"breakout": 1}, "strong", 1.0), ("2026-11-25", "TCS", {"breakout": 1}, "medium", -1.0)])
    with st.lock:
        st.db.execute("UPDATE trial_alerts SET data=? WHERE sym='TCS'", (json.dumps({"pro": {"votes": {"breakout": 1}, "label": "medium",
                                                                                           "score": 80, "seg": "stock"}}),))
        st.db.commit()
    e = types.SimpleNamespace(store=st, data_dir=tmp_path, source="upstox", _real_source=lambda: "upstox",
                              session_day=lambda: dt.date(2026, 11, 25))
    monkeypatch.setattr(live_api, "engine", lambda: e)
    for k in list(memo._mem):
        memo._mem.pop(k, None)
    app = FastAPI()
    app.include_router(live_api.router)
    c = TestClient(app)
    d = c.get("/api/live/pro?seg=index").json()
    assert d["day"] == "2026-11-25" and d["book"] == "pro_index" and [x["sym"] for x in d["signals"]["strong"]] == ["NIFTY"]
    s = c.get("/api/live/pro?seg=stock").json()
    assert [x["sym"] for x in s["signals"]["medium"]] == ["TCS"] and s["signals"]["strong"] == []
    assert c.get("/api/live/pro?seg=x").status_code == 400
    js = (ROOT / "webapp" / "static" / "live.js").read_text()
    assert "Object.assign(PAGES, { pro: pagePro })" in js and 'href="#/pro"' in (ROOT / "webapp" / "static" / "index.html").read_text()


def test_pro_page_has_a_tab_for_everything_it_watches(tmp_path, monkeypatch):
    """3.02 (Mandeep, 24 Nov: "Pro mein jo alerts aayege jo bhi observery things hai sabke tabs banao pro mein")."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from webapp import live_api, memo
    st = Store(tmp_path / "t.db")
    _graded(st, [("2026-11-24", "NIFTY", {"breakout": 1}, "strong", 1.0), ("2026-11-25", "NIFTY", {"breakout": 1}, "strong", 1.0),
                 ("2026-11-25", "BANKNIFTY", {"breakout": -1}, "low", -1.0), ("2026-11-25", "FINNIFTY", {"breakout": 1}, "medium", 0.5)])
    st.add_trade({"source": "upstox#pro_index", "day": "2026-11-25", "sym": "NIFTY", "label": "NIFTY 25650 CE", "status": "closed",
                  "net": 2500.0, "entry": 100.0, "exit": 140.0, "reason": "profit lock"})
    st.add_trade({"source": "upstox#pro_index", "day": "2026-11-25", "sym": "NIFTY", "label": "NIFTY 25700 CE", "status": "open",
                  "entry": 90.0, "opt_key": "K1", "qty": 75, "dir": 1})
    st.add_trade({"source": "upstox#pro_stocks", "day": "2026-11-25", "sym": "TCS", "status": "closed", "net": -500.0, "entry": 1.0, "exit": 0.5})
    e = types.SimpleNamespace(store=st, data_dir=tmp_path, source="upstox", _real_source=lambda: "upstox",
                              session_day=lambda: dt.date(2026, 11, 25))
    monkeypatch.setattr(live_api, "engine", lambda: e)
    for k in list(memo._mem):
        memo._mem.pop(k, None)
    app = FastAPI()
    app.include_router(live_api.router)
    c = TestClient(app)
    d = c.get("/api/live/pro?seg=index").json()
    sg = d["signals"]
    assert [x["sym"] for x in sg["strong"]] == ["NIFTY"] and [x["sym"] for x in sg["medium"]] == ["FINNIFTY"]
    assert [x["sym"] for x in sg["low"]] == ["BANKNIFTY"] and sg["low_n"] == 1            # the low ones are listed too
    assert d["days"] == ["2026-11-25", "2026-11-24"]
    tr = d["trades"]
    assert tr["n"] == 1 and tr["net"] == 2500 and tr["won"] == 1 and [t["opt_key"] for t in tr["open"]] == ["K1"]
    assert tr["days"] == [{"day": "2026-11-25", "n": 1, "net": 2500, "won": 1}]            # the stocks account is separate
    assert d["base_weights"]["breakout"] == 1.5 and "challenger" in d["learn"]
    old = c.get("/api/live/pro?seg=index&day=2026-11-24").json()
    assert old["day"] == "2026-11-24" and [x["sym"] for x in old["signals"]["strong"]] == ["NIFTY"] and old["signals"]["low"] == []
    js = (ROOT / "webapp" / "static" / "live.js").read_text()
    for tab in ('["strong", "Strong"]', '["watch", "Watch"]', '["recorded", "Recorded"]', '["trades", "Trades"]',
                '["checks", "Checks"]', '["learning", "Learning"]'):
        assert tab in js
    assert 'id="pro-tabs"' in js and 'id="pro-day"' in js and "function proLearning(d)" in js and "function proFactors(f)" in js


def test_pro_calendar_compares_with_the_main_account_day_by_day(tmp_path, monkeypatch):
    """3.03 (Mandeep, 24 Nov: "make the separate calendar tab inside pro so we can compare it with main one; add all the
    things as per the pro the main has")."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from live import daybook as D
    from webapp import live_api, memo
    st = Store(tmp_path / "t.db")
    for src, sym, net in (("upstox", "NIFTY", -1000.0), ("upstox", "TCS", 300.0), ("upstox#pro_index", "NIFTY", 2500.0),
                          ("upstox#pro_stocks", "TCS", -200.0), ("upstox#trail", "NIFTY", 50.0)):
        st.add_trade({"source": src, "day": "2026-11-25", "sym": sym, "label": f"{sym} 100 CE 30 Dec", "status": "closed", "net": net,
                      "entry": 100.0, "exit": 100.0 + net / 75, "qty": 75, "entry_ts": 1.0, "exit_ts": 2.0})
    c = D.calendar(st, tmp_path, "upstox", "2026-11")
    a = c["days"][0]["accounts"]
    assert a["main"]["seg"] == {"index": {"net": -1000, "n": 1, "won": 0}, "stock": {"net": 300, "n": 1, "won": 1}}
    assert a["pro_index"]["net"] == 2500 and a["pro_index"]["seg"]["index"]["n"] == 1 and a["pro_stocks"]["seg"]["stock"]["net"] == -200
    b = D.build(st, tmp_path, "upstox", "2026-11-25", accounts=["pro_index"])
    assert [t["account"] for t in b["trades"]] == ["pro_index"] and b["net"] == 2500
    assert len(D.build(st, tmp_path, "upstox", "2026-11-25")["trades"]) == 5
    e = types.SimpleNamespace(store=st, data_dir=tmp_path, paper=None, session_day=lambda: dt.date(2026, 11, 26),
                              day_book=lambda day, open_pnl=None, accounts=None: D.build(st, tmp_path, "upstox", day, accounts=accounts))
    monkeypatch.setattr(live_api, "engine", lambda: e)
    for k in list(memo._mem):
        memo._mem.pop(k, None)
    app = FastAPI()
    app.include_router(live_api.router)
    cl = TestClient(app)
    p = cl.get("/api/live/day/2026-11-25?acct=pro_index").json()
    allday = cl.get("/api/live/day/2026-11-25").json()
    assert [t["account"] for t in p["trades"]] == ["pro_index"] and len(allday["trades"]) == 5     # not mixed up in the cache
    assert cl.get("/api/live/day/2026-11-25?acct=nope").status_code == 400
    js = (ROOT / "webapp" / "static" / "live.js").read_text()
    assert '["calendar", "Calendar"]' in js and "/api/live/day/${day}?acct=${acct}" in js and "function proCalSum(c)" in js
    assert "Main's full day" in js and "What Pro missed" in js and "Every Pro trade and why" in js


def test_pro_and_no_locks_accounts_have_no_day_locks(tmp_path):
    """3.04: the Pro accounts and the 'Main · no locks' challenger keep no trades-a-day count, day loss stop, cool-off or
    good-day lock, whatever the main account's settings; the main account keeps its own."""
    st = Store(tmp_path / "t.db")
    main = {"capital": 300000, "max_trades_day": 6, "max_daily_loss_pct": 3, "cooloff_min": 30, "profit_lock": 5000,
            "max_open": 6, "no_entry_after": "14:45", "kinds": "all", "min_severity": 2}
    bs = B.Books("upstox", st, main, log=lambda m: None)
    for bid in ("pro_index", "pro_stocks", "nolock"):
        c = bs.books[bid].cfg
        assert (c["max_trades_day"], c["max_daily_loss_pct"], c["cooloff_min"], c["profit_lock"], c["max_open"]) == (99, 100, 0, 0, 99), bid
        assert c["capital"] == 300000                                      # the money set in the app
    assert bs.books["pro_index"].cfg["no_entry_after"] == "15:00" and bs.books["nolock"].cfg["no_entry_after"] == "14:45"
    assert bs.specs["nolock"]["pick"] == {"same_as_main": True, "risk_pass": True}
    now = _t(DAY, 12, 0)
    day = now.date().isoformat()
    for k in range(8):                                                     # a bad day: 8 losing trades
        st.add_trade({"source": "upstox#nolock", "day": day, "sym": f"S{k}", "status": "closed", "net": -5000.0,
                      "reason": "stop loss", "exit_ts": now.timestamp() - 60, "entry": 1.0, "exit": 0.5})
    assert bs.books["nolock"].guard(now)["blocked"] is False


def test_day_book_says_what_the_day_locks_stopped(tmp_path):
    from live import daybook as D
    from live.record import OUTCOME_SQL
    st = Store(tmp_path / "t.db")
    with st.lock:
        st.db.executescript(OUTCOME_SQL)
    day = "2026-11-19"
    t0 = dt.datetime(2026, 11, 19, 10, 30, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))).timestamp()
    def add(sym, reasons, focus=True, o60=None, omax=None, dt_=0):
        aid = st.add_alert({"ts": t0 + dt_, "day": day, "source": "upstox", "sym": sym, "kind": "sharp_move", "direction": "up",
                            "severity": 3, "title": sym, "detail": "", "price": 1.0,
                            "data": {"focus": focus, "risk": {"verdict": "BLOCK", "reasons": reasons}}})
        if o60 is not None:
            with st.lock:
                st.db.execute("INSERT INTO outcomes(alert_id, source, day, ts, sym, kind, direction, o60, omax) VALUES (?,?,?,?,?,?,?,?,?)",
                              (aid, "upstox", day, t0 + dt_, sym, "sharp_move", "up", o60, omax))
                st.db.commit()
    g = "6 trades today: over-trading guard reached."
    add("TCS", [g], o60=20.0, omax=40.0)
    add("TCS", [g], o60=-50.0, omax=1.0, dt_=60)                          # the same symbol again: counted once
    add("INFY", [g], o60=-10.0, omax=5.0, dt_=120)
    add("SBIN", [g, "Reward:risk 0.8 is under your minimum 1.2."], o60=99.0, dt_=180)   # blocked by more than the lock
    add("HDFC", [g], focus=False, o60=99.0, dt_=240)                       # not a Focus alert
    add("ITC", ["Daily loss limit hit (₹-9,000). No new trades today."], dt_=300)
    out = D.locked_out(st, "upstox", day)
    a = next(x for x in out if x["lock"] == "trades-a-day count")
    assert (a["n"], a["graded"], a["up"], a["big"], a["avg_o60"]) == (2, 2, 1, 1, 5.0) and a["from"] == "10:30"
    b = next(x for x in out if x["lock"] == "daily loss stop")
    assert b["n"] == 1 and "not graded yet" in b["text"]
    assert any(m.get("what") == "lock" for m in D.build(st, tmp_path, "upstox", day)["missed"])
    assert not any(m.get("what") == "lock" for m in D.build(st, tmp_path, "upstox", day, accounts=["pro_index"])["missed"])
