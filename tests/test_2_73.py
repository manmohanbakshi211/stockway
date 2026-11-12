"""2.73 (Mandeep, 13 Nov 2026): "the system should understand the market movement and choose what is the best for
making profit as per the market situation". The market situation picker: every live Lab rule's trades split by the
situation at entry (known at that bar only), learned on train + validation, checked on holdout; a live signal is taken
only when the rule is expected to make money in today's situation. Its own paper account; the main one is unchanged."""
import datetime as dt
import json
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from lab import factory as F
from lab import situation as S
from live import books as B
from live.store import Store

ROOT = Path(__file__).resolve().parents[1]


def test_situation_of_a_bar_is_activity_against_the_symbols_own_day():
    c = S.cfg()
    base = dict(vix=14.0, dte=5, d_trend=1, tod=60, med20=1.0, frac=0.5, vix_cuts=[12.0, 16.0], c=c)
    trend = S.classify(0.6, 0.55, 0.0, d=1, **base)        # range 0.6 vs 0.5 expected by now, moved 0.55 cleanly
    assert trend == {"day": "trend", "move": "with", "vix": "mid", "expiry": "far", "gap": "flat", "trend": "up"}
    assert S.classify(0.6, 0.55, 0.0, d=-1, **base)["move"] == "against"
    assert S.classify(0.2, 0.05, 0.0, d=1, **base)["day"] == "quiet"
    assert S.classify(0.6, 0.1, 0.0, d=1, **base)["day"] == "range"
    assert S.classify(0.6, 0.1, 0.5, d=1, **{**base, "dte": 0})["gap"] == "up"
    assert S.classify(0.6, 0.1, 0.5, d=1, **{**base, "dte": 0})["expiry"] == "expiry"
    assert "move" not in S.classify(0.6, 0.1, 0.0, d=0, **base)                 # a straddle has no side
    # the same day 10x bigger (another index): the same situation (it is measured in the symbol's own typical day)
    assert S.classify(6.0, 5.5, 0.0, d=1, **{**base, "med20": 10.0}) == trend


def test_expected_result_shrinks_small_samples():
    c = S.cfg()
    m = S.fit([100, 100, -300, -300], [{"day": "range"}, {"day": "range"}, {"day": "trend"}, {"day": "trend"}], c)
    assert m["m0"] == -100 and m["cells"]["day"] == {"range": [2, 100.0], "trend": [2, -300.0]}
    e, parts = S.expected(m, {"day": "range"}, c)
    assert e == pytest.approx(-100 + 200 * 2 / (2 + c["shrink_k"]), abs=0.1)   # 2 trades barely move it
    assert parts[0][:4] == ("day", "range", 2, 100.0)


def _table(n_days=120):
    rows, start = [], dt.date(2025, 1, 1)
    for k in range(n_days):
        day = start + dt.timedelta(days=k)
        trend = k % 2 == 0
        for tod, rng, ret in ((15, 0.4, 0.35 if trend else 0.1), (100, 0.7, 0.6 if trend else 0.1), (330, 1.0, 0.9 if trend else 0.1)):
            rows.append({"sym": "NIFTY", "date": day, "dir": 1, "tod": float(tod), "day_range": rng, "day_ret": ret, "gap": 0.0,
                         "vix": np.nan, "dte": 5.0, "d_trend": 0.0, "premium": 100.0,
                         "pnl_std": (-100.0 if trend else 200.0) if tod == 15 else np.nan})
    return pd.DataFrame(rows)


RULE = {"id": "R1", "dir": 1, "conds": [["tod", ">", 0]], "win": [15, 330], "exit": "std", "max_day": 1, "syms": ["NIFTY"],
        "rule": "test rule"}


