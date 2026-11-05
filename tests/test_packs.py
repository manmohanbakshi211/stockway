"""2.43 strategy files: a .json of strategies (data only) checked against the app's fixed vocabulary, kept per upload,
queued in every Lab run, run on the test bench, never on live paper by itself. Plus the 9:20 gun pieces: first-candle
facts, daily structure, heavyweights, a first-candle stop and the momentum exit."""
import datetime as dt
import json

import numpy as np
import pytest

from lab import exits as X, factory as F, packs as P
from live import htf


def _file(**kw):
    d = {"stockway_pack": 1, "id": "ghanshyam_920", "name": "9:20 setup", "version": 1, "source": "test",
         "exits": {"fc_mom": {"stop": ["fc"], "momentum": "close", "max": 375},
                   "fc_2r": {"stop": ["fc"], "target_r": 2}},
         "strategies": [
             {"name": "920-A put", "dir": -1, "conds": [["fc_pos", "<", -0.5], ["d_struct3", "<", -0.5]], "win": [10, 75],
              "exit": ["fc_mom", "fc_2r", "std"], "syms": ["BANKNIFTY"]},
             {"name": "920-D heavyweights", "dir": 1, "conds": [["fc_pos", ">", 0.5], ["hdfc_flow", ">", 0]], "win": [10, 75],
              "exit": "fc_mom", "syms": ["BANKNIFTY"]}],
         "cannot_test": [{"idea": "half size against the trend", "needs": "money rules"}]}
    d.update(kw)
    return json.dumps(d).encode()


def test_check_reads_a_file_into_lab_rules_and_lists_what_it_cannot_test():
    r = P.check(_file(extra_field=1))
    assert r["fatal"] is None and len(r["rules"]) == 3
    assert {x["exit"] for x in r["rules"]} >= {"std"} and all(x["source"].startswith("pack:ghanshyam_920:") for x in r["rules"])
    a = [x for x in r["rules"] if x["name"] == "920-A put"]
    assert all(x["win"][0] == 10 and x.get("early") and x["syms"] == ["BANKNIFTY"] for x in a)       # 09:25 allowed for files
    keys = [k for k in r["exits"]]
    assert len(keys) == 2 and all(k.startswith("x") for k in keys)
    assert X.spec_key(r["exits"][keys[0]]) == keys[0]                                                 # same exit, same key
    st = {s["name"]: s for s in r["strategies"]}
    assert st["920-D heavyweights"]["status"] == "cannot" and "hdfc_flow" in st["920-D heavyweights"]["why"]
    assert any("half size" in c["idea"] for c in r["cannot_test"]) and any("extra_field" in p for p in r["problems"])


@pytest.mark.parametrize("raw,why", [(b"{not json", "JSON"), (b'{"id": "x"}', "stockway_pack"),
                                     (json.dumps({"stockway_pack": 1, "id": "Bad Id!"}).encode(), "id must"),
                                     (b" " * (P.MAX_BYTES + 1), "too large")])
def test_bad_files_are_refused_with_a_reason(raw, why):
    assert why in (P.check(raw)["fatal"] or "")


def test_a_file_is_data_only_and_exits_use_a_fixed_vocabulary():
    r = P.check(_file(exits={"evil": {"stop": ["fc"], "code": "__import__('os').system('x')"},
                             "odd": {"stop": ["moon"]}, "ok": {"stop": ["pct", 0.3], "target_r": 3}},
                      strategies=[{"name": "s", "dir": 1, "conds": [["rsi", "<", 30]], "win": [15, 200], "exit": ["evil", "ok"]}]))
    assert any("unknown exit fields" in p for p in r["problems"]) and any("stop must be" in p for p in r["problems"])
    assert len(r["rules"]) == 1 and r["rules"][0]["exit"].startswith("x")
    assert not any("os" in json.dumps(v) and "system" in json.dumps(v) for v in r["exits"].values())


