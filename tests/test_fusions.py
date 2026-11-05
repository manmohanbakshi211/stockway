"""2.46 fusions: pairs of the best named strategies (ranked on TRAIN days only), tested like any rule and counted in
the Lab's luck number; never live by themselves; and the live confluence fact (how many named strategies agree)."""
import datetime as dt
import types

import numpy as np
import pandas as pd

from lab import factory as F, fusions as FU


def _entries():
    return [{"dir": 1, "conds": [["rsi", "<", 30]], "win": [15, 200], "syms": ["NIFTY"], "syms_all": ["NIFTY"], "name": "A", "source": "library:A"},
            {"dir": 1, "conds": [["vs_twap", ">", 0]], "win": [60, 330], "syms": ["NIFTY", "BANKNIFTY"], "syms_all": ["NIFTY", "BANKNIFTY"], "name": "B", "source": "library:B"},
            {"dir": 1, "conds": [["adx", ">", 25], ["st_10_3", ">", 0], ["rsi", ">", 50], ["dte", "<", 3]], "win": [15, 330], "syms": ["NIFTY"], "syms_all": ["NIFTY"], "name": "C", "source": "library:C"},
            {"dir": -1, "conds": [["rsi", ">", 70]], "win": [15, 330], "syms": ["NIFTY"], "syms_all": ["NIFTY"], "name": "D", "source": "library:D"}]


def _saved(tmp_path, entries, t_train):
    st = F.load(tmp_path)
    for e, t in zip(entries, t_train):
        for ex in F.BUY_EXITS:
            v = F.validate({**e, "syms": e["syms_all"], "exit": ex, "max_day": 1})
            st["rules"][v["id"]] = {**v, "stage": "failed train", "train": {"n": 60, "t": t}, "valid": {"n": 20, "t": 99.0},
                                    "hold": {"n": 20, "t": 99.0}}
    F.save(tmp_path, st)


def test_fusions_pair_the_best_on_train_only(tmp_path, monkeypatch):
    ents = _entries()
    monkeypatch.setattr(FU, "named_entries", lambda d: ents)
    _saved(tmp_path, ents, [1.5, 0.5, 2.0, 1.0])
    ranked = FU.train_rank(tmp_path, ents)
    assert [e["name"] for _t, _n, e in ranked] == ["C", "A", "D", "B"]
    rules = FU.build(tmp_path, top=3)
    names = {r["source"] for r in rules}
    assert names == {"fusion:A + B"} or names == {"fusion:C + A", "fusion:A + B"} - {"fusion:C + A"} or "fusion:A + B" in names
    ab = [r for r in rules if r["source"] == "fusion:A + B"]
    assert len(ab) == len(FU.EXITS) and ab[0]["win"] == [60, 200] and ab[0]["syms"] == ["NIFTY"] and len(ab[0]["conds"]) == 2
    assert not any("C" in r["source"].split(":")[1].split(" + ") and len(r["conds"]) > 4 for r in rules)   # 5 conds: skipped
    assert all(r["dir"] == 1 for r in rules)                       # D has no partner in its direction
    rules1 = FU.build(tmp_path, top=1)
    assert rules1 == []                                            # the single best can't pair with itself


def test_fusions_count_towards_luck_and_never_go_live_by_themselves(tmp_path, monkeypatch):
    rng = np.random.default_rng(1)
    rows = []
    for k in range(60):
        day = dt.date(2025, 1, 1) + dt.timedelta(days=k)
        for bar in range(2, 20):
            base = {f: float(rng.normal()) for f in F.FEATURES}
            base["tod"] = (bar + 1) * 5.0
            for d in (1, -1):
                rows.append({"sym": "NIFTY", "date": day, "bar": bar, **base, "dir": d, "premium": 8000,
                             **{"pnl_" + r: float(rng.normal(-100, 500)) for r in set(F.EXITS) | set(F.STYLES)}})
    f = F.cache_path(tmp_path)
    f.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(f, index=False)
    fus = [{"dir": 1, "conds": [["rsi", "<", 0], ["vs_twap", ">", 0]], "win": [15, 200], "exit": ex, "max_day": 1, "syms": ["NIFTY"],
            "source": "fusion:A + B", "note": "x"} for ex in ("std", "atr2r")]
    monkeypatch.setattr(FU, "build", lambda d, **k: fus)
    st = F.run(tmp_path, n_random=0, n_mutate=0, log=lambda m: None)
    got = [r for r in st["rules"].values() if r["source"] == "fusion:A + B"]
    assert len(got) == 2 and st["tried_total"] == 2
    st2 = F.run(tmp_path, n_random=0, n_mutate=0, log=lambda m: None)
    assert st2["tried_total"] == 2                                  # already tested: not counted again
    r = {**got[0], "stage": "promoted", "evidence": "strong"}
    assert not F.is_live(r) and F.is_live({**r, "live": "on"})


def test_live_confluence_counts_named_strategies_both_ways():
    from live.engine import LiveEngine
    e = LiveEngine.__new__(LiveEngine)
    t0 = 1_800_000_000.0
    e.note_confluence("NIFTY", 1, "L1", t0)
    e.note_confluence("NIFTY", 1, "L1", t0 + 60)                    # the same setup twice counts once
    e.note_confluence("NIFTY", 1, "P2", t0 + 120)
    e.note_confluence("NIFTY", -1, "L3", t0 + 180)
    e.note_confluence("BANKNIFTY", 1, "L9", t0 + 180)
    now = types.SimpleNamespace(timestamp=lambda: t0 + 200)
    assert e.confluence_facts("NIFTY", "up", now) == {"conf_al": 2, "conf_against": 1}
    assert e.confluence_facts("NIFTY", "down", now) == {"conf_al": 1, "conf_against": 2}
    later = types.SimpleNamespace(timestamp=lambda: t0 + 1000)      # 15 minutes on: the first ones dropped out
    assert e.confluence_facts("NIFTY", "up", later)["conf_al"] == 1
    assert e.confluence_facts("NIFTY", "flat", now) == {}


def test_fusions_stay_off_the_stock_bench():
    from live.chartdata import ChartData
    cd = ChartData.__new__(ChartData)
    cd.e = types.SimpleNamespace(log=lambda *a: None)
    base = [{"_kind": "fusion", "syms": ["NIFTY"], "_variant": "F1", "_name": "x", "dir": 1, "conds": [], "win": [15, 330]},
            {"_kind": "library", "syms": ["NIFTY"], "_variant": "L1", "_name": "y", "dir": 1, "conds": [], "win": [15, 330]}]
    cd._lib_cache = (__import__("time").time(), base)
    assert [r["_variant"] for r in cd._bench_rules_stocks()] == ["L1s"]
