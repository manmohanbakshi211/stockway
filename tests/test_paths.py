"""2.45 trade journeys: every bench signal's option held to 15:15 from the day's recording; peaks, givebacks, bottoms,
recoveries and the market at each moment; fixed exits chosen on older days and judged on newer ones."""
import datetime as dt
import json

import numpy as np
import pandas as pd

from live import paths as P

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _t(day, hh, mm):
    return dt.datetime.combine(dt.date.fromisoformat(day), dt.time(hh, mm), IST).timestamp()


def test_journey_peak_giveback_bottom_recovery():
    t0 = 1000.0
    ts = t0 + 60 * np.arange(0, 121)
    up = np.r_[np.linspace(100, 130, 21), np.linspace(130, 105, 100)]          # +30% at 20 min, then gives back
    j = P.journey(ts, up, 100.0, t0, ctx_at=lambda t: {"und": 0.1})
    assert j["mfe_pct"] == 30 and j["mfe_min"] == 20 and j["t_p20"] is not None and j["first"] == "+20"
    assert abs(j["giveback"] - 25) < 0.01 and j["end_pct"] == 5 and j["pct60"] is not None
    ev = {e["what"]: e for e in j["events"]}
    assert ev["peak"]["min"] == 20 and ev["giveback"]["min"] > 20 and ev["peak"]["ctx"] == {"und": 0.1}
    down = np.r_[np.linspace(100, 75, 31), np.linspace(75, 102, 90)]           # -25% at 30 min, then back above entry
    k = P.journey(ts, down, 100.0, t0)
    assert k["mae_pct"] == -25 and k["first"] == "-20" and k["recovered"] == 1
    assert {"loss_start", "bottom", "recover"} <= {e["what"] for e in k["events"]}
    assert P.journey(ts[:2], up[:2], 100.0, t0) is None


def _bench(tmp_path, day, n=6):
    from live.bench import BenchDB
    b = BenchDB(tmp_path / "bench.db")
    t0 = _t(day, 10, 0)
    for i in range(n):
        sig = f"s{day}{i}"
        b.db.execute("INSERT INTO bench_signals(sig, source, ts, day, sym, kind, variant, direction) VALUES (?,?,?,?,?,?,?,?)",
                     (sig, "upstox", t0, day, "NIFTY", "orb", None, "up"))
        b.db.execute("INSERT INTO bench_trades(ts, day, source, sym, kind, variant, name, style, direction, key1, entry, sig) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (t0, day, "upstox", "NIFTY", "orb", None, "ORB", "buy", "up", "OPT1", 100.0, sig))
    b.db.commit()
    return t0


def _recording(tmp_path, day, rise=True):
    rec = tmp_path / "recordings"
    rec.mkdir(exist_ok=True)
    ts = np.arange(_t(day, 9, 15), _t(day, 15, 30), 60)
    k = np.arange(len(ts))
    opt = 100 + (np.minimum(k - 45, 40) if rise else -np.minimum(k - 45, 30)).clip(-90, None) * (k >= 45)
    minutes = pd.DataFrame({"ts": ts.astype(int), "key": "OPT1", "ltp": opt, "vol": 0.0, "oi": 1000.0 + k, "iv": 0.2,
                            "bid": opt - 0.5, "ask": opt + 0.5, "tbq": 0.0, "tsq": 0.0, "cvd": 0.0})
    other = minutes.assign(key="OTHER")
    pd.concat([minutes, other]).to_parquet(rec / f"upstox_{day}_minutes.parquet", index=False)
    met = pd.DataFrame({"ts": ts.astype(int), "sym": "NIFTY",
                        "data": [json.dumps({"spot": 25000 + int(i), "vs_vwap_pct": 0.1, "ret_5m": 0.05}) for i in k]})
    met.to_parquet(rec / f"upstox_{day}_metrics.parquet", index=False)


def test_build_day_from_a_recording_and_the_board_view(tmp_path):
    days = [f"2026-10-{d:02d}" for d in (20, 21, 22, 23, 26, 27, 28)]
    for i, d in enumerate(days):
        _bench(tmp_path, d)
        _recording(tmp_path, d, rise=i % 3 != 0)
    assert P.pending_days(tmp_path)[:2] == days[::-1][:2]
    r = P.run(tmp_path, log=lambda m: None)
    assert [x["n"] for x in r["days"]] == [6] * len(days) and P.pending_days(tmp_path) == []
    c = P.connect(tmp_path, ro=True)
    row = dict(c.execute("SELECT * FROM trade_paths WHERE day=?", (days[1],)).fetchone())
    c.close()
    assert row["mfe_pct"] > 30 and row["t_p20"] is not None and row["first"] == "+20" and row["opt_key"] == "OPT1"
    ev = json.loads(row["events"])
    assert ev and "und" in ev[0]["ctx"] and "oi" in ev[0]["ctx"] and json.loads(row["path"])[0] is not None
    v = P.view(tmp_path, days=3650)
    g = v["groups"][0]
    assert v["ready"] and g["name"] == "ORB" and g["n"] == 42 and g["p20_first"] > 50
    assert g["best_exit"] is None or {"target", "stop", "time", "train_avg", "hold_avg"} <= set(g["best_exit"])
    assert v["at_peak"]["n"] >= 5
    from lab import proc
    assert "paths" in proc.JOBS


def test_missing_recording_is_noted_not_fatal(tmp_path):
    _bench(tmp_path, "2026-10-20")
    out = P.build_day(tmp_path, "2026-10-20", log=lambda m: None)
    assert out["n"] == 0 and "recording" in out["note"]


def test_best_exit_is_chosen_on_older_days_only():
    rows = []
    for i in range(10):
        day = f"2026-10-{10 + i:02d}"
        late = i >= 6
        for _ in range(5):
            rows.append({"day": day, "t_p10": 5.0, "t_p20": None if late else 10.0, "t_p30": None, "t_p50": None,
                         "t_m10": 20.0 if late else None, "t_m20": None, "t_m30": None, "pct30": 1.0, "pct60": 1.0,
                         "pct120": 1.0, "end_pct": -5.0 if late else 2.0})
    b = P.best_exit(rows, min_n=10)
    assert b and b["target"] in (20, 10) and b["train_avg"] >= 10 and b["hold_avg"] <= b["train_avg"]
