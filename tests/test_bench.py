"""2.30: test bench (honest paper trade for every signal), Strategy board, capital, prop challenge, tilt coach."""
import datetime as dt
import types

import pytest

from live import bench as B, capital, coach
from live.clock import IST
from live.store import Store


class I:
    def __init__(self, bid, ask):
        self.bid, self.ask, self.ltp = bid, ask, (bid + ask) / 2


class St(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)


def _engine(state):
    exp = dt.date(2026, 11, 10)
    o = lambda k: types.SimpleNamespace(key=k, lot=65)
    chain = {exp: {23800.0: {"CE": o("CE800")}, 23850.0: {"CE": o("CE850")}, 23900.0: {"CE": o("CE900")}}}
    u = types.SimpleNamespace(is_index=True, chains=chain)
    return types.SimpleNamespace(state=state, uni=types.SimpleNamespace(underlyings={"NIFTY": u}), regimes={"NIFTY": {"code": "trend_up"}})


OPT = {"key": "CE800", "side": "CE", "strike": 23800.0, "expiry": "2026-11-10", "lot": 65, "label": "NIFTY 23800 CE"}
CFG = {"target_pct": 30, "stop_pct": 20, "max_hold_min": 60, "no_entry_after": "14:45"}


def test_bench_fills_like_the_paper_account(tmp_path):
    st = St(CE800=I(99, 100), CE850=I(74, 75), CE900=I(54, 55))
    e = _engine(st)
    b = B.Bench(Store(tmp_path / "live.db"), "upstox")
    now = dt.datetime(2026, 11, 4, 10, 0, tzinfo=IST)
    n = b.add({"sym": "NIFTY", "kind": "orb", "direction": "up", "option": OPT, "id": 7}, e, now, CFG)
    assert n == 2                                                       # option buy + debit spread
    assert b.add({"sym": "NIFTY", "kind": "orb", "direction": "up", "option": OPT}, e, now, CFG) == 0   # cooldown
    buy = next(r for r in b.open if r["style"] == "buy")
    spr = next(r for r in b.open if r["style"] == "spread")
    assert buy["entry"] == 100 and spr["entry"] == 100 - 54          # pay the ask, receive the bid on the sold leg
    st["CE800"], st["CE900"] = I(131, 132), I(60, 61)                  # the bought option's bid passes +30%
    b.update(st, now.timestamp() + 600)
    with b.store.lock:
        rows = {r["style"]: dict(r) for r in b.store.db.execute("SELECT * FROM bench_trades").fetchall()}
    assert rows["buy"]["reason"] == "target" and rows["buy"]["exit"] == 131
    assert rows["buy"]["net"] == round((131 - 100) * 65 - rows["buy"]["charges"], 2) and rows["buy"]["charges"] > 0
    assert rows["spread"]["exit"] is None or rows["spread"]["exit"] == 131 - 61


def test_bench_respects_the_entry_window(tmp_path):
    e = _engine(St(CE800=I(99, 100)))
    b = B.Bench(Store(tmp_path / "live.db"), "upstox")
    late = dt.datetime(2026, 11, 4, 14, 50, tzinfo=IST)
    assert b.add({"sym": "NIFTY", "kind": "orb", "direction": "up", "option": OPT}, e, late, CFG) == 0


def test_board_ranks_by_money_after_costs_and_capital(tmp_path):
    st = Store(tmp_path / "live.db")
    B.Bench(st, "upstox")
    day = dt.date.today().isoformat()
    rows = []
    for i in range(8):
        rows.append(("good", "buy", "trend_up", 900 if i % 4 else -300, 6000, 1300))
        rows.append(("bad", "buy", "range", -400 if i % 3 else 200, 6000, 1300))
        rows.append(("pricey", "buy", "trend_up", 5000, 60000, 12000))
    with st.lock:
        st.db.executemany("INSERT INTO bench_trades(ts, day, source, sym, kind, style, regime, net, cost_lot, risk_lot, is_index) "
                          "VALUES (0, ?, 'upstox', 'NIFTY', ?, ?, ?, ?, ?, ?, 1)", [(day, *r) for r in rows])
        st.db.commit()
    bd = B.board(st, "upstox", capital=40000, risk_pct=5.0, regime="trend_up", today=day)
    labels = [r["kind"] for r in bd["rows"]]
    assert labels[0] in ("good", "pricey")
    best = [r["kind"] for r in bd["best"]]
    assert best == ["good"]                                          # "pricey" doesn't fit ₹40K, "bad" loses
    big = B.board(st, "upstox", capital=2_000_000, risk_pct=1.0, regime="trend_up", today=day)
    assert "pricey" in [r["kind"] for r in big["best"]]
    g = next(r for r in bd["rows"] if r["kind"] == "good")
    assert g["today"]["n"] == 8 and g["regime"]["n"] == 8 and g["all"]["rank"] is not None


def test_capital_reads_any_amount_and_keeps_the_40k_preset():
    from live.paper import SMALL_ACCOUNT
    for t, v in (("40k", 40000), ("₹40,000", 40000), ("1.5 lakh", 150000), ("2,00,000", 200000), ("1.2 crores", 1.2e7),
                 ("Rs. 75,000", 75000), (500000, 500000)):
        assert capital.parse_amount(t) == v, t
    for bad in ("40", "abc", "5 bananas", "2000 cr", ""):
        with pytest.raises(ValueError):
            capital.parse_amount(bad)
    s = capital.paper_settings(40000)
    assert {k: s[k] for k in SMALL_ACCOUNT if k in s} == {k: v for k, v in SMALL_ACCOUNT.items() if k in s}


def _trade(day, h, net, mode="auto"):
    t0 = dt.datetime.fromisoformat(f"{day}T{h:02d}:00:00+05:30").timestamp()
    return {"status": "closed", "entry_ts": t0, "exit_ts": t0 + 600, "net": net, "mode": mode, "day": day}


def test_challenge_and_patterns():
    days = [f"2026-11-{d:02d}" for d in range(2, 28) if dt.date(2026, 11, d).weekday() < 5]
    win = [_trade(d, 10, 300) for d in days[:20]]
    ok = coach.challenge(win, 40000, None)
    assert ok["state"] == "passed" and ok["net"] == 6000
    bust = win[:3] + [_trade(days[3], 10, -1000), _trade(days[3], 11, -800)]
    bad = coach.challenge(bust, 40000, None)
    assert bad["state"] == "failed" and "Daily loss" in bad["why"]
    seq = []
    for i, d in enumerate(days[:6]):
        seq += [_trade(d, 10, -200), _trade(d, 11, -150), _trade(d, 12, -100, "override"), _trade(d, 13, 250)]
    p = coach.patterns(seq)
    assert p["after_2_losses"]["n"] >= 3 and any("After 2 losses" in l for l in p["lines"])
    today = [_trade(days[0], 10, -200), _trade(days[0], 11, -100)]
    assert "Two losses in a row" in coach.streak_warning(today, seq)
    assert coach.streak_warning([_trade(days[0], 10, 100)], seq) is None


def test_library_rules_on_the_bench_once_per_entry():
    from live.chartdata import ChartData
    cd = ChartData.__new__(ChartData)
    cd.e = types.SimpleNamespace(log=lambda *a: None)
    rules = cd._bench_rules()
    sig = {(str(r["conds"]), r["dir"], str(r.get("win"))) for r in rules}
    assert rules and len(sig) == len(rules) and all(r["dir"] in (1, -1) for r in rules)
    assert len({r["_variant"] for r in rules}) == len(rules) and any("book" in r["_name"] for r in rules)
