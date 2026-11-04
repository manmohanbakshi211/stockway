"""2.41 candle forecast: shapes and their trader names, honest training (models and the PAKKA level from train /
validation days, numbers from holdout days), a planted edge found and none in noise, a failing pattern bullet skipped,
the training job in a child process, live predictions at every stage and grading at the close."""
import datetime as dt
import sqlite3
import threading
import types

import numpy as np
import pandas as pd

from live import candlecast as CC, fortune as F, nextcandle as N

DAY = dt.date(2026, 11, 4)
SMALL = {**CC.DEFAULTS, "min_days": 20, "min_calls": 10, "min_calls_share": 0.0, "window": 6,
         "model": {"iters": 40, "lr": 0.1, "leaves": 15}, "similar": {"on": True, "neighbors": 20, "pool": 3000, "show": 3},
         "shape": {"on": True, "iters": 20, "leaves": 7},
         "patterns": {"list": ["inside_bar", "outside_bar", "no_such_pattern"], "iters": 20, "leaves": 7, "min_pos": 10, "min_calls": 5},
         "zone": {"cover": 0.90, "iters": 30, "leaves": 7}}


def _minutes(days, seed=1, start=DAY - dt.timedelta(days=120), plant=False, today=False, px=1000.0):
    """1-minute bars on weekdays. plant: after 3 minutes on heavy volume, a 5-minute candle turns sharply."""
    rng = np.random.default_rng(seed)
    rows, d, n = [], start, 0
    while n < days:
        if d.weekday() < 5 and (today or d < DAY):
            t0 = F._open_ts(d)
            for c in range(75):
                heavy = plant and rng.random() < 0.3
                o5, turn = px, 0.0
                for m in range(5):
                    o = px
                    if heavy and m == 3:
                        turn = -0.003 * np.sign(px - o5 or 1)          # against the move of the first 3 minutes
                    px *= 1 + rng.normal(0, 0.0008) + (turn if m >= 3 else 0.0)
                    v = (900 if heavy and m < 3 else 100) * (1 + rng.random())
                    rows.append([t0 + 60 * (5 * c + m), o, max(o, px) * 1.0001, min(o, px) * 0.9999, px, v])
            n += 1
        d += dt.timedelta(days=1)
    return rows


def test_shapes_and_their_names():
    assert CC.family(100, 101.1, 95, 101) == 1 and CC.fam_name(1, -1) == "Hammer" and CC.fam_name(1, 1) == "Hanging man"
    assert CC.family(100, 106, 98.9, 99) == 2 and CC.fam_name(2, 1) == "Shooting star" and CC.fam_name(2, -1) == "Inverted hammer"
    assert CC.family(100, 102, 98, 100.1) == 0 and CC.family(100, 104.1, 99.9, 104) == 4 and CC.family(100, 100.1, 95.9, 96) == 5
    assert CC.family(100, 103, 97, 101) == 3 and CC.family(100, 103, 99, 102.5) == 6


def _data(rows, sym_idx=0, window=6):
    return CC.dataset(rows, None, "5m", "1m", [1, 2, 3, 4], sym_idx, window)


def test_honest_training_finds_a_planted_turn_and_nothing_in_noise():
    d = _data(_minutes(70, seed=3, plant=True))
    logs = []
    b, rep = CC.train_tf(d, "5m", "1m", SMALL, log=logs.append)
    assert rep["train_to"] < rep["hold_from"] and rep["hold_days"] >= 10 and {s["k"] for s in rep["stages"]} == {1, 2, 3, 4}
    s3 = next(s for s in rep["stages"] if s["k"] == 3)
    assert s3["all_right"] >= s3["simple_all"] + 5                   # heavy volume then a turn: beyond 'keeps its colour'
    assert s3["zone"]["held"] >= 80 and 0 < s3["zone"]["width_atr"]
    assert "error" in s3["patterns"]["no_such_pattern"] and "base_rate" in s3["patterns"]["inside_bar"]   # a bad bullet is skipped
    assert all(l["n"] > 0 for l in s3["ladder"]) and s3["ladder"][-1]["hi"] == 1.0
    assert b["stages"][3]["colour"] is not None and len(b["stages"][3]["pool"]["A"]) <= 3000 and logs
    dn = _data(_minutes(70, seed=4))
    _, rn = CC.train_tf(dn, "5m", "1m", SMALL, log=lambda m: None)
    for s in rn["stages"]:
        assert abs(s["all_right"] - s["simple_all"]) <= 4             # a random walk: nothing beyond the plain rule
        if s.get("calls"):
            assert s["right"] >= 80                                   # PAKKA level holds on unseen days (target 90)


