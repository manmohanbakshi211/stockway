"""2.31: every signal in 3 ways (only buying, buying + selling, only selling), money blocked per lot (margin estimate),
capital accounts at 4 levels, the mover sweep and the data vault."""
import datetime as dt
import json
import types

import pytest

from live import accounts as A, bench as B, margin as M, movers as MV
from live.clock import IST
from live.store import Store


class I:
    def __init__(self, bid, ask):
        self.bid, self.ask, self.ltp = bid, ask, (bid + ask) / 2


class St(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)


EXP = dt.date(2026, 11, 10)
OPT = {"key": "CE24000", "side": "CE", "strike": 24000.0, "expiry": EXP.isoformat(), "lot": 65, "label": "NIFTY 24000 CE"}
CFG = {"target_pct": 30, "stop_pct": 20, "max_hold_min": 60, "no_entry_after": "14:45"}
NOW = dt.datetime(2026, 11, 4, 10, 0, tzinfo=IST)


def _full():
    """A Nifty chain with calls and puts every 50 points around 24000, and live quotes for all of them."""
    o = lambda k: types.SimpleNamespace(key=k, lot=65)
    ks = [23850.0 + 50 * i for i in range(7)]
    chain = {EXP: {k: {"CE": o(f"CE{int(k)}"), "PE": o(f"PE{int(k)}")} for k in ks}}
    st = St()
    for k in ks:
        ce = max(5.0, 100 - (k - 24000) * 0.5)
        pe = max(5.0, 100 + (k - 24000) * 0.5)
        st[f"CE{int(k)}"], st[f"PE{int(k)}"] = I(ce - 1, ce), I(pe - 1, pe)
    u = types.SimpleNamespace(is_index=True, chains=chain)
    e = types.SimpleNamespace(state=st, uni=types.SimpleNamespace(underlyings={"NIFTY": u}), regimes={"NIFTY": {"code": "trend_up"}})
    return e, st


def test_every_style_on_a_full_chain(tmp_path):
    e, st = _full()
    b = B.Bench(Store(tmp_path / "live.db"), "upstox")
    n = b.add({"sym": "NIFTY", "kind": "orb", "direction": "up", "option": OPT, "severity": 3,
               "data": {"score": 70, "conviction": 80, "focus": True}}, e, NOW, CFG)
    assert n == len(B.STYLES) == 8
    r = {x["style"]: x for x in b.open}
    assert {x["family"] for x in b.open} == {"buy", "mixed", "sell"}
    assert r["buy"]["entry"] == 100 and r["buy_otm"]["entry"] == 75 and r["buy_itm"]["entry"] == 125
    assert r["spread"]["entry"] == 100 - 49                         # buy 24000 CE at the ask, sell 24100 CE at the bid
    assert r["sell"]["entry"] == -99                                # sell the 24000 PE at its bid
    assert r["credit"]["entry"] == -99 + 50                         # sell 24000 PE (bid 99), buy 23900 PE (ask 50)
    # target / stop are % of the premium at stake, in both directions
    assert r["sell"]["target"] == pytest.approx(-99 + 0.3 * 99) and r["sell"]["stop"] == pytest.approx(-99 - 0.2 * 99)
    assert r["sell_day"]["hold_min"] >= 10 ** 6 and r["buy_day"]["target"] == pytest.approx(160)
    # money blocked per lot: premium for buys, margin estimate for sales and spreads
    assert r["buy"]["need_lot"] == 100 * 65
    naked = M.rates(NOW.date(), True)["naked_pct"] / 100 * 24000 * 65
    assert r["sell"]["need_lot"] == pytest.approx(naked, rel=1e-6) and r["sell"]["max_loss"] is None
    assert r["credit"]["max_loss"] == pytest.approx((100 - 49) * 65)       # 100 points wide minus the 49 received
    assert r["credit"]["need_lot"] < r["sell"]["need_lot"] / 2               # the hedge frees most of the margin
    d = json.loads(r["credit"]["data"])
    assert [l["q"] for l in d["legs"]] == [-1, 1]
    with b.bdb.lock:                                     # one row per signal: the facts and the app's rating, shared by its styles
        sig = [dict(x) for x in b.bdb.db.execute("SELECT * FROM bench_signals").fetchall()]
    assert len(sig) == 1 and sig[0]["sig"] == r["credit"]["sig"] == r["buy"]["sig"]
    tag = json.loads(sig[0]["tag"])
    assert tag["conv"] == 80 and tag["focus"] is True
    # the put falls: the sale and the credit spread make money, the call buys lose nothing yet
    for k in list(st):
        if k.startswith("PE"):
            st[k] = I(st[k].bid * 0.6, st[k].ask * 0.6)
    b.update(st, NOW.timestamp() + 300)
    with b.bdb.lock:
        rows = {x["style"]: dict(x) for x in b.bdb.db.execute("SELECT * FROM bench_trades WHERE net IS NOT NULL").fetchall()}
    assert rows["sell"]["reason"] == "target" and rows["sell"]["net"] > 0 and rows["sell"]["charges"] > 0
    assert rows["sell"]["gross"] == pytest.approx((-(100 * 0.6) - -99) * 65)    # bought back at the new ask
    assert rows["credit"]["reason"] == "target" and rows["credit"]["net"] > 0


