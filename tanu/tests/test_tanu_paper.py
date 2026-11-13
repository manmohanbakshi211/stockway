"""T4: paper accounts and their risk rules, the journal (footprints, idempotent), the evening review, and the engine
driving accounts through its hooks (a failing listener never stops the engine)."""
import datetime as dt
import json
from types import SimpleNamespace

import numpy as np

from tanu.engine.core import Engine, Position
from tanu.journal import review as RV
from tanu.journal.store import Journal
from tanu.paper.accounts import Account, Accounts
from tanu.setups import registry as RG
from tanu.state import day as SD

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DAY = "2026-11-13"


def _t(hh, mm):
    return dt.datetime(2026, 11, 13, hh, mm, tzinfo=IST).timestamp()


def _eng(status="trial"):
    return SimpleNamespace(setups=[SimpleNamespace(id="s1", status=status)], lots={"NIFTY": 65, "BANKNIFTY": 30})


def _p(sym="NIFTY", d=1, entry=100.0, at=(10, 0), exit_=None, mins=20, peak=None, trough=None):
    p = Position(setup="s1", sym=sym, day=DAY, d=d, strike=25000.0, side="CE" if d > 0 else "PE", signal_t=0.0,
                 state={"day": "trend", "spot": 25000.0})
    p.entry, p.entry_t = entry, _t(*at)
    p.peak, p.trough = peak or entry, trough or entry
    if exit_ is not None:
        p.exit, p.exit_t, p.reason = exit_, p.entry_t + 60 * mins, "time"
    return p


SNIPER = {"id": "sniper", "setups": "trial+live", "symbols": ["NIFTY", "BANKNIFTY"], "capital": 100000, "lots": 1,
          "risk": {"max_open": 1, "max_trades_day": 3, "daily_loss_pct": 3, "stop_after_losses": 2,
                   "max_premium_pct": 30, "one_per_group": True, "no_entry_after": "14:30"}}


def test_risk_rules_refuse_with_reasons(tmp_path):
    j = Journal(tmp_path / "j.db")
    a = Account(SNIPER, j, {"index": ["NIFTY", "BANKNIFTY"]})
    e = _eng()
    a.on_fill(e, _p())                                                      # taken
    a.on_fill(e, _p(sym="BANKNIFTY", at=(10, 5)))                           # max_open 1
    assert "already 1 open" in j.blocked_rows()[0]["reason"]
    a.open.clear()
    a.risk["max_open"] = 5
    a.on_fill(e, _p())
    a.on_fill(e, _p(sym="BANKNIFTY", at=(10, 6)))                           # same direction, same group
    assert any("same direction in group 'index'" in b["reason"] for b in j.blocked_rows())
    a.open.clear()
    a.on_fill(e, _p(at=(14, 45)))
    a.on_fill(e, _p(entry=600.0, at=(11, 0)))                               # 600 x 65 = 39,000 > 30% of 1L
    a.on_fill(e, _p(sym="SBIN", at=(11, 1)))
    reasons = " | ".join(b["reason"] for b in j.blocked_rows())
    assert "no new trades after 14:30" in reasons and "over 30% of capital" in reasons and "SBIN is not traded" in reasons
    assert Account(SNIPER, j).wants("shadow", "s1") is False and Account(SNIPER, j).wants("live", "s1")


def test_daily_loss_and_losing_streak_stop_the_day(tmp_path):
    j = Journal(tmp_path / "j.db")
    a = Account({**SNIPER, "risk": {"stop_after_losses": 2}}, j)
    e = _eng()
    for k in range(2):
        p = _p(at=(10, k))
        a.on_fill(e, p)
        p.exit, p.exit_t, p.reason = 90.0, p.entry_t + 600, "time"
        a.on_close(e, p)
    assert a.stopped == "2 losses in a row"
    a.on_fill(e, _p(at=(11, 0)))
    assert "stopped for the day" in j.blocked_rows()[-1]["reason"]
    b = Account({**SNIPER, "risk": {"daily_loss_pct": 3}}, Journal(tmp_path / "k.db"))
    p = _p()
    b.on_fill(e, p)
    p.exit, p.exit_t, p.reason = 40.0, p.entry_t + 600, "premium_stop"         # 60 x 65 = ₹3,900 + charges > 3%
    b.on_close(e, p)
    assert b.stopped.startswith("daily loss")
    b.on_fill(e, _p(at=(12, 0)))                                              # the next day starts fresh
    p2 = _p(at=(10, 0))
    p2.day = "2026-11-16"
    b.on_fill(e, p2)
    assert b.stopped is None and b.trades_today == 1


