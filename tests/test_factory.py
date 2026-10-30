"""Strategy Lab: rule evaluation, the 3-period guard, outside ideas, ebook reading (with a stand-in AI)."""
import datetime as dt
import json

import numpy as np
import pandas as pd

from lab import factory as F


def _book(n_days=150, seed=3, planted=True):
    rng = np.random.default_rng(seed)
    rows = []
    d0 = dt.date(2025, 1, 1)
    for k in range(n_days):
        day = d0 + dt.timedelta(days=k)
        for bar in range(2, 40):
            base = {f: float(rng.normal()) for f in F.FEATURES}
            base["tod"] = (bar + 1) * 5.0
            for d in (1, -1):
                noise = rng.normal(-200, 800)
                edge = 900 if planted and d == 1 and base["ret_b3"] > 1.0 else 0
                rows.append({"sym": "NIFTY", "date": day, "bar": bar, **base, "dir": d, "premium": 8000,
                             **{"pnl_" + r: noise + edge for r in F.EXITS}})
    return pd.DataFrame(rows)


def test_planted_edge_passes_and_noise_does_not():
    b = F.Book(_book())
    good = F.validate({"dir": 1, "conds": [["ret_b3", ">", 1.0]], "win": [15, 330], "exit": "std", "max_day": 1, "syms": ["NIFTY"]})
    r = F.score(b, good)
    assert r["stage"] == "promoted" and r["hold"]["avg"] > 0 and "Buy the ATM call" in r["rule"]
    bad = F.validate({"dir": -1, "conds": [["rsi", ">", 0.5]], "win": [15, 330], "exit": "std", "max_day": 1, "syms": ["NIFTY"]})
    assert F.score(b, bad)["stage"] != "promoted"
    # a rule that fails TRAIN is never promoted, even if later periods look good
    r2 = F.score(b, good)
    r2_train_fail = {**r2, "train": {"n": 5}}
    assert not F.passes(r2_train_fail["train"], F.PASS["train"])


def test_one_trade_per_day_and_stop_after_loss():
    b = F.Book(_book(30, planted=False))
    s = F.validate({"dir": 1, "conds": [["ret_b1", ">", -9]], "win": [15, 330], "exit": "quick", "max_day": 2, "syms": ["NIFTY"]})
    t = b.trades(s)
    per_day = t.groupby("day").size()
    assert per_day.max() <= 2
    for day, g in t.groupby("day"):                      # a second trade only after a winning first one
        if len(g) == 2:
            assert g["pnl"].iloc[0] > 0


def test_search_counts_and_outside_ideas(tmp_path):
    b = F.Book(_book(80, planted=False))
    passed, n = F.search(b, n_random=200, n_mutate=50, seed=1)
    assert n >= 200 and all(F.passes(F.quick_train(b, s), F.PASS["train"]) for s in passed)
    q = F.add_ideas(tmp_path, [{"dir": 1, "conds": [["rsi", "<", 30]], "win": [15, 200], "exit": "std"},
                               {"dir": 1, "conds": [["volume_profile", ">", 1]]}], source="ebook:test.pdf")
    assert q == {"queued": 1, "rejected": 1}
    assert F.load(tmp_path)["ideas"][0]["source"] == "ebook:test.pdf"


def test_signal_now_matches_backtest_features():
    c = np.array([100, 100.2, 100.5, 100.9, 101.4, 101.6])
    f = F.bar_features(c - 0.1, c + 0.2, c - 0.2, c, {"prev_close": 99.5, "prev_high": 100.5, "prev_low": 99, "gap": 0.4, "vix": 14, "dte": 2, "dow": 1})
    assert abs(f["ret_b3"][-1] - (101.6 / 100.5 - 1) * 100) < 1e-9 and f["tod"][-1] == 30
    rule = F.validate({"dir": 1, "conds": [["ret_b3", ">", 1.0], ["pdh_dist", ">", 0]], "win": [15, 60], "exit": "std", "syms": ["NIFTY"]})
    assert F.signal_now(rule, f, "NIFTY") and not F.signal_now(rule, f, "BANKNIFTY")


class FakeAI:
    def enabled(self):
        return True

    def ask(self, prompt, max_tokens=0, auto=True):
        assert "Do not quote the book" in prompt
        return json.dumps({"strategies": [{"dir": 1, "conds": [["or_pos", ">", 0.5], ["vs_twap", ">", 0]], "win": [15, 120],
                                           "exit": "swing", "note": "opening range breakout, ch. 3"}],
                           "cannot_test": [{"idea": "volume profile node", "needs": "volume profile"}]})


def test_ebook_to_queued_rules(tmp_path):
    from lab import ebooks
    (ebooks.book_dir(tmp_path) / "orb.txt").write_text(("Enter long on a breakout above the opening range high with a stop "
                                                       "below VWAP and a target of twice the risk. " * 400))
    rec = ebooks.extract(tmp_path, "orb.txt", FakeAI(), log=lambda m: None)
    assert rec["queued"] >= 1 and rec["cannot_test"]
    assert ebooks.books(tmp_path)[0]["done"]
    assert F.load(tmp_path)["ideas"][0]["source"] == "ebook:orb.txt"