def test_store_keeps_every_upload_switches_and_feeds_the_lab_queue(tmp_path):
    out = P.store(tmp_path, _file(), "920.json")
    assert out["rules"] == 3 and not out["already"]
    assert P.store(tmp_path, _file(), "920.json")["already"]                         # same file again: nothing new
    P.store(tmp_path, _file(version=2, name="9:20 setup v2"), "920b.json")
    idx = P.load_index(tmp_path)
    assert len(idx["packs"]["ghanshyam_920"]["versions"]) == 2 and len(list((tmp_path / "packs" / "files").iterdir())) == 2
    assert len(idx["exits"]) == 2 and P.activate(tmp_path) == 2 and all(k in X.PACK_STYLES for k in idx["exits"])
    q = P.queue(tmp_path)
    assert q["queued"] == 3 and all(i["source"].startswith("pack:") for i in F.load(tmp_path)["ideas"])
    assert P.queue(tmp_path)["already_queued"] == 3 or P.queue(tmp_path)["queued"] == 0
    P.set_on(tmp_path, "ghanshyam_920", False)
    assert P.rules(tmp_path) == [] and len(P.rules(tmp_path, only_on=False)) == 3
    v = P.view(tmp_path)
    assert v["packs"][0]["on"] is False and v["packs"][0]["strategies"][0]["labs"]["1y"]["tested"] == 0
    assert P.root(tmp_path / "lab5y") == P.root(tmp_path)                            # the 5-year Lab reads the same files


def test_file_rules_never_go_live_by_themselves():
    r = {"stage": "promoted", "evidence": "strong", "source": "pack:x:y"}
    assert not F.is_live(r) and F.is_live({**r, "source": "library:z"})
    v = F.validate({"dir": 1, "conds": [["rsi", "<", 30]], "win": [10, 75], "exit": "std", "source": "library:z", "early": True})
    assert v["win"][0] == F.FIRST_ENTRY_TOD and "early" not in v                     # only files may ask for 09:25


def test_set_live_refuses_file_rules(tmp_path):
    st = F.load(tmp_path)
    st["rules"] = {"S1": {"id": "S1", "source": "pack:x:y", "stage": "promoted"}}
    F.save(tmp_path, st)
    with pytest.raises(PermissionError):
        F.set_live(tmp_path, "S1", True)


def test_first_candle_facts_and_daily_structure():
    o = np.array([100, 101, 99, 96, 95.0])
    h = np.array([102, 103, 100, 97, 96.0])
    l = np.array([99, 100, 97, 94, 93.0])
    c = np.array([101, 102.5, 100, 95, 94.0])
    f = F.first_candle_facts(o, h, l, c, prev_high=101.5, prev_low=98.0)
    assert f["fc_color"][0] == 1 and abs(f["fc_range"][0] - 3.0) < 1e-9 and f["fc_pd"][0] == 1     # took out yesterday's high
    assert np.isnan(f["fc_run"][0]) and list(f["fc_run"][1:]) == [1, 0, -1, -2]
    assert htf.struct([10, 9, 8], [5, 4, 3], 3) == -1 and htf.struct([10, 11, 12], [5, 6, 7], 3) == 1
    assert htf.struct([10, 11], [5, 4], 2) == 0 and htf.struct([10], [5], 2) is None


def test_heavyweights_weighted_direction():
    hw = {"weights": {"HDFCBANK": 0.28, "ICICIBANK": 0.25, "AXISBANK": 0.09},
          "stocks": {"HDFCBANK": (100, 99, [101, 99.5]), "ICICIBANK": (50, 49, [51, 49.5]), "AXISBANK": (20, 21, [21, 19])}}
    a, b = F.hw_series(hw, 2)
    assert a[0] == 1.0 and a[1] == -1.0                              # all up since the open, then all down
    assert abs(b[0] - (0.28 + 0.25 + 0.0) / 0.62) < 1e-9 and abs(b[1] - (0.28 + 0.25 - 0.09) / 0.62) < 1e-9
    assert np.isnan(F.hw_series({"weights": {"HDFCBANK": 0.28, "ICICIBANK": 0.25}, "stocks": {"AXISBANK": (1, 1, [1])}}, 1)[0][0])
    from lab import data as D
    w = D.heavyweights("BANKNIFTY", dt.date(2026, 11, 5))
    assert set(w) == {"HDFCBANK", "ICICIBANK", "AXISBANK"} and D.heavyweights("NIFTY") == {}


