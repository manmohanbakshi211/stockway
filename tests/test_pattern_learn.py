"""2.93 pattern learning (Mandeep, 23 Nov 2026: "patterens to observe karte raho or ye dekhna hai humein ki system
patterens achhe se pakdna seekhe toh do the best"): which candle patterns pay, in which situation; shadow only."""
import datetime as dt
import inspect
import json
import sqlite3
import types
from pathlib import Path

import numpy as np
import pandas as pd

from lab import pattern_learn as PL

ROOT = Path(__file__).resolve().parents[1]
_CFG = PL.cfg


def _lab_table(tmp: Path, days=120, seed=1):
    """A fake Strategy Lab bar table: an engulfing pays only with the daily trend; the price follows it then too."""
    rng = np.random.default_rng(seed)
    rows = []
    for di in range(days):
        day = dt.date(2025, 1, 1) + dt.timedelta(days=di)
        for sym in ("NIFTY", "SBIN", "INFY"):
            for bar in range(3, 70):
                tr = float(rng.choice([-1, 0, 1]))
                base = {"sym": sym, "date": day, "bar": bar, "tod": (bar + 1) * 5, "d_trend": tr if di > 5 else np.nan,
                        "day_ret": rng.normal(), "vs_twap": rng.normal() * .3, "rsi": rng.uniform(20, 80),
                        "pos_in_range": rng.uniform(), "lvl_atr": rng.uniform(0, 3), "lvl_touch": 0.0,
                        "ret_b6": rng.normal() * .3, "range_x": rng.uniform(.3, 3), "adx": rng.uniform(10, 40),
                        "d_pos20": rng.uniform(), "dte": float(rng.integers(0, 10)), "vix": np.nan}
                pat = float(rng.choice([0, 1, -1], p=[.7, .15, .15]))
                for d in (1, -1):
                    good = pat * d > 0 and tr * d > 0
                    y = -0.05 + (0.12 if good else 0) + rng.normal() * 0.3
                    rows.append({**base, "dir": d, "premium": 1000.0, "pnl_std": y * 1000, "cdl_engulf": pat,
                                 "cdl_harami": 0.0})
    (tmp / "lab").mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(tmp / "lab" / "factory_bars.parquet")


def _cfg():
    c = _CFG()
    c["lab"].update(min_pattern_n=200, min_pick_n=50)
    c["bench"].update(min_pattern_n=100, min_pick_n=20, min_days=8)
    return c


def test_history_learning_finds_the_situation_where_a_pattern_pays(tmp_path):
    _lab_table(tmp_path)
    r = PL.learn_lab(tmp_path, _cfg())
    assert not r.get("error") and r["source"] == "lab" and r["days"] == 120
    assert r["hold_pick"]["avg"] > r["hold_all"]["avg"] and r["lift"] > 0.02           # on days it never learned from
    q = [x["avg"] for x in r["quintiles"]]
    assert q[-1] > q[0] and r["ranks"]
    eng = next(p for p in r["patterns"] if p["key"] == "cdl_engulf")
    assert eng["name"] == "Engulfing" and eng["own_model"]
    assert eng["best"][0]["what"] == "with the daily trend"                             # in plain words
    assert any(w["what"] == "against the daily trend" for w in eng["worst"])
    assert r["verdict"]["code"] in ("pays", "learns") and "move" in r


def test_an_unknown_fact_does_not_make_every_known_value_look_good():
    # rows with no daily trend are worse (they come from early days); "with" and "against" must not both look good
    y = np.array([-1.0] * 50 + [0.2] * 50 + [0.0] * 50)
    lab = {"trend": np.array(["na"] * 50 + ["1"] * 50 + ["-1"] * 50, dtype=object)}
    m = PL.fit(y, lab)
    e_with = PL.expected(m, {"trend": np.array(["1"], dtype=object)}, 10)[0]
    e_against = PL.expected(m, {"trend": np.array(["-1"], dtype=object)}, 10)[0]
    assert e_with > m["m0"] and e_against < m["m0"] + 0.15 and e_with > e_against


