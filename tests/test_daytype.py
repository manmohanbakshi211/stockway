"""2.66 day-type forecaster: trend / range / quiet day from the 09:45 facts, walk-forward by month, champion vs
challenger every night, live forecast at 09:45 graded at the close. Mandeep, 11 Nov 2026."""
import datetime as dt
import json
import types

import numpy as np
import pandas as pd

from lab import daytype as D


def _bars(n_months=26, seed=5):
    """Synthetic Lab bar table rows (only the 09:45 and 14:45 bars matter): the 09:45 range and how cleanly it moved
    carry real information about the day, everything else is noise or missing."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2024-01-01", periods=n_months * 21)
    rows = []
    for d in days:
        for sym in ("NIFTY", "BANKNIFTY"):
            rng_end = float(np.exp(rng.normal(0, 0.35)))
            eff_end = float(rng.uniform(0, 1))
            side = 1 if rng.random() < 0.5 else -1
            gap = float(rng.normal(0, 0.3))
            move_end = side * eff_end * rng_end
            rng_now = rng_end * float(rng.uniform(0.3, 0.6))
            move_now = move_end * float(rng.uniform(0.2, 0.5)) + float(rng.normal(0, 0.03))
            for tod, (r, mv) in ((30.0, (rng_now, move_now)), (330.0, (rng_end, move_end))):
                for direction in (1, -1):                  # the same index facts on the call and put rows
                    rows.append({"sym": sym, "date": d.date(), "tod": tod, "dir": direction, "gap": gap,
                                 "day_ret": gap + mv, "day_range": r, "vix": float(rng.normal(14, 2)), "dte": float(rng.integers(0, 6)),
                                 "or_pos": float(rng.normal()), "range_x": float(rng.normal(1, 0.2)), "pdh_dist": float(rng.normal()),
                                 "pdl_dist": float(rng.normal()), "cpr_width": np.nan, "adx": float(rng.normal(20, 5))})
    return pd.DataFrame(rows)


def test_labels():
    c = D.cfg()
    assert D.label(0, 1.0, 1.2, 1.0, c) == "trend"          # clean 1% move, range 1.2x a usual day
    assert D.label(0, 0.1, 1.2, 1.0, c) == "range"          # wide but went nowhere
    assert D.label(0, 0.5, 0.5, 1.0, c) == "quiet"          # half a usual day's range
    assert D.label(0, 0.5, 0.5, None, c) is None            # no typical day yet: no label


def test_table_one_row_per_day_no_lookahead():
    c = D.cfg()
    df = _bars(3)
    t, cols = D.table(df, c)
    assert not t.duplicated(["date", "sym"]).any()
    one = t[t["sym"] == "NIFTY"].reset_index(drop=True)
    full = df[(df["sym"] == "NIFTY") & (df["tod"] == 330) & (df["dir"] == 1)].sort_values("date")["day_range"].to_numpy()
    i = 15                                                  # the typical day for row i uses only the days before it
    day_i = list(df[(df["sym"] == "NIFTY") & (df["tod"] == 330) & (df["dir"] == 1)].sort_values("date")["date"]).index(one["date"][i])
    assert abs(one["med20"][i] - np.median(full[max(0, day_i - 20):day_i])) < 1e-9
    assert {"rel_range", "eff_now", "is_bank"} <= set(cols)


def test_learn_walk_forward_champion_and_predict(tmp_path):
    df = _bars()
    r = D.learn(tmp_path, df=df, log=lambda m: None)
    assert r["walk"]["tested_days"] > 200 and r["champion"] in D.cfg()["models"]
    assert "cpr_width" not in r["facts"]                    # a fact this history lacks is left out
    assert r["walk"]["logloss_skill"] > 0                   # the 09:45 range carries the answer in this data
    assert r["walk"]["quiet_top20"] > r["walk"]["quiet_all"]
    assert (tmp_path / D.MODEL).exists() and json.loads((tmp_path / D.OUT).read_text())["champion"] == r["champion"]
    p = D.predict([tmp_path], "NIFTY", {"gap": 0.1, "day_ret": 0.1, "day_range": 0.2, "vix": 14})
    assert abs(sum(p["probs"].values()) - 100) < 0.5 and p["probs"]["quiet"] > p["usual"]["quiet"]
    assert D.predict([tmp_path], "FINNIFTY", {"gap": 0}) is None
    assert D.predict([tmp_path / "nothing"], "NIFTY", {"gap": 0}) is None


def test_a_broken_model_is_skipped(tmp_path):
    c = dict(D.cfg(), models={"nonsense": {}, "logit": {"C": 0.3}})
    r = D.learn(tmp_path, df=_bars(), c=c, log=lambda m: None)
    assert r["champion"] == "logit" and any(x.get("error") for x in r["runs"] if x["model"] == "nonsense")


def test_reads_only_the_cached_table_never_builds(tmp_path):
    assert "note" in D.learn(tmp_path, log=lambda m: None)                 # no table: a note, nothing built
    from lab.factory import cache_path
    cache_path(tmp_path).parent.mkdir(parents=True)
    _bars().to_parquet(cache_path(tmp_path))
    assert D.learn(tmp_path, log=lambda m: None)["walk"]["tested_days"] > 0
    cache_path(tmp_path).unlink()                                          # table gone for a night: the result stays
    assert "note" in D.learn(tmp_path, log=lambda m: None)
    assert json.loads((tmp_path / D.OUT).read_text())["walk"]["tested_days"] > 0


def test_live_forecast_at_0945_and_graded_at_close(tmp_path):
    D.learn(tmp_path, df=_bars(), log=lambda m: None)
    from live.chartdata import ChartData
    logs = []
    fake = types.SimpleNamespace(e=types.SimpleNamespace(data_dir=tmp_path, log=logs.append))
    now = dt.datetime(2026, 11, 12, 9, 50)
    n = 6
    f = {"tod": (np.arange(n) + 1) * 5.0, "gap": np.full(n, 0.1), "day_ret": np.linspace(0.1, 0.15, n),
         "day_range": np.linspace(0.05, 0.2, n), "vix": np.full(n, 14.0)}
    ChartData._daytype(fake, "NIFTY", now, 5, f)                           # 09:40: too early
    assert not D.live_load(tmp_path)
    ChartData._daytype(fake, "NIFTY", now, 6, f)                           # 09:45 bar closed: the forecast
    rows = D.live_load(tmp_path)
    assert len(rows) == 1 and rows[0]["sym"] == "NIFTY" and not rows[0]["late"] and "on trial" in logs[-1]
    ChartData._daytype(fake, "NIFTY", now, 7, f)                           # once a day
    assert len(D.live_load(tmp_path)) == 1
    n = 66
    f2 = {"tod": (np.arange(n) + 1) * 5.0, "gap": np.full(n, 0.1), "day_ret": np.full(n, 0.12), "day_range": np.full(n, 0.2)}
    ChartData._daytype(fake, "NIFTY", now.replace(hour=14, minute=50), 66, f2)
    row = D.live_load(tmp_path)[0]
    assert row["label"] == "quiet" and row["right"] in (True, False)
    v = D.live_view(tmp_path)
    assert v["graded"] == 1 and v["right_pct"] in (0.0, 100.0)
    td = D.today(tmp_path, "2026-11-12")
    assert td["rows"][0]["trusted"] is False                               # needs 20+ live days first


def test_on_the_system_page():
    from live.selfcheck import static_checks
    c = next(x for x in static_checks() if x["id"] == "daytype")
    assert c["ok"] is True


def test_app_data_stays_out_of_git():
    """2.66: models, Lab results, AI reports and the day-type record never get uploaded (only the template is tracked)."""
    from pathlib import Path
    ign = (Path(__file__).resolve().parents[1] / ".gitignore").read_text().split()
    assert "data/*" in ign and ign.index("!data/events_template.csv", ign.index("data/*")) > ign.index("data/*")