def _engine(tmp_path):
    class Store:
        def __init__(self):
            self.db = sqlite3.connect(":memory:", check_same_thread=False)
            self.lock = threading.Lock()
    return types.SimpleNamespace(store=Store(), source="upstox", data_dir=tmp_path, log=lambda m: None, running=True)


def _write_history(tmp_path, sym, rows):
    f = tmp_path / "history" / sym / "minutes.parquet"
    f.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).to_parquet(f, index=False)


def test_job_trains_in_a_child_and_live_stages_are_graded(tmp_path, monkeypatch):
    monkeypatch.setattr(N, "cfg", lambda: {**N.DEFAULTS, "cascade": [{"tf": "5m", "from": "1m", "after": 3}]})
    monkeypatch.setattr(CC, "cfg", lambda: SMALL)
    hn, hb = _minutes(45, seed=7, plant=True), _minutes(45, seed=8, px=2000.0)
    _write_history(tmp_path, "NIFTY", hn)
    _write_history(tmp_path, "BANKNIFTY", hb)
    out = CC.run(tmp_path, log=lambda m: None)
    assert out["trained"] == ["5m"] and (tmp_path / "candlecast" / "5m.joblib").exists()
    from lab import proc
    assert "candlecast" in proc.JOBS
    e = _engine(tmp_path)
    cast = CC.Cast(e)
    v = cast.view("NIFTY", "5m")
    assert v["status"] == "ready" and v["report"]["stages"] and "now" not in v
    assert cast.view("RELIANCE", "5m")["status"] == "symbol"
    today = _minutes(1, seed=9, start=DAY, today=True, px=hn[-1][4])
    mtoday = _minutes(1, seed=10, start=DAY, today=True, px=hb[-1][4])
    t0 = F._open_ts(DAY)
    tail, mtail = [list(r) for r in hn[-600:]], [list(r) for r in hb[-600:]]
    for k in (1, 2, 3, 4):
        now = t0 + 60 * k + 5
        cast.step("NIFTY", "5m", "1m", tail + today, mtail + mtoday, now, DAY.isoformat())
        cur = cast.cur[("NIFTY", "5m")]
        p = cur["stages"][k]
        assert cur["k"] == k and p["dir"] in ("up", "down") and 50 <= p["strength"] <= 100 and p["zone"][0] < p["zone"][1]
        assert p["shape"] and p["similar"]["n"] == 20 and len(p["similar"]["closest"]) == 3 and isinstance(p["pakka"], bool)
        assert {x["sym"] for x in p["similar"]["closest"]} <= {"NIFTY", "BANKNIFTY"}
    v = cast.view("NIFTY", "5m")
    assert v["now"]["k"] == 4 and v["now"]["time"] == int(t0 + F.IST_SHIFT)
    cast.step("NIFTY", "5m", "1m", tail + today, mtail + mtoday, t0 + 300 + 5, DAY.isoformat())     # the candle closed
    rows = e.store.db.execute("SELECT k, right, simple_right, fam_right, zone_in, actual FROM candlecast ORDER BY k").fetchall()
    assert [r[0] for r in rows] == [1, 2, 3, 4] and all(r[1] in (0, 1) for r in rows)
    real = F.groups([r for r in today if r[0] < t0 + 300], "5m")[0][1]
    assert abs(__import__("json").loads(rows[0][5])["close"] - real[-1][4]) < 1e-9
    st = cast.live_stats("5m", sym="NIFTY")
    assert [s["k"] for s in st] == [1, 2, 3, 4] and all(s["n"] == 1 for s in st)


def test_training_starts_once_in_the_background(tmp_path, monkeypatch):
    monkeypatch.setattr(CC, "cfg", lambda: SMALL)
    _write_history(tmp_path, "NIFTY", _minutes(2, seed=1))
    calls = []
    done = threading.Event()
    from lab import proc

    def fake(job, data_dir, log=None, **kw):
        calls.append((job, str(data_dir)))
        done.set()
        return {"trained": []}
    monkeypatch.setattr(proc, "run", fake)
    cast = CC.Cast(_engine(tmp_path))
    cast.ensure()
    assert done.wait(5) and calls == [("candlecast", str(tmp_path))]
    for _ in range(50):
        if not cast.status["training"]:
            break
        threading.Event().wait(0.05)
    assert not cast.status["training"] and CC._TRAIN.acquire(blocking=False)
    CC._TRAIN.release()
    cast.ensure()                                                # tried a moment ago: not again for hours
    assert len(calls) == 1