def test_a_broken_style_is_skipped_and_the_rest_run(tmp_path, monkeypatch):
    f = tmp_path / "styles.json"
    f.write_text(json.dumps({"styles": {"buy": {"legs": [{"side": "with", "offset": 0, "q": 1}]},
                                        "bad": {"legs": [{"side": "sideways", "offset": 0, "q": 1}]},
                                        "worse": {"legs": []}}}))
    monkeypatch.setattr(B, "STYLE_FILE", f)
    B._LOADED.clear()
    try:
        styles, errs = B.load_styles()
        assert list(styles) == ["buy"] and len(errs) == 2
        e, _ = _full()
        b = B.Bench(Store(tmp_path / "live.db"), "upstox", log=lambda *a: None)
        assert b.add({"sym": "NIFTY", "kind": "orb", "direction": "up", "option": OPT}, e, NOW, CFG) == 1
        assert b.status()["errors"] and b.status()["styles"]["buy"]["opened"] == 1
    finally:
        B._LOADED.clear()


def test_margin_estimates_are_dated(tmp_path, monkeypatch):
    f = tmp_path / "margin.json"
    f.write_text(json.dumps({"rates": [{"from": "2020-01-01", "index": {"naked_pct": 5, "hedged_exposure_pct": 1, "expiry_day_extra_pct": 0},
                                        "stock": {"naked_pct": 15, "hedged_exposure_pct": 3, "expiry_day_extra_pct": 0}},
                                       {"from": "2026-01-01", "index": {"naked_pct": 7, "hedged_exposure_pct": 2, "expiry_day_extra_pct": 2},
                                        "stock": {"naked_pct": 18.5, "hedged_exposure_pct": 3.5, "expiry_day_extra_pct": 0}}]}))
    monkeypatch.setattr(M, "FILE", f)
    M._CACHE.clear()
    try:
        leg = [{"side": "PE", "strike": 24000, "q": -1, "price": 100}]
        assert M.money_needed(leg, 65, True, "2025-06-01")["need"] == pytest.approx(0.05 * 24000 * 65)
        assert M.money_needed(leg, 65, True, "2026-11-04")["need"] == pytest.approx(0.07 * 24000 * 65)
        assert M.money_needed(leg, 65, True, "2026-11-04", expiry_day=True)["need"] == pytest.approx(0.09 * 24000 * 65)
        debit = M.money_needed([{"side": "CE", "strike": 24000, "q": 1, "price": 100},
                                {"side": "CE", "strike": 24100, "q": -1, "price": 50}], 65, True, "2026-11-04")
        assert debit["max_loss"] == pytest.approx(50 * 65) and debit["need"] == pytest.approx(50 * 65 + 0.02 * 24100 * 65)
        buy = M.money_needed([{"side": "CE", "strike": 24000, "q": 1, "price": 100}], 65, True)
        assert buy["need"] == 6500 and buy["margin"] == 0
    finally:
        M._CACHE.clear()


