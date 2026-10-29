"""Scenario engine: analog matching on a synthetic library."""
import datetime as dt

import numpy as np
import pandas as pd

from live.analog import N, Library, evaluate, forecast


def _lib(n_days=140, seed=3):
    rng = np.random.default_rng(seed)
    rows, px = [], 20000.0
    d = dt.date(2024, 1, 1)
    k = 0
    while k < n_days:
        d += dt.timedelta(days=1)
        if d.weekday() >= 5:
            continue
        t0 = dt.datetime.combine(d, dt.time(9, 15), dt.timezone(dt.timedelta(hours=5, minutes=30))).timestamp()
        px *= np.exp(rng.normal(0, 0.004))
        path = px * np.exp(np.cumsum(rng.normal(0, 0.0004, N)))
        for m in range(N):
            rows.append((t0 + 60 * m, path[m], path[m] * 1.0002, path[m] * 0.9998, path[m], 0))
        px = path[-1]
        k += 1
    return Library("NIFTY", pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]))


def test_forecast_shapes_and_probabilities():
    lib = _lib()
    i = len(lib.days) - 1
    f = forecast(lib, lib.C[i], 60, lib.days[i], lib.prev_close[i], lib.sigma[i])
    assert f and f["k"] == 40
    for h in ("5", "15", "60"):
        x = f["horizons"][h]
        assert abs(x["up"] + x["side"] + x["down"] - 1) < 1e-6
        assert x["lo_pts"] <= x["median_pts"] <= x["hi_pts"]
    assert set(f["paths"]) <= {"up", "side", "down"} and all(len(p["path"]) == 60 for p in f["paths"].values())
    assert all(s["date"] < lib.days[i].isoformat() for s in f["similar"])        # never matches today or later


def test_no_skill_on_random_walk():
    ev = evaluate(_lib(), last_n=40, checkpoints=range(30, 301, 90))
    h = ev["horizons"]["15"]
    assert h["n"] > 50 and h["skill_pct"] < 10            # a random walk can't be predicted: the test must say so
