"""2.54 smart chart lines: line book, line finder, live grading. Display and shadow grading only, never trades.

Mandeep, 11 Nov 2026: "make some smart system that can draw lines on the chart by itself analysing each and everything
that matters"."""
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from lab import levelbook as LB
from live import linesmith as LS

ROOT = Path(__file__).resolve().parents[1]
P = {"away_pct": 0.15, "touch_pct": 0.03, "move_pct": 0.25, "brk_pct": 0.10}


def test_first_touch_hold_break_and_undecided():
    L = 100.0
    # comes down from 100.5 to 100.01 (touch), bounces to 100.4: HOLD
    cl = np.array([100.5, 100.3, 100.1, 100.02, 100.2, 100.3, 100.4])
    hi, lo = cl + 0.02, cl - 0.02
    t, side, res = LB.first_touch(L, 0, hi, lo, cl, P, 60)
    assert side == 1 and res == "hold" and t == 3
    # comes down, closes 0.2% below, runs to -0.4%: BREAK
    cl = np.array([100.5, 100.3, 100.05, 99.8, 99.7, 99.6, 99.55])
    t, side, res = LB.first_touch(L, 0, cl + 0.01, cl - 0.01, cl, P, 60)
    assert side == 1 and res == "break"
    # touches and goes nowhere: undecided
    cl = np.array([100.5, 100.3, 100.02, 100.05, 100.03, 100.04])
    assert LB.first_touch(L, 0, cl + 0.01, cl - 0.01, cl, P, 60)[2] is None
    # never far enough away first: no touch counted
    cl = np.array([100.05, 100.02, 100.01, 100.0])
    assert LB.first_touch(L, 0, cl, cl, cl, P, 60) is None


def test_day_lines_and_rounds_and_swings():
    prev = [(110 + i, 100 + i, 105 + i) for i in range(25)]
    lines = dict()
    for t, x in LB.day_lines("NIFTY", 23040.0, prev):
        lines.setdefault(t, []).append(x)
    assert lines["pdh"] == [134.0] and lines["pdl"] == [124.0] and lines["wk_h"] == [134.0] and lines["d20_l"] == [105.0]
    assert 23000.0 in lines["round_b"] and 23000.0 not in lines["round_s"] and 23100.0 in lines["round_s"]
    h1 = np.array([10, 11, 12, 13, 14] * 2 + [20] * 5 + [12] * 5 + [11] * 5 + [10] * 5 + [9] * 5 + [15] * 5, float)
    sw = LB.swings5(h1, h1 - 1)
    assert ("sw_h", 20.0, 25) in [(a, b, c) for a, b, c in sw]


def _book(hold, rnd, n=200, tr=None, ho=None):
    def cell(h, nn, htr, hho):
        return {"touches": nn, "n": nn, "hold": h, "n_tr": nn // 2, "hold_tr": htr, "n_ho": nn // 2, "hold_ho": hho}
    return {"speed_cuts": [0.4, 0.7], "cells": {
        "pdl": {"fast|against": cell(hold, n, tr if tr is not None else hold, ho if ho is not None else hold), "all": cell(50, 500, 50, 50)},
        "random": {"fast|against": cell(rnd, n, rnd, rnd), "all": cell(50, 900, 50, 50)},
        "pdh": {"fast|against": cell(40, 10, 40, 40), "all": cell(52, 300, 52, 52)}}}


def test_evidence_verdicts_and_honest_words():
    ev = LB.evidence(_book(38.0, 49.0), "pdl", 0.9, "against")
    assert ev["verdict"] == "likely breaks" and ev["matters"] and ev["cell"] == "fast|against"
    assert "broke 62% of 200 times when price came fast" in ev["words"] and "breaks clearly more often than a random line" in ev["words"]
    ev = LB.evidence(_book(38.0, 39.0), "pdl", 0.9, "against")          # same as random lines: the situation decides
    assert ev["verdict"] == "likely breaks" and not ev["matters"] and "adds little" in ev["words"]
    ev = LB.evidence(_book(38.0, 49.0, tr=30.0, ho=55.0), "pdl", 0.9, "against")   # halves disagree: not a real edge
    assert not ev["matters"]
    ev = LB.evidence(_book(38.0, 49.0), "pdh", 0.9, "against")          # too few touches in the fine cell: falls back to all
    assert ev["cell"] == "all" and ev["verdict"] == "coin flip"
    assert LB.evidence(_book(38.0, 49.0), "tl_up", 0.9, "against")["verdict"] == "not tested yet"
    assert LB.evidence(None, "pdl", 0.9, "against") is None


def _history(tmp_path, days=40, seed=1):
    rng = np.random.default_rng(seed)
    rows, px = [], 20000.0
    d0 = dt.date(2024, 12, 2)
    k = 0
    while k < days:
        d = d0 + dt.timedelta(days=k + (k // 5) * 2)
        t0 = dt.datetime.combine(d, dt.time(9, 15), tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))).timestamp()
        for i in range(375):
            o = px
            px *= 1 + rng.normal(0, 0.0006)
            rows.append([t0 + 60 * i, o, max(o, px) * 1.0002, min(o, px) * 0.9998, px, 0.0])
        k += 1
    f = tmp_path / "history" / "NIFTY"
    f.mkdir(parents=True)
    pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).to_parquet(f / "minutes.parquet")
    return rows


