"""2.60 mass search: every single / paired condition x window x direction x exit at once on the Lab's bar table, walk-
forward by month, luck line, best to the Lab's exams. Mandeep, 11 Nov 2026: "1000 magzine ... prove you best"."""
import datetime as dt

import numpy as np
import pandas as pd

from lab import factory as F
from lab import mass as MS


def _table(n_days=200, edge=True, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    d0 = dt.date(2025, 1, 1)
    for k in range(n_days):
        day = d0 + dt.timedelta(days=k)
        for sym in ("NIFTY", "BANKNIFTY"):
            for bar in range(64):
                tod = 15 + 5 * bar
                base = {f: float(rng.normal()) for f in ("rsi", "vs_twap", "ret_b1", "gap")}
                for d in (1, -1):
                    pnl = float(rng.normal(-100, 400))
                    if edge and d == 1 and base["rsi"] > 1.0 and tod <= 90:
                        pnl += 900                                           # a real edge: calls when RSI is high early
                    rows.append({"date": day, "sym": sym, "tod": float(tod), "dir": d, "premium": 8000, **base,
                                 **{"pnl_" + e: pnl for e in ("std", "quick", "trail")}})
    return pd.DataFrame(rows)


def _cfg(**kw):
    c = MS.cfg()
    c.update({"syms": ["NIFTY", "BANKNIFTY"], "exits": ["std", "quick"], "quantiles": [0.2, 0.8], "pair_top": 5,
              "queue_to_lab": False, **kw})
    return c


def test_finds_the_planted_edge_and_it_holds_on_unseen_months(tmp_path):
    r = MS.run(tmp_path, log=lambda m: None, c=_cfg(), df=_table())
    top = r["best"][0]
    assert top["dir"] == 1 and any(c[0] == "rsi" and c[1] == ">" for c in top["conds"]) and top["win"][0] == 15
    assert r["best"][0]["train_lcb"] > r["luck_lcb"]
    assert r["walk"] and r["walk_total"]["avg"] > 200                    # months after train: still paying
    assert r["walk_total"]["avg"] * r["walk_total"]["n"] > r["walk_total"]["base_total"]
    assert r["tested"] == (r["conditions"] + r["pairs"]) * r["windows"] * r["scopes"] * len(r["exits"])
    assert MS.view(tmp_path)["tested"] == r["tested"]


def test_no_edge_stays_near_the_luck_line(tmp_path):
    r = MS.run(tmp_path, log=lambda m: None, c=_cfg(), df=_table(edge=False))
    assert r["best"][0]["train_lcb"] < r["luck_lcb"] + 150                 # nothing real: the best is about luck
    assert r["walk_total"]["avg"] is None or r["walk_total"]["avg"] < 300


def test_rules_go_to_the_lab_exams_as_ideas(tmp_path, monkeypatch):
    got = {}
    monkeypatch.setattr(F, "queue_untested", lambda d, ideas, source: got.update(ideas=ideas, source=source) or {"queued": len(ideas)})
    r = MS.run(tmp_path, log=lambda m: None, c=_cfg(queue_to_lab=True), df=_table())
    assert got["source"] == "mass search" and got["ideas"] and r["to_lab"] == len(got["ideas"])
    v = F.validate(got["ideas"][0])
    assert v and v["dir"] == 1 and v["max_day"] == 1                    # a normal Lab rule: the usual three exams decide


def test_first_bar_in_the_window_is_the_trade():
    m = np.zeros((2, MS.NB), bool)
    m[0, 3] = m[0, 10] = True
    m[1, 30] = True
    any_, first = MS.fire(m, (15, 90))                                   # bars 0..15
    assert list(any_) == [True, False] and first[0] == 3


def test_job_registered():
    from lab import proc
    assert "mass" in proc.JOBS
