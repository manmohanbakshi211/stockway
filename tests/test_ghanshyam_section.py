"""2.69: the Ghanshyam section (Mandeep, 12 Nov 2026): one file for all his strategies (9:20 merged, course fixes), its
own paper account that takes only his strategies on Nifty and Bank Nifty (1 lot, once per strategy, symbol and day),
and the accountability view (Lab, test bench, account) with every signal. Paper only; the main account is untouched."""
import datetime as dt
import hashlib
import json
import types
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lab import packs as P
from live import books as B
from live import ghanshyam as G
from live.clock import IST
from live.store import Store

ROOT = Path(__file__).resolve().parents[1]
MAIN = {"capital": 500000, "risk_pct": 1.0, "max_daily_loss_pct": 3.0, "target_pct": 20, "stop_pct": 15,
        "max_hold_min": 60, "exit_time": "15:15", "no_entry_after": "14:45", "min_severity": 2, "max_open": 5,
        "max_trades_day": 10, "max_lots": 5, "kinds": ["volume_surge"], "auto": True}


class Inst:
    def __init__(self, ltp, ltt=None):
        self.ltp, self.bid, self.ask, self.ltt, self.t = ltp, ltp, ltp, ltt, None


def _now():
    return dt.datetime(2026, 11, 9, 10, 0, tzinfo=IST)


def _state():
    ms = int(_now().timestamp() * 1000) - 1000
    px = {"O1": Inst(100.0, ms), "O2": Inst(90.0, ms), "UND": Inst(25000.0, ms)}
    return types.SimpleNamespace(get=px.get)


def _sig(sym="NIFTY", key="O1"):
    return {"sym": sym, "kind": "pack", "direction": "up", "severity": 2, "title": "t", "und_key": "UND",
            "option": {"key": key, "label": f"{sym} 25000 CE", "side": "CE", "lot": 65}, "data": {}}


def _rule(pid="ghanshyam_course", name="9:20 call", v="Pabc1234"):
    return {"_pack": pid, "_strategy": name, "_variant": v, "_name": f"G: {name}"}


def test_the_file_merges_920_and_fixes_the_course(monkeypatch):
    monkeypatch.setattr(P, "each_symbol_on", lambda: False)
    raw = (ROOT / "rules" / "strategy_files" / "ghanshyam_course.json").read_bytes()
    d = json.loads(raw)
    res = P.check(raw)
    assert res["fatal"] is None and res["problems"] == [] and len(res["strategies"]) == 25 and d["version"] == 2
    names = [s["name"] for s in d["strategies"]]
    assert any("y'day high taken in 1st candle" in n for n in names) and all(len(n) <= 60 for n in names)          # 920-E kept
    assert not any(n.startswith("920-A") or n.startswith("920-B") for n in names)            # losers left out
    fib = [s for s in d["strategies"] if s["name"].startswith("Fibonacci")]
    assert all(c[2] >= 1.0 for s in fib for c in s["then"]["conds"] if c[0] in ("fall_day", "rise_day"))
    assert any(c[0] == "pm_edge5" for s in d["strategies"] for c in s["conds"])
    brk = next(s for s in d["strategies"] if s["name"].startswith("Breakout: 3+"))
    assert ["lv_touches", ">", 2.5] in brk["conds"] and ["lv_run", "<", 0.5] in brk["then"]["conds"]
    assert not any(x in raw.decode().lower() for x in ("place_order", "api_key", "access_token"))


def test_bench_variant_is_the_same_id_as_before():
    r = {"conds": [["fc_pos", ">", 0.5]], "dir": 1, "win": [10, 75], "syms": ["BANKNIFTY"]}
    sig = json.dumps(["gx", r["conds"], r["dir"], r.get("win"), r["syms"]], sort_keys=True)
    assert P.bench_variant("gx", r) == "P" + hashlib.sha1(sig.encode()).hexdigest()[:7]


def _books(tmp_path):
    st = Store(tmp_path / "t.db")
    return st, B.Books("upstox", st, dict(MAIN), None, log=lambda m: None)