def test_learns_which_days_suit_a_rule_and_checks_it_on_holdout(tmp_path, monkeypatch):
    c0 = S.cfg()
    monkeypatch.setattr(S, "cfg", lambda: {**c0, "trust": {**c0["trust"], "min_hold_trades": 10}})
    book = F.Book(_table(), feats=["tod", "day_range", "day_ret", "gap", "vix", "dte", "d_trend"])
    res = S.learn(tmp_path, book=book, rules=[RULE], log=lambda m: None)
    info = S.load(tmp_path)
    r = info["rules"]["R1"]
    assert r["hold"]["all"]["n"] == 24 and r["hold"]["picked"]["n"] == 12                 # trend days skipped
    assert r["hold"]["picked"]["total"] > r["hold"]["all"]["total"] and res["beats_holdout"]
    cells = r["model"]["cells"]["day"]
    assert cells["trend"][1] == -100 and cells["range"][1] == 200
    f = {"tod": np.array([15.0]), "day_range": np.array([0.4]), "day_ret": np.array([0.35]), "gap": np.array([0.0]),
         "vix": np.array([np.nan]), "dte": np.array([5.0]), "d_trend": np.array([0.0])}
    sit = S.live_situation(info, f, "NIFTY", 1)
    assert sit["day"] == "trend" and S.verdict(info, "R1", sit)["take"] is False
    f["day_ret"] = np.array([0.1])
    v = S.verdict(info, "R1", S.live_situation(info, f, "NIFTY", 1))
    assert v["take"] is True and v["exp"] > 0 and "range day so far" in v["why"]
    assert S.verdict(info, "NOPE", {})["take"] is None
    view = S.view(tmp_path, {"NIFTY": {**sit, "_mv": 1}}, {"R1": {"rule": "test rule"}})
    assert view["ready"] and view["rules"][0]["now"]["NIFTY"]["take"] is False and view["words"]["NIFTY"]


def test_a_broken_rule_is_skipped_and_the_lean_table_reads_only_what_it_needs(tmp_path):
    book = F.Book(_table(), feats=["tod", "day_range", "day_ret", "gap", "vix", "dte", "d_trend"])
    bad = {**RULE, "id": "BAD", "conds": [["no_such_fact", ">", 0]]}
    S.learn(tmp_path, book=book, rules=[RULE, bad], log=lambda m: None)
    info = S.load(tmp_path)
    assert "R1" in info["rules"] and ("error" in info["rules"]["BAD"] or "BAD" not in info["rules"])
    p = tmp_path / "t.parquet"
    df = _table()
    df["unused_fact"] = 1.0
    df.to_parquet(p)
    lb = S.lean_book(p, [RULE])
    assert "unused_fact" not in lb.cols and "tod" in lb.cols and len(lb.trades(RULE)) == 120


def test_default_book_is_unchanged():
    df = _table(30)
    a, b = F.Book(df), F.Book(df, feats=None)
    assert set(a.cols) == set(F.FEATURES) | {"dir", "sym"} == set(b.cols)
    pd.testing.assert_frame_equal(a.trades(RULE), b.trades(RULE))


MAIN = {"capital": 500000, "risk_pct": 1.0, "max_daily_loss_pct": 3.0, "target_pct": 20, "stop_pct": 15,
        "max_hold_min": 60, "exit_time": "15:15", "no_entry_after": "14:45", "min_severity": 2, "max_open": 5,
        "max_trades_day": 10, "max_lots": 5, "kinds": "all", "auto": True}


def test_auto_pick_account_takes_only_lab_signals_that_fit_today(tmp_path):
    st = Store(tmp_path / "t.db")
    bs = B.Books("upstox", st, dict(MAIN), None, log=lambda m: None)
    assert "autopick" in bs.books

    def a(kind="factory", take=True):
        d = {"focus": True, "score": 90, "risk": {"verdict": "PASS", "lots": 1}}
        if take is not None:
            d["situation"] = {"take": take}
        return {"id": 1, "sym": "NIFTY", "kind": kind, "severity": 3, "direction": "up", "option": {"key": "O"}, "data": d}
    assert bs.pick("autopick", a()) is None
    assert bs.pick("autopick", a(take=False)) == "the situation picker skips it today"
    assert "not learned" in bs.pick("autopick", a(take=None))
    assert bs.pick("autopick", a(kind="volume_surge")) == "not a Strategy Lab rule signal"
    spec = json.loads((ROOT / "rules" / "paper_books.json").read_text())["books"]["autopick"]
    assert spec["pick"]["situation"] and "Paper only" in spec["explain"]


def test_wiring_job_nightly_and_alert():
    from lab import proc
    assert "situation" in proc.JOBS
    eng = (ROOT / "live" / "engine.py").read_text()
    assert 'proc.run("situation", self.data_dir' in eng and "def situation_learn" in eng
    cd = (ROOT / "live" / "chartdata.py").read_text()
    assert "SIT.verdict(sit_info" in cd and '{"situation": pick}' in cd
    src = (ROOT / "lab" / "situation.py").read_text().lower()
    for bad in ("place_order", "modify_order", "cancel_order", "access_token"):
        assert bad not in src
