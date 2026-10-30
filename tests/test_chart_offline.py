"""The chart is never blank: before the open a stray pre-open tick must not hide the previous sessions."""
import datetime as dt

import numpy as np
import pandas as pd

from live.clock import IST


def _minutes(path, days=3):
    rows = []
    d0 = dt.date.today() - dt.timedelta(days=days + 2)
    k = 0
    while len({r[0] // 86400 for r in rows}) < days:
        d0 += dt.timedelta(days=1)
        if d0.weekday() >= 5 or d0 >= dt.date.today():
            continue
        t0 = dt.datetime.combine(d0, dt.time(9, 15), IST).timestamp()
        for m in range(375):
            p = 25000 + 20 * np.sin((k + m) / 30)
            rows.append([int(t0 + 60 * m), p, p + 3, p - 3, p + 1, 0])
        k += 375
    path.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).to_parquet(path / "minutes.parquet", index=False)


def test_preopen_tick_does_not_blank_the_chart(tmp_path):
    from radar.config import load_config
    from live.engine import LiveEngine
    _minutes(tmp_path / "history" / "NIFTY")
    e = LiveEngine(load_config, tmp_path)
    now = dt.datetime.now(IST).replace(hour=7, minute=44, second=0, microsecond=0)
    e.metrics["NIFTY"] = {"sym": "NIFTY", "spot": 25010.0, "is_index": True}
    e.charts.today_bars = lambda s: [[now.timestamp(), 25010, 25010, 25010, 25010, 0]]
    p = e.charts.payload("NIFTY", 5, 1)
    assert p and len(p["candles"]) == 75 and p["offline"]["source"] == "saved 1-minute history"
    assert any(l["name"] == "PDH" for l in p["levels"])
    p3 = e.charts.payload("NIFTY", 15, 3)
    assert p3 and len(p3["candles"]) >= 50