def test_ghanshyam_account_takes_only_his_strategies_on_the_two_indices(tmp_path):
    st, bs = _books(tmp_path)
    assert "ghanshyam" in bs.books and bs.has_pack()
    now, s = _now(), _state()
    assert bs.offer_pack(_sig(), s, now, _rule())["ghanshyam"] == "taken"
    assert "ghanshyam" not in bs.offer_pack(_sig("RELIANCE", "O2"), s, now, _rule(name="other"))     # not his symbols
    assert "ghanshyam" not in bs.offer_pack(_sig("BANKNIFTY", "O2"), s, now, _rule(pid="aseem_x"))   # not his file
    assert bs.offer_pack(_sig(), s, now, _rule())["ghanshyam"].startswith("this strategy already")   # once a day
    t = [x for x in st.trades(source="upstox#ghanshyam")]
    assert len(t) == 1 and t[0]["mode"] == "pack:Pabc1234" and t[0]["lots"] == 1 and t[0]["sym"] == "NIFTY"
    assert st.trades(source="upstox") == []                                      # the main account untouched
    a = {"id": 1, "sym": "S1", "kind": "volume_surge", "severity": 3, "direction": "up", "title": "t", "und_key": "UND",
         "option": {"key": "O2", "label": "S1 CE", "side": "CE", "lot": 100},
         "data": {"focus": True, "score": 90, "risk": {"verdict": "PASS", "lots": 1}}}
    assert "ghanshyam" not in bs.consider(a, s, now)                             # ordinary alerts never reach it


def test_view_shows_accountability_and_signals(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "each_symbol_on", lambda: False)
    data = tmp_path / "data"
    data.mkdir()
    out = G.install(data)
    assert out["id"] == "ghanshyam_course" and out["rules"] >= 80
    st = Store(data / "live.db")
    strat = G.strategies(data)
    assert len(strat) == 25
    s920 = next(v for k, v in strat.items() if "y'day high taken in 1st candle" in k)
    var = s920["variants"][0]
    from live.bench import db_for
    db = db_for(st)
    now = _now()
    with db.lock:
        db.db.execute("INSERT INTO bench_trades(ts, day, source, sym, kind, variant, name, style, direction, entry, exit, "
                      "exit_ts, net, reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (now.timestamp(), now.date().isoformat(), "upstox", "BANKNIFTY", "pack", var, "x", "buy", "up",
                       500.0, 560.0, now.timestamp() + 900, 1700.0, "target"))
        db.db.commit()
    bs = B.Books("upstox", st, dict(MAIN), None, log=lambda m: None)
    rule = {"_pack": "ghanshyam_course", "_strategy": s920["name"], "_variant": var, "_name": "x"}
    sig = _sig("BANKNIFTY")
    assert bs.offer_pack(sig, _state(), now, rule)["ghanshyam"] == "taken"
    e = types.SimpleNamespace(store=st, state=_state(), data_dir=data)
    monkeypatch.setattr(G.dt, "datetime", type("D", (dt.datetime,), {"now": staticmethod(lambda tz=None: now)}))
    v = G.view(e, bs, "upstox", True, days=30)
    row = next(r for r in v["strategies"] if r["name"] == s920["name"])
    assert row["bench"]["n"] == 1 and row["bench"]["net"] == 1700.0 and row["account"]["open"] == 1
    assert v["signals"][0]["strategy"] == s920["name"] and v["signals"][0]["account"]["status"] == "open"
    assert v["account"]["open"][0]["strategy"] == s920["name"] and v["have_file"]


def test_api_and_install(tmp_path, monkeypatch):
    from webapp import live_api
    monkeypatch.setattr(P, "each_symbol_on", lambda: False)
    data = tmp_path / "data"
    data.mkdir()
    st = Store(data / "live.db")
    bs = B.Books("upstox", st, dict(MAIN), None, log=lambda m: None)
    e = types.SimpleNamespace(paper=object(), books=bs, source="upstox", state=_state(), store=st, data_dir=data,
                              cfg=lambda: {"live": {"source": "upstox", "paper": MAIN}})
    monkeypatch.setattr(live_api, "engine", lambda: e)
    app = FastAPI()
    app.include_router(live_api.router)
    cl = TestClient(app)
    v = cl.get("/api/live/ghanshyam").json()
    assert v["have_file"] is False and v["file_in_repo"] is True
    r = cl.post("/api/live/ghanshyam/install").json()
    assert r["id"] == "ghanshyam_course" and r["version"] == 2
    v = cl.get("/api/live/ghanshyam?days=7").json()
    assert v["have_file"] and len(v["strategies"]) == 25 and v["days"] == 7