def _rows(st, rows):
    B.Bench(st, "upstox", log=lambda *a: None)
    bdb = B.db_for(st)                                   # 2.31: the bench has its own database file (data/bench.db)
    with bdb.lock:
        bdb.db.executemany("INSERT INTO bench_trades(ts, day, source, sym, kind, variant, style, family, sig, exit_ts, net, cost_lot, "
                          "risk_lot, need_lot, regime) VALUES (?, ?, 'upstox', ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, 'trend_up')", rows)
        bdb.db.commit()


def test_board_by_family_and_money_needed(tmp_path):
    st = Store(tmp_path / "live.db")
    day = dt.date.today().isoformat()
    rows = []
    for i in range(10):
        rows.append((i, day, "NIFTY", "orb", "buy", "buy", f"s{i}", i + 60, 300, 6000, 1300, 6000))
        rows.append((i, day, "NIFTY", "orb", "sell", "sell", f"s{i}", i + 60, 500, -6000, 1300, 110000))
        rows.append((i, day, "NIFTY", "orb", "credit", "mixed", f"s{i}", i + 60, 200, -3000, 700, 35000))
    _rows(st, rows)
    small = B.board(st, "upstox", capital=40000, risk_pct=5.0, today=day)
    fits = {r["style"]: r["fits"] for r in small["rows"]}
    assert fits == {"buy": True, "sell": False, "credit": True}     # ₹35K margin fits 90% of ₹40K; ₹1.1L doesn't
    assert {f["family"] for f in small["families"]} == {"buy", "mixed", "sell"}
    only_sell = B.board(st, "upstox", capital=500000, risk_pct=1.5, today=day, family="sell")
    assert [r["style"] for r in only_sell["rows"]] == ["sell"] and only_sell["rows"][0]["fits"] and only_sell["best"]


def test_capital_accounts_follow_their_tier(tmp_path):
    st = Store(tmp_path / "live.db")
    d0 = dt.date.today()
    days = [(d0 - dt.timedelta(days=k)).isoformat() for k in (2, 1, 0)]
    rows, t = [], 1_000_000.0
    for day in days:
        for i in range(6):                               # 6 signals a day, 10 minutes apart, each closes before the next
            t += 600
            good = 800 if i % 2 == 0 else -200
            rows.append((t, day, f"S{i}", "good", "buy", "buy", f"g{t}", t + 300, good, 5000, 1200, 5000))
            rows.append((t, day, f"S{i}", "bad", "buy", "buy", f"b{t}", t + 300, -500, 5000, 1200, 5000))
    _rows(st, rows)
    rws = A.load_rows(st, "upstox", 60)
    small = A.run(rws, 40000, "buy", "all")
    assert small["n"] == 2 * 3                            # ₹40K tier: 2 trades a day, 1 lot
    big = A.run(rws, 500000, "buy", "all")
    assert big["n"] > small["n"] and big["capital"] == 500000
    # walk-forward: on the first day nothing is proven yet; later only "good" is taken
    pr = A.proven_by_day(rws, min_n=3)
    assert pr[days[0]] == {} and ("good", "", "buy") in pr[days[1]] and ("bad", "", "buy") not in pr[days[2]]
    pv = A.run(rws, 500000, "buy", "proven", pr)
    assert pv["n"] > 0 and all(x["label"].startswith("good") for x in pv["best_kinds"]) and pv["net"] > 0
    g = A.grid(st, "upstox", levels=[40000, 150000, 300000, 500000], days=60, max_age=0)
    assert len(g["cells"]) == 4 * 3 * 2 and {c["family"] for c in g["cells"]} == {"buy", "mixed", "sell"}


def test_daily_loss_stop_in_the_account(tmp_path):
    st = Store(tmp_path / "live.db")
    day = dt.date.today().isoformat()
    rows = [(1000.0 + 600 * i, day, f"S{i}", "x", "buy", "buy", f"x{i}", 1000.0 + 600 * i + 300, -1000, 4000, 900, 4000)
            for i in range(10)]
    _rows(st, rows)
    rws = A.load_rows(st, "upstox", 60)
    r = A.run(rws, 150000, "buy", "all")                  # 2 lots x -₹1,000; 4% daily loss = ₹6,000: stops after 3 losses
    assert r["n"] == 3 and r["net"] == -6000 and r["curve"][-1]["locked"]
    r2 = A.run(rws, 500000, "buy", "all")                 # medium tier: 3 lots, 3% = ₹15,000: stops after 5 losses
    assert r2["n"] == 5 and r2["net"] == -15000


