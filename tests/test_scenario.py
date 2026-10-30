"""Days like today (lab/scenario.py) on small synthetic data: no network."""
import datetime as dt
import json

import numpy as np
import pandas as pd

from lab.bhav import eod_dir
from lab.scenario import next_entry, run, stale, verdict


def _setup(tmp_path):
    rng = np.random.default_rng(1)
    days = pd.bdate_range("2021-01-01", "2023-06-30").date
    ed = eod_dir(tmp_path)
    ed.mkdir(parents=True, exist_ok=True)
    close = 17000 * np.exp(np.cumsum(rng.normal(0, 0.01, len(days))))
    pd.DataFrame({"date": days, "open": close, "high": close, "low": close, "close": close}).to_parquet(ed / "spot_NIFTY.parquet")
    vix = pd.DataFrame({"date": days, "close": 15 + rng.normal(0, 2, len(days))})
    vix.to_parquet(ed / "vix_yahoo.parquet")          # fresh cache: no Yahoo call
    last = days[-1]
    exp = last + dt.timedelta(days=4)
    S = float(close[-1])
    k = round(S / 50) * 50
    rows = []
    for strike in range(k - 500, k + 550, 50):
        for side in ("CE", "PE"):
            px = max(5.0, (S - strike if side == "CE" else strike - S)) + 60
            rows.append({"date": last, "sym": "NIFTY", "kind": "OPT", "expiry": exp, "strike": float(strike), "side": side,
                         "open": px, "high": px, "low": px, "close": px, "settle": px, "oi": 1, "chg_oi": 0, "volume": 10, "underlying": S})
    pd.DataFrame(rows).to_parquet(ed / f"fo_{last.year}.parquet")
    tr = []
    for i, d in enumerate(days[25:-5:5]):
        e = d + dt.timedelta(days=5)
        for short in (1.0, 1.5):
            pnl = 800.0 if i % 3 else -900.0
            tr.append({"sym": "NIFTY", "family": "iron_condor", "pid": f"iron_condor|entry=2,flt=none,short={short},wing=2.0",
                       "date": d, "expiry": e, "year": d.year, "pnl": pnl + short, "implied_move": 1.0 + rng.normal(0, 0.1), "real_move": 0.8})
    lab = tmp_path / "lab"
    lab.mkdir()
    pd.DataFrame(tr).to_parquet(lab / "eod_trades.parquet")
    (lab / "eod_results.json").write_text(json.dumps({"results": {"NIFTY|iron_condor": {"sym": "NIFTY", "family": "iron_condor",
        "years": [{"year": 2023, "pid": "iron_condor|entry=2,flt=none,short=1.0,wing=2.0"}]}}}))
    return last, exp


def test_scenario_runs_and_ranks(tmp_path):
    last, exp = _setup(tmp_path)
    assert stale(tmp_path)
    r = run(tmp_path, log=lambda m: None)
    t = r["today"]["NIFTY"]
    assert t["date"] == str(last) and t["next_expiry"] == str(exp) and t["implied_move"] > 0 and t["conditions"]
    row = r["strategies"][0]
    assert row["entry"] == "2" and row["near"]["n"] > 0 and row["all"]["n"] >= row["near"]["n"]
    assert row["strict"] and row["strict"]["all"]["n"] > 0              # only 2023 was a strict pick
    assert (tmp_path / "lab" / "scenario.json").exists() and not stale(tmp_path)


def test_verdict_and_next_entry():
    assert verdict({"n": 10, "avg": 900, "t": 3}, {"avg": 1})[0] == "few"
    assert verdict({"n": 60, "avg": 500, "t": 2.0}, {"avg": 100})[0] == "good"
    assert verdict({"n": 60, "avg": -500, "t": -2.0}, {"avg": 100})[0] == "bad"
    st = {"date": "2026-10-29", "next_expiry": "2026-11-23", "prev_expiry": "2026-10-27"}
    assert next_entry("1", st) == "Fri 20 Nov close"
    assert next_entry("after_expiry", st) == "after the 23 Nov expiry"
    st2 = {"date": "2026-10-30", "next_expiry": "2026-11-03", "prev_expiry": "2026-10-27"}
    assert next_entry("2", st2) == "today's close"