def test_labels_are_the_same_for_one_occurrence_and_for_many():
    dims = PL.cfg()["bench"]["dims"]
    facts = {"tf": "1H", "early": "0", "dir": -1.0, "is_index": 1, "rel_vol": 2.0, "mins": 45, "htf_d_al": 1,
             "vwap_al": -0.5, "nifty_al": 0.1, "htf_room_atr": 3, "cdl_al_sum": 2, "regime": "range", "htf_pos20": 0.9,
             "dte": 1, "ev_level": 2.0, "against": 0, "ret5_al": 0.5, "iv_hv": None}
    one = PL.labels({k: [v] for k, v in facts.items()}, [-1], dims)
    many = PL.labels({k: [v, v] for k, v in facts.items()}, [-1, -1], dims)
    assert all(one[k][0] == many[k][1] for k in one)
    assert one["vol"][0] == "b3" and one["trend"][0] == "1" and one["d20"][0] == "b0"   # flipped for a bearish pattern
    assert one["events"][0] == "2" and one["ivhv"][0] == "na" and one["tf"][0] == "1H"
    sp = {d["name"]: d for d in dims}
    assert PL.words(sp["vol"], "b3") == "volume 1.5-2.5x normal"
    rng = np.random.default_rng(3)                     # the live fast path (one pattern) == the learning path (many)
    m = PL.fit(rng.normal(size=400), PL.labels({"rel_vol": rng.uniform(0, 4, 400), "htf_d_al": rng.integers(-1, 2, 400)},
                                               np.ones(400), dims))
    for f in ({"rel_vol": 3.1, "htf_d_al": 1}, {"rel_vol": None, "htf_d_al": float("nan")}, {"rel_vol": "x"}):
        one = PL.labels({k: [v] for k, v in f.items()}, [1], dims)
        two = PL.labels({k: [v, v] for k, v in f.items()}, [1, 1], dims)
        assert {k: v[0] for k, v in one.items()} == {k: v[0] for k, v in two.items()}
        assert abs(PL.expected(m, one, 50)[0] - PL.expected(m, two, 50)[0]) < 1e-12


def _bench_db(path: Path, days=12, seed=2, after: str | None = None, tag_q=False):
    rng = np.random.default_rng(seed)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS bench_signals (sig TEXT, source TEXT, ts REAL, day TEXT, sym TEXT, kind TEXT, "
                "variant TEXT, direction TEXT, alert_id INT, ctx TEXT, tag TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS bench_trades (sig TEXT, source TEXT, day TEXT, kind TEXT, style TEXT, "
                "r_mult REAL, is_index INT, exit_ts REAL)")
    start = dt.date.fromisoformat(after) + dt.timedelta(days=1) if after else dt.date(2026, 11, 2)
    for di in range(days):
        day = (start + dt.timedelta(days=di)).isoformat()
        for i in range(60):
            kind = "candle_engulfing" if i % 2 else "candle_harami"
            vol = float(rng.uniform(0.3, 4))
            idx = int(rng.random() < 0.2)
            r = -0.2 + (0.35 if vol > 2.5 else 0) + rng.normal() * 0.3
            sig = f"{day}|{i}"
            ctx = {"rel_vol": vol, "is_index": idx, "mins": int(rng.integers(5, 360)), "htf_d_al": int(rng.integers(-1, 2))}
            tag = {"pq": 90 if vol > 2.5 else 20} if tag_q else None
            for src in ("upstox", "upstox#coin", "sim"):
                con.execute("INSERT INTO bench_signals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (sig if src == "upstox" else sig + src, src, 0, day, "SBIN", kind, "15m" if i % 3 else "1H@70%",
                             "up" if i % 4 else "down", None, json.dumps(ctx), json.dumps(tag) if tag else None))
                for style in ("buy", "sell"):
                    con.execute("INSERT INTO bench_trades VALUES (?,?,?,?,?,?,?,?)",
                                (sig if src == "upstox" else sig + src, src, day, kind, style, r if style == "buy" else -r, idx, 1.0))
    con.commit()
    con.close()


def test_bench_learning_scores_live_patterns_and_logs_each_run(tmp_path, monkeypatch):
    (tmp_path / "lab").mkdir()
    _bench_db(tmp_path / "bench.db")
    monkeypatch.setattr(PL, "cfg", _cfg)
    res = PL.run(tmp_path, log=lambda m: None)
    out = json.loads((tmp_path / "lab" / PL.OUT).read_text())
    b = out["bench"]
    assert b["source"] == "bench" and b["days"] == 12 and b["n"] == 12 * 60          # coin flips and practice left out
    assert b["hold_pick"]["avg"] > b["hold_all"]["avg"]
    assert "error" in out["lab"]                                                         # no Lab table here: said, not crashed
    assert res["bench"]["code"] in ("pays", "learns")
    runs = (tmp_path / "lab" / PL.RUNS).read_text().splitlines()
    assert len(runs) == 1 and json.loads(runs[0])["src"] == "bench"
    live = PL.Live(tmp_path)
    hi = live.score("candle_engulfing", "15m", "up", {"rel_vol": 3.5, "is_index": 0}, why=True)
    lo = live.score("candle_engulfing", "15m", "up", {"rel_vol": 0.4, "is_index": 0})
    assert hi["q"] > lo["q"] and hi["exp"] > lo["exp"] and hi["pick"] and not lo["pick"]
    assert any("volume 2.5x+" in w for w, _ in hi["up"])
    assert live.score("orb", None, "up", {}) is None and live.score("candle_engulfing", "15m", "flat", {}) is None