def test_mover_pick_uses_each_stocks_own_range():
    metrics = {"QUIET": {"chg_pct": 1.2, "ret_15m": 0.1, "is_index": False},
               "WILD": {"chg_pct": 1.2, "ret_15m": 0.1, "is_index": False},
               "FRESH": {"chg_pct": 0.1, "ret_15m": -0.8, "is_index": False},
               "NIFTY": {"chg_pct": 0.2, "ret_15m": 0.3, "is_index": True}}
    daily = {"QUIET": {"htf": {"atr_pct": 1.0}}, "WILD": {"htf": {"atr_pct": 4.0}}, "FRESH": {"htf": {"atr_pct": 2.0}},
             "NIFTY": {"htf": {"atr_pct": 0.9}}}
    p = MV.pick(metrics, daily, dict(MV.DEFAULTS))
    syms = [x["sym"] for x in p]
    assert syms[0] == "NIFTY" and "QUIET" in syms and "WILD" not in syms
    assert next(x for x in p if x["sym"] == "FRESH")["direction"] == "down"


def test_vault_records_chains_news_and_context(tmp_path):
    from live.record import Recorder
    from live import vault
    r = Recorder(tmp_path, "upstox", "2026-11-04")
    res = {"sym": "RELIANCE", "expiry": "2026-11-25", "spot": 1500.0,
           "rows": [{"strike": 1500.0, "CE": {"key": "c", "ltp": 30, "bid": 29.5, "ask": 30.5, "oi": 1000, "iv": 22},
                     "PE": {"key": "p", "ltp": 28, "oi": 900}}]}
    assert r.chain(1000, res) == 2
    r.news([{"title": "Reliance results beat", "ts": 990, "source": "x", "syms": ["RELIANCE"], "tone": 1}], 1000)
    r.context(1000, "market", {"vix": 12.5})
    h = r.health()
    assert h["chain_rows"] == 2 and h["news"] == 1 and h["context"] == 1
    st = types.SimpleNamespace(lock=__import__("threading").Lock(), insts={"k": types.SimpleNamespace(key="k", ltp=1, vol=1, oi=1, iv=1, bid=1, ask=1, tbq=1, tsq=1, cvd=1)})
    r.snapshot(1000, st, {}, lambda m: m)
    r.finalize(keep_days=100000, log=lambda *a: None)
    parts = {p["day"]: p["parts"] for p in vault.days(tmp_path)}
    assert {"minutes", "chains", "news", "context"} <= set(parts["2026-11-04"])


def test_bench_keeps_its_own_database_and_copies_the_old_trades_once(tmp_path):
    st = Store(tmp_path / "live.db")
    with st.lock:                                        # a 2.30 bench table in the main database
        st.db.executescript(B.SQL)
        st.db.execute("INSERT INTO bench_trades(ts, day, source, sym, kind, style, net, cost_lot, risk_lot) "
                      "VALUES (1, ?, 'upstox', 'NIFTY', 'orb', 'buy', 250, 6000, 1300)", (dt.date.today().isoformat(),))
        st.db.commit()
    B._DBS.clear()
    b = B.Bench(st, "upstox", log=lambda *a: None)
    assert b.bdb.path == (tmp_path / "bench.db").resolve()
    B._DBS.clear()
    B.Bench(st, "upstox", log=lambda *a: None)           # a restart does not copy them again
    with b.bdb.lock:
        assert b.bdb.db.execute("SELECT COUNT(*) FROM bench_trades").fetchone()[0] == 1
    with st.lock:                                        # the originals stay where they were
        assert st.db.execute("SELECT COUNT(*) FROM bench_trades").fetchone()[0] == 1
    bd = B.board(st, "upstox", capital=40000, risk_pct=5.0)
    assert bd["n_closed"] == 1 and bd["rows"][0]["all"]["total"] == 250
    con = b.bdb.reader()                                 # readers have their own connection, read-only
    with pytest.raises(Exception):
        con.execute("DELETE FROM bench_trades")
    con.close()