def test_build_book_from_history_and_prefer_yours(tmp_path):
    _history(tmp_path)
    book = LB.build(tmp_path, syms=("NIFTY", "BANKNIFTY"), log=lambda m: None)
    assert book["syms"]["NIFTY"]["days"] >= 15 and "random" in book["cells"] and "pdh" in book["cells"]
    assert {"all"} <= set(book["cells"]["random"]) and book["split"] == "2025-01-01"
    assert (tmp_path / "lab" / "level_book.json").exists()
    mine = LB.load(tmp_path)
    assert mine["_file"] == "yours"
    shipped = LB.load(tmp_path / "nothing-here")
    assert shipped["_file"] == "shipped" and shipped["touches"] > 1000


def test_line_finder_labels_actions_and_trendline(tmp_path):
    rows = _history(tmp_path, days=26, seed=3)
    day = rows[-375:][:200]
    prev = []
    for i in range(25):
        seg = rows[i * 375:(i + 1) * 375]
        prev.append((max(r[2] for r in seg), min(r[3] for r in seg), seg[-1][4]))
    out = LS.compute("NIFTY", day, prev, day[-1][4], {"oi_call": day[-1][4] * 1.003}, rows[-3 * 375:-175], LB.load(None))
    assert out["lines"] and all(x["label"] and x["action"] and x["color"] for x in out["lines"])
    assert [x["price"] for x in out["lines"]] == sorted((x["price"] for x in out["lines"]), reverse=True)
    sides = {x["side"] for x in out["lines"]}
    assert sides <= {"support", "resistance"}
    oi = [x for x in out["lines"] if "Call OI wall" in x["names"]]
    assert not oi or oi[0]["verdict"] in ("not tested yet",) or len(oi[0]["names"]) > 1
    # a clean falling trendline: lower highs every 30 minutes, closes below the line
    t0 = rows[-375][0]
    tl_rows = []
    for i in range(150):
        top = 20100 - i * 0.8
        wave = 15 * abs(((i % 30) - 15) / 15)
        c = top - 5 - wave
        tl_rows.append([t0 + 60 * i, c, c + (5 if i % 30 == 0 else 1), c - 2, c, 0])
    tls = LS.trendlines(tl_rows, tl_rows[-1][4])
    dn = [t for t in tls if t["type"] == "tl_dn"]
    assert dn and dn[0]["touches"] >= 2 and dn[0]["slope_per_hour"] < 0


def test_live_grader_records_touches_and_summary(tmp_path):
    from live.store import Store
    from live.linegrade import LineGrader
    rows = _history(tmp_path, days=26, seed=5)
    day_rows = rows[-375:]
    day = LS.ist_day(day_rows[0][0])

    class Charts:
        def today_bars(self, sym):
            return day_rows if sym == "NIFTY" else []

        def _session_start(self, d):
            return day_rows[0][0]

    class E:
        state, charts, data_dir = object(), Charts(), tmp_path
        metrics = {"NIFTY": {"spot": day_rows[-1][4], "opt": {}}}
        daily = {"NIFTY": {"d_ohlc": [[str(day - dt.timedelta(days=30 - i)), 0, r[0], r[1], r[2], 0]
                                     for i, r in enumerate([(max(x[2] for x in rows[j * 375:(j + 1) * 375]),
                                                             min(x[3] for x in rows[j * 375:(j + 1) * 375]),
                                                             rows[(j + 1) * 375 - 1][4]) for j in range(25)])]}}
        gex = {}

    st = Store(tmp_path / "live.db")
    g = LineGrader(E(), st, "upstox", log=lambda m: None)
    now = dt.datetime.combine(day, dt.time(15, 31))
    g.tick(now)
    g.tick(now)                                                    # idempotent
    n = st.db.execute("SELECT COUNT(*) FROM line_touches").fetchone()[0]
    assert n > 0
    s = g.summary(days=3650)
    assert s["touches"] == n and s["decided"] <= n and s["since"] == day.isoformat()


def test_lines_never_trade():
    for f in ("lab/levelbook.py", "live/linesmith.py", "live/linegrade.py"):
        t = (ROOT / f).read_text()
        for bad in ("_open(", "place_order", "paper.", "emit(", "/orders"):
            assert bad not in t, (f, bad)
    for f in ("live/paper.py", "live/riskgate.py", "live/books.py", "lab/factory.py"):
        t = (ROOT / f).read_text()
        assert "linesmith" not in t and "linegrade" not in t and "levelbook" not in t, f
    c = json.loads((ROOT / "rules" / "lines.json").read_text())
    assert c["valid_from"] and c["types"]["random"]
