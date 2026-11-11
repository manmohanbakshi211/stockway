"""Multi-timeframe charts: bars for every timeframe from 1-minute history (no Dhan), markings per timeframe,
higher-timeframe levels, confluence, trend strip, and strategy overlays with stop / target outcomes."""
import datetime as dt
import math

import numpy as np

from live.clock import IST


def minutes(n_days=70, seed=3):
    rng = np.random.default_rng(seed)
    rows, d, p = [], dt.date.today() - dt.timedelta(days=int(n_days * 1.45)), 25000.0
    while d < dt.date.today():
        if d.weekday() < 5:
            t0 = dt.datetime.combine(d, dt.time(9, 15), IST).timestamp()
            drift = rng.normal(0, 0.0004)
            for m in range(375):
                o = p
                p = p * (1 + drift + rng.normal(0, 0.0007)) + 4 * math.sin(m / 25)
                rows.append([t0 + 60 * m, o, max(o, p) + 2, min(o, p) - 2, p, 0])
        d += dt.timedelta(days=1)
    return rows


def engine(tmp_path, rows):
    from radar.config import load_config
    from live.engine import LiveEngine
    e = LiveEngine(load_config, tmp_path)
    e.charts.history = lambda s: []
    e.charts.offline_rows = lambda s: rows
    e.factory_rules = lambda: []
    return e


def test_all_timeframes_and_markings(tmp_path):
    from live.mtf import MTF
    e = engine(tmp_path, minutes())
    v = MTF(e).view("NIFTY")
    P = v["panels"]
    assert set(P) == {"1W", "1D", "4H", "1H", "15m", "5m"}               # 2.70: 4H replaces 75m, 1W added
    for tf, p in P.items():
        assert p["candles"], tf
        assert p["trend"] in ("up", "down", "sideways"), tf
        assert not p.get("failed"), (tf, p.get("failed"))
    day = [c for c in P["4H"]["candles"]]
    per_day = {}
    for c in day:
        per_day.setdefault(dt.datetime.utcfromtimestamp(c["time"]).date(), 0)
        per_day[dt.datetime.utcfromtimestamp(c["time"]).date()] += 1
    assert max(per_day.values()) == 2                                  # 4H = 09:15-13:15 and 13:15-15:30
    v75 = MTF(e).view("NIFTY", ["75m"])                                 # the old 75m still answers for old links
    assert max(sum(1 for c in v75["panels"]["75m"]["candles"] if dt.datetime.utcfromtimestamp(c["time"]).date() == d) for d in per_day) == 5
    names = {x["name"] for x in P["1D"]["levels"]}
    assert {"PDH", "PDL", "PWH", "PWL"} <= names
    assert any(x["htf"] for x in P["5m"]["htf_levels"])               # daily levels drawn on the 5-minute chart
    assert "vwap" in P["5m"]["series"] and "ema20" in P["1D"]["series"]
    assert v["summary"] and isinstance(v["confluence"], list)


def test_strategy_overlay_resolves_each_signal(tmp_path):
    from live.mtf import MTF
    e = engine(tmp_path, minutes(20, seed=7))
    m = MTF(e)
    ids = [o["id"] for o in m.overlays("NIFTY")]
    assert "setup:ema_pullback" in ids and not any(i.endswith("wall_fade") for i in ids)
    total = 0
    for oid in ids:
        r = m.overlay("NIFTY", oid, days=10)
        assert "error" not in r, r
        for s in r["signals"]:
            assert s["result"] in ("target", "stop", "time", "open")
            assert (s["stop"] < s["entry"] < s["target"]) if s["dir"] == "up" else (s["target"] < s["entry"] < s["stop"])
        total += r["stats"]["n"]
    assert total > 0


def test_overlay_is_plain_json(tmp_path):
    import json
    from live.mtf import MTF
    m = MTF(engine(tmp_path, minutes(20, seed=7)))
    for o in m.overlays("NIFTY"):
        json.dumps(m.overlay("NIFTY", o["id"], days=10))
    json.dumps(m.view("NIFTY"))


def test_dhan_bars_are_fetched_in_90_day_chunks_and_cached(tmp_path, monkeypatch):
    from live import mtf as M
    e = engine(tmp_path, [])
    calls = []

    class FakeDhan:
        def daily(self, sym, frm, to, security_id=None, segment="IDX_I", instrument="INDEX"):
            calls.append(("D", frm, to))
            t = dt.datetime.combine(dt.date.today() - dt.timedelta(days=3), dt.time(0, 0), IST).timestamp()
            return {"timestamp": [t], "open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5], "volume": [10]}

        def post(self, path, body):
            calls.append((body["interval"], body["fromDate"], body["toDate"]))
            t0 = dt.datetime.combine(dt.date.fromisoformat(body["fromDate"]), dt.time(9, 15), IST).timestamp()
            return {"timestamp": [t0, t0 + 900], "open": [1, 2], "high": [2, 3], "low": [0.5, 1], "close": [2, 2.5], "volume": [0, 0]}
    m = M.MTF(e)
    monkeypatch.setattr(m, "_dhan", lambda: FakeDhan())
    rows = m.base_rows("NIFTY", 15, 60)
    assert rows and all(c[0] == "15" for c in calls)
    spans = [(dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days for _, a, b in calls]
    assert max(spans) <= 89 and len(calls) >= 1
    n = len(calls)
    m.base_rows("NIFTY", 15, 60)
    assert len(calls) == n                                   # cached: no new call within 30 minutes
    assert (tmp_path / "charts" / "NIFTY_15.json").exists()
    d = m.base_rows("NIFTY", "D", 400)
    assert d and calls[-1][0] == "D"