def test_forward_check_judges_only_days_after_the_model(tmp_path):
    _bench_db(tmp_path / "bench.db", days=3, after="2026-11-20", tag_q=True)
    _bench_db(tmp_path / "bench.db", days=2, seed=5, tag_q=True)                        # older days: not counted
    f = PL.forward(tmp_path / "bench.db", "2026-11-20", 70)
    assert f["days"] == 3 and f["n"] == 180 and f["since"] == "2026-11-20"
    assert f["picked"]["avg"] > f["all"]["avg"] and f["lift"] > 0
    assert PL.forward(tmp_path / "bench.db", None, 70)["n"] == 0


def test_the_test_bench_records_the_quality_of_each_candle_pattern(tmp_path):
    from live import bench as B
    from live.clock import IST
    from live.store import Store
    from tests.test_bench import CFG, OPT, I, St, _engine
    e = _engine(St(CE800=I(99, 100), CE850=I(74, 75), CE900=I(54, 55)))
    seen = []
    e.pattern_quality = lambda a, variant, ctx: seen.append((a["kind"], variant)) or {"q": 81, "exp": -0.05}
    b = B.Bench(Store(tmp_path / "live.db"), "upstox")
    now = dt.datetime(2026, 11, 4, 10, 0, tzinfo=IST)
    assert b.add({"sym": "NIFTY", "kind": "candle_engulfing", "direction": "up", "option": OPT}, e, now, CFG, variant="15m") > 0
    assert b.add({"sym": "NIFTY", "kind": "orb", "direction": "up", "option": OPT}, e, now, CFG) > 0
    with b.bdb.lock:
        tags = {r[0]: r[1] for r in b.bdb.db.execute("SELECT kind, tag FROM bench_signals").fetchall()}
    assert json.loads(tags["candle_engulfing"]) == {"pq": 81, "pexp": -0.05}
    assert not tags["orb"] or "pq" not in json.loads(tags["orb"])                     # only candle patterns are scored
    assert seen == [("candle_engulfing", "15m")]
    e.pattern_quality = lambda *a: 1 / 0                                                # a failing learner never stops the bench
    now2 = now + dt.timedelta(minutes=30)
    assert b.add({"sym": "NIFTY", "kind": "candle_harami", "direction": "up", "option": OPT}, e, now2, CFG, variant="15m") > 0


def test_wired_in_nightly_lab_api_page_and_self_check():
    from lab import proc
    from live import engine, selfcheck
    assert "patlearn" in proc.JOBS and "pattern_learn.run" in inspect.getsource(proc._inproc)
    src = inspect.getsource(engine.LiveEngine._nightly_lab)
    assert 'proc.run("patlearn", self.data_dir' in src and 'proc.run("patlearn", r5' in src
    assert "def pattern_quality" in inspect.getsource(engine.LiveEngine)
    srv = (ROOT / "webapp" / "server.py").read_text()
    assert '@app.get("/api/lab/patlearn")' in srv and '@app.post("/api/lab/patlearn/run")' in srv
    js = (ROOT / "webapp" / "static" / "live.js").read_text()
    assert "async function labPatterns()" in js and 'id="lab-pat"' in js and "labPatterns();" in js
    assert '"pattern_learn"' in inspect.getsource(selfcheck)
    c = json.loads((ROOT / "rules" / "pattern_learn.json").read_text())
    assert c["valid_from"] == "2026-11-23" and c["bench"]["style"] == "buy" and len(c["bench"]["dims"]) >= 12


def test_market_data_only():
    src = inspect.getsource(PL).lower()
    for bad in ("place_order", "modify_order", "cancel_order", "/orders", "positions", "holdings", "funds"):
        assert bad not in src