def test_journal_keeps_full_footprints_and_is_idempotent(tmp_path):
    j = Journal(tmp_path / "j.db")
    a = Account({"id": "x", "setups": "all", "capital": 1e7, "lots": 2, "risk": {}}, j)
    e = _eng("shadow")
    for _ in range(2):                                                        # the same history twice
        p = _p(exit_=120.0, peak=130.0, trough=95.0)
        a.on_fill(e, p)
        a.on_close(e, p)
    rows = j.trades()
    assert len(rows) == 1
    r = rows[0]
    assert (r["qty"], r["mfe_pct"], r["mae_pct"], r["minutes"]) == (130, 30.0, -5.0, 20.0)
    assert r["pnl_rs"] == round(20 * 130 - r["charges"], 2) and r["state"]["day"] == "trend"


def test_review_counts_lessons_and_writes_questions(tmp_path):
    j = Journal(tmp_path / "j.db")
    a = Account({"id": "x", "setups": "all", "capital": 1e7, "lots": 1, "risk": {}}, j)
    e = _eng()
    for k, (ex, pk) in enumerate([(80, 125), (85, 115), (90, 101), (130, 140), (88, 112)]):
        p = _p(at=(10, k), exit_=float(ex), peak=float(pk))
        a.on_fill(e, p)
        a.on_close(e, p)
    rev = RV.daily(j, DAY)
    x = rev["accounts"]["x"]
    assert x["summary"]["trades"] == 5 and x["summary"]["wins"] == 1 and x["losers"] == 4
    assert x["green_then_red"] == 3 and x["never_moved"] == 1
    assert rev["hypotheses"][0]["idea"] == "exits give back gains"
    p = RV.write(rev, tmp_path)
    assert "Losers that were up 10%+ first: 3 of 4" in p.read_text()
    assert json.loads((tmp_path / "research" / "hypotheses.jsonl").read_text().splitlines()[0])["day"] == DAY


class FakeOptions:
    def __init__(self, spot_at):
        self.spot_at = spot_at

    def atm(self, sym, day, t, spot, side):
        return round(spot / 50) * 50

    def bar(self, sym, day, strike, side, t):
        s = self.spot_at.get(int(t))
        if s is None:
            return None
        p = max(1.0, 100 + (0.5 if side == "CE" else -0.5) * (s - strike))
        return (p, p + 1, p - 1, p)


class Broken:
    def on_fill(self, engine, p):
        raise RuntimeError("boom")

    def on_close(self, engine, p):
        raise RuntimeError("boom")


def test_engine_drives_accounts_and_a_broken_listener_is_harmless(tmp_path):
    ctx = SD.DayContext(prev_close=25000.0, med_range=0.8, frac=np.linspace(0.1, 1.0, 375))
    c = np.full(375, 25000.0)
    c[:60] += np.linspace(0, 20, 60)
    c[60:120] = c[59] + np.linspace(0, 600, 60)
    c[120:] = c[119] - np.linspace(0, 40, 255)
    t = np.array([_t(9, 15) + 60 * m for m in range(375)])
    setups = [s for s in RG.load()[0] if s.id == "sharp_move_trend"]
    j = Journal(tmp_path / "j.db")
    e = Engine(setups, FakeOptions({int(a): b for a, b in zip(t, c)}), spread_pct={"NIFTY": 0.3})
    e.listeners += [Broken(), Accounts(j)]
    e.start_day("NIFTY", DAY, ctx)
    for a, b in zip(t, c):
        e.on_bar("NIFTY", float(a), b, b + 1, b - 1, b)
    e.end_day("NIFTY")
    rows = j.trades()
    assert [r["account"] for r in rows] == ["shadow"]                       # sniper takes only trial / live setups
    assert rows[0]["pnl_rs"] > 0 and rows[0]["mfe_pct"] > 0
    assert any("Broken.on_fill" in x for x in e.closed[0].state["listener_errors"])
