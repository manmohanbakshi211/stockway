"""Option selling (0.12.0): the sold-option version of every setup, short fills / exits / charges, selling paper
accounts with margin, the journal's kind column, the selling yardstick; and the 10 EMA pullback trigger."""
import datetime as dt
import json
import sqlite3

import numpy as np
import pandas as pd
import pytest

from tanu.data.facts import Facts
from tanu.engine.core import Engine
from tanu.engine.tf import EmaAlerts
from tanu.evaluate import report
from tanu.journal.store import Journal
from tanu.paper.accounts import SPEC, Accounts
from tanu.setups.registry import Exit, SellPlan, Setup
from tanu.state.day import DayContext

DAY = "2026-11-13"
T0 = int(dt.datetime.fromisoformat(DAY + "T09:15:00+05:30").timestamp())


# ---------------------------------------------------------------- 10 EMA pullback trigger
def test_pullback_red_candle_above_ema_then_break_of_its_high_gives_plus_one_with_ema_stop():
    a = EmaAlerts(5, 10, ema=100.0)
    out, m = [], 0
    for o, h, l, c in [(110, 112, 109, 111)] * 5 + [(111, 111.5, 106, 107)] * 5 + [(107, 113, 107, 112.5)] * 2:
        a.on_bar(m, o, h, l, c)
        out.append(a.pull)
        m += 1
    # candle 1 green above EMA (no trigger); candle 2 red (111 -> 107), low 106 > EMA: long trigger (high 111.5)
    hits = [(i, s, round(st, 2)) for i, (s, st) in enumerate(out) if s]
    ema_after_2 = a.ema
    assert hits == [(10, 1, round(ema_after_2, 2))]               # first minute of candle 3 breaks 111.5


def test_pullback_green_candle_below_ema_then_break_of_its_low_gives_minus_one():
    a = EmaAlerts(5, 10, ema=100.0)
    out, m = [], 0
    for o, h, l, c in [(90, 91, 89, 89.5)] * 5 + [(89, 94, 89, 93)] * 5 + [(93, 93, 88, 88.5)] * 2:
        a.on_bar(m, o, h, l, c)
        out.append(a.pull)
        m += 1
    assert [(i, s) for i, (s, _) in enumerate(out) if s] == [(10, -1)]