def test_first_candle_stop_and_momentum_exit():
    sp, _ = X.spec_check({"stop": ["fc"], "momentum": "close"})
    k = X.spec_key(sp)
    X.PACK_STYLES[k] = sp
    try:
        assert X.risk_pts(k, 100.0, -1, float("nan"), fc_lo=98.0, fc_hi=103.0) == 3.0     # a put: stop at the candle high
        assert np.isnan(X.risk_pts(k, 104.0, -1, 1.0, fc_lo=98.0, fc_hi=103.0))          # already beyond: no trade
        o = np.array([101, 100, 99, 98, 98.5, 97.0])
        h = np.array([103, 100.5, 99.5, 98.6, 99.2, 97.5])
        l = np.array([98, 99, 98, 97.5, 98.0, 96.0])
        c = np.array([100, 99.2, 98.2, 98.4, 99.0, 96.5])
        # put entered after bar 1 (m0 = 10): bar 4 closes 99.0 above bar 3's high 98.6 -> out at minute 25
        mk = X.momentum_k(o, h, l, c, 1, -1, "close", 10, 360)
        assert mk == 25 - 11
        spot = np.full(40, 99.0)
        opt = np.full(40, 50.0)
        kk, why = X.first_exit(k, spot, opt, 99.2, 50.0, -1, 3.8, mom_k=mk)
        assert (kk, why) == (14, "momentum")
        assert X.momentum_k(o, h, l, c, 1, -1, None, 10, 360) is None
        assert "first 5-min candle" in sp["words"] and "closes beyond" in sp["words"]
    finally:
        X.PACK_STYLES.pop(k, None)


def test_built_in_styles_unchanged():
    for name in X.STYLES:
        assert X.spec_of(name) is X.STYLES[name]
    assert X.risk_pts("atr2r", 100.0, 1, 2.0) == 2.0 and X.risk_pts("bar2r", 100.0, 1, 2.0, 99.0, 101.0) == pytest.approx(1.2)


def test_a_file_runs_through_the_lab_and_rescoring_is_identical(tmp_path):
    from lab import synth
    import pandas as pd
    synth.generate(tmp_path, start=dt.date(2025, 1, 1), end=dt.date(2025, 3, 15), seed=5)
    raw = json.dumps({"stockway_pack": 1, "id": "t920", "name": "t", "version": 1,
                      "exits": {"fc_mom": {"stop": ["fc"], "momentum": "close"}},
                      "strategies": [{"name": "A", "dir": -1, "conds": [["fc_pos", "<", -0.5]], "win": [10, 75],
                                      "exit": ["fc_mom", "std"], "syms": ["BANKNIFTY"]}]}).encode()
    assert P.store(tmp_path, raw, "t.json")["rules"] == 2
    st = F.run(tmp_path, n_random=50, n_mutate=10, log=lambda m: None)
    pr = {r["exit"]: r for r in st["rules"].values() if str(r.get("source", "")).startswith("pack:t920:")}
    assert len(pr) == 2 and all(r["early"] and r["win"][0] == 10 and r["train"].get("n") for r in pr.values())
    key = next(k for k in pr if k.startswith("x"))
    df = pd.read_parquet(tmp_path / "lab" / "factory_bars.parquet")
    assert "pnl_" + key in df and ((df["dir"] != 0) & (df["tod"] == 10)).any()
    import importlib.util
    spec = importlib.util.spec_from_file_location("lab_regression", str(__import__("pathlib").Path(__file__).resolve().parents[1] / "tools" / "lab_regression.py"))
    reg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reg)
    assert reg.main(str(tmp_path)) == 0                                  # file rules rescore identically
    v = P.view(tmp_path)["packs"][0]["strategies"][0]["labs"]["1y"]
    assert v["tested"] == 2 and v["stage"] in ("promoted", "failed train", "failed validation", "failed holdout")


def test_file_strategies_run_on_the_test_bench_once_each(tmp_path):
    import types
    from live.chartdata import ChartData
    P.store(tmp_path, _file(), "920.json")
    cd = ChartData.__new__(ChartData)
    cd.e = types.SimpleNamespace(log=lambda *a: None, data_dir=tmp_path)
    rules = cd._bench_rules()
    pk = [r for r in rules if r.get("_kind") == "pack"]
    assert len(pk) == 1 and pk[0]["syms"] == ["BANKNIFTY"] and pk[0]["_name"].startswith("9:20 setup: 920-A put")
    assert pk[0]["_variant"].startswith("P") and pk[0]["win"][0] == 10
    assert not [r for r in cd._bench_rules_stocks() if r.get("_kind") == "pack"]       # only where the file says
    assert F.signal_now(pk[0], {"tod": [5.0, 10.0], "fc_pos": [np.nan, -0.8], "d_struct3": [-1.0, -1.0]}, "BANKNIFTY")
    assert not F.signal_now(pk[0], {"tod": [5.0, 10.0], "fc_pos": [np.nan, -0.8], "d_struct3": [-1.0, -1.0]}, "NIFTY")
    from live import registry
    assert registry.KINDS["pack"]["family"] == "lab" and not registry.KINDS["pack"].get("paper")