# ---------------------------------------------------------------- selling in the engine
class Opt:
    """Strikes every 50 around 25,000; option prices follow a simple path per test."""

    def __init__(self, price):
        self.price = price

    def atm(self, sym, day, t, spot, side):
        return round(spot / 50) * 50

    def strikes(self, sym, day, t, side):
        return [24800.0 + 50 * i for i in range(9)]

    def bar(self, sym, day, strike, side, t):
        p = self.price(int((t - T0) // 60), strike, side)
        return (p, p, p, p)


def _setup(sell=None):
    return Setup(id="sig10", name="t", why="t", source="t", valid_from=DAY, symbols=["NIFTY"], window=("09:25", "09:25"),
                 when=[("m", "==", 10), ("d_long", ">", 0)], exit=Exit(atr_init=50, atr_trail=50), sell=sell or SellPlan())


def _run(setup, price, n=60, journal=None):
    eng = Engine([setup], Opt(price))
    if journal is not None:
        eng.listeners.append(Accounts(journal))
    eng.start_day("NIFTY", DAY, DayContext(prev_close=25000.0, med_range=1.2, frac=np.linspace(0.2, 1, 375)))
    for m in range(n):
        eng.on_bar("NIFTY", T0 + 60 * m, 25000, 25001, 24999, 25000)
    eng.end_day("NIFTY")
    return eng


def test_a_bullish_signal_also_sells_an_out_of_the_money_put_and_keeps_half_the_premium():
    # the sold put (24950, one strike below the 25000 ATM) decays from 100 to 40
    price = lambda m, k, side: max(100.0 - 2 * max(m - 11, 0), 40.0) if side == "PE" else 100.0
    eng = _run(_setup(), price)
    tr = {t["kind"]: t for t in eng.trades()}
    s = tr["sell"]
    assert s["setup"] == "sig10@sell" and s["side"] == "PE" and s["strike"] == 24950.0 and s["d"] == 1
    assert s["reason"] == "premium_target"
    sp = 0.5 / 100                                                      # default spread for an unknown symbol
    entry = 100 * (1 - sp / 2) - 0.05
    assert s["entry"] == pytest.approx(entry)
    exit_ = 48.0 * (1 + sp / 2) + 0.05                                # target 49.85; that minute opened at 48
    assert s["exit"] == pytest.approx(exit_)
    ch = Facts().option_charges(exit_, entry, 65, DAY)                 # STT on the sell side = the entry
    assert s["pnl_rs"] == pytest.approx(round((entry - exit_) * 65 - ch, 2))
    assert s["mfe_pct"] > 0 and s["pnl_rs"] > 0
    assert tr["buy"]["setup"] == "sig10" and tr["buy"]["side"] == "CE"  # the bought version is unchanged


def test_premium_stop_buys_back_when_the_sold_option_rises_30_percent():
    price = lambda m, k, side: 100.0 + 5 * max(m - 11, 0) if side == "PE" else 100.0
    s = {t["kind"]: t for t in _run(_setup(), price).trades()}["sell"]
    assert s["reason"] == "premium_stop" and s["pnl_rs"] < 0 and s["pct"] <= -30


def test_at_stop_strike_sells_at_the_setups_stop_level():
    eng = _run(_setup(SellPlan(strike="at_stop")), lambda m, k, side: 100.0, n=15)
    sold = [p for p in eng.closed + eng.open if p.kind == "sell"][0]
    assert sold.strike == 24900.0                                      # stop = 25000 - 50 x ATR (2) = 24900


def test_sell_versions_can_be_switched_off_per_setup():
    eng = _run(_setup(SellPlan(on=False)), lambda m, k, side: 100.0, n=15)
    assert all(p.kind == "buy" for p in eng.closed + eng.open)


# ---------------------------------------------------------------- accounts, journal, yardstick
def test_four_accounts_at_five_lakh_and_selling_accounts_take_only_sold_options(tmp_path):
    spec = json.loads(SPEC.read_text())
    assert {a["id"]: (a["kind"], a["capital"]) for a in spec["accounts"]} == {
        "shadow": ("buy", 500000), "sniper": ("buy", 500000), "shadow_sell": ("sell", 500000),
        "sniper_sell": ("sell", 500000), "shadow_multileg": ("multileg", 500000)}
    j = Journal(tmp_path / "j.db")
    price = lambda m, k, side: max(100.0 - 2 * max(m - 11, 0), 40.0) if side == "PE" else 100.0
    _run(_setup(), price, journal=j)
    rows = {r["account"]: r for r in j.trades()}
    assert set(rows) == {"shadow", "shadow_sell"}                      # snipers take only trial / live setups
    assert rows["shadow"]["kind"] == "buy" and rows["shadow"]["setup"] == "sig10"
    s = rows["shadow_sell"]
    assert s["kind"] == "sell" and s["setup"] == "sig10@sell" and s["pnl_rs"] > 0
    assert s["margin"] == pytest.approx(25000 * 65 * 0.12, rel=0.01)  # notional x 12% (facts/margins.json)


def test_sniper_sell_refuses_when_margin_would_pass_its_limit(tmp_path):
    spec = json.loads(SPEC.read_text())
    for a in spec["accounts"]:
        if a["id"] == "sniper_sell":
            a.update(setups="all", capital=200000)                    # 80% of 2 lakh < one lot's ~1.95 lakh margin
    p = tmp_path / "acc.json"
    p.write_text(json.dumps(spec))
    j = Journal(tmp_path / "j.db")
    eng = Engine([_setup()], Opt(lambda m, k, side: 100.0))
    eng.listeners.append(Accounts(j, spec_path=p))
    eng.start_day("NIFTY", DAY, DayContext(prev_close=25000.0, med_range=1.2, frac=np.linspace(0.2, 1, 375)))
    for m in range(20):
        eng.on_bar("NIFTY", T0 + 60 * m, 25000, 25001, 24999, 25000)
    eng.end_day("NIFTY")
    why = [b["reason"] for b in j.blocked_rows() if b["account"] == "sniper_sell"]
    assert why and "margin" in why[0]


def test_old_journal_without_kind_opens_and_reads_as_buy(tmp_path):
    db = sqlite3.connect(tmp_path / "old.db")
    db.executescript("""CREATE TABLE trades (account TEXT, setup TEXT, sym TEXT, day TEXT, d INTEGER, side TEXT, strike REAL,
      entry_t REAL, exit_t REAL, entry REAL, exit REAL, lots INTEGER, lot INTEGER, qty INTEGER, charges REAL, pnl_rs REAL,
      pct REAL, reason TEXT, mfe_pct REAL, mae_pct REAL, minutes REAL, und_entry REAL, und_exit REAL, state TEXT,
      PRIMARY KEY (account, setup, sym, day, d, entry_t));
      INSERT INTO trades (account, setup, sym, day, d, entry_t, state) VALUES ('shadow','x','NIFTY','2026-11-12',1,1,'{}');""")
    db.commit()
    db.close()
    (r,) = Journal(tmp_path / "old.db").trades()
    assert r["kind"] == "buy"


def test_selling_versions_are_judged_against_the_selling_yardstick():
    rng = np.random.default_rng(1)
    days = pd.bdate_range("2025-01-01", periods=120).strftime("%Y-%m-%d")
    buy, sell = rng.normal(0, 5, len(days)), rng.normal(3, 5, len(days))
    rows = []
    for s, vals in (("random_baseline", buy), ("random_baseline@sell", sell), ("x", buy), ("x@sell", sell)):
        rows += [{"setup": s, "sym": "NIFTY", "day": d, "pct": v, "pnl_rs": 1.0, "reason": "stop", "minutes": 30,
                  "entry": 100.0} for d, v in zip(days, vals)]
    rep = report.evaluate(pd.DataFrame(rows))
    by = {r["setup"]: r for r in rep["setups"]}
    assert "passes" not in by["random_baseline@sell"] and rep["k"] == 2          # yardsticks are not ideas
    assert by["x@sell"]["passes"] is False and "does not beat the baseline" in by["x@sell"]["why_not"]
    # without its selling yardstick in the run it cannot pass at all
    only = pd.DataFrame([r for r in rows if r["setup"] in ("random_baseline", "x@sell")])
    r = {r["setup"]: r for r in report.evaluate(only)["setups"]}["x@sell"]
    assert r["passes"] is False and "no yardstick (random_baseline@sell) in this run" in r["why_not"]
