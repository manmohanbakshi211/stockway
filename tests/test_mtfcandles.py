"""2.31: candle patterns on every timeframe as each candle closes, recorded, as facts on every signal, and on trial."""
import datetime as dt
import types

import numpy as np

from live import mtfcandles as MC, ta
from live.clock import IST

DAY = dt.date(2026, 11, 4)
T0 = MC.session_start(DAY)


def _minutes(segments):
    """1-minute rows from (n_minutes, open, close) segments, each a straight line with small wicks."""
    rows, t, px = [], T0, None
    for n, o, c in segments:
        for i in range(n):
            a = o + (c - o) * i / n
            b = o + (c - o) * (i + 1) / n
            rows.append([t, a, max(a, b) + 0.1, min(a, b) - 0.1, b, 100])
            t += 60
    return rows


def test_resample_matches_the_old_one():
    rows = _minutes([(45, 100, 90), (30, 90, 95)])
    a = np.asarray(rows)
    old = ta.resample(a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5], 15, T0)
    new = MC.resample(rows, 15, T0)
    for k in range(6):
        assert np.allclose(old[k], new[:, k])


def test_daily_and_weekly_use_finished_candles_only():
    d_ohlc = []
    day = DAY - dt.timedelta(days=40)
    while day <= DAY:                                  # weekdays up to and including today
        if day.weekday() < 5:
            d_ohlc.append([day.isoformat(), 100, 102, 98, 101, 1000])
        day += dt.timedelta(days=1)
    d, w = MC.daily_weekly(d_ohlc, DAY)
    last = dt.datetime.fromtimestamp(d[-1][0], IST).date()
    assert last < DAY and last == DAY - dt.timedelta(days=1)
    lastw = dt.datetime.fromtimestamp(w[-1][0], IST).date()
    assert lastw.isocalendar()[:2] < DAY.isocalendar()[:2]       # this week's candle isn't finished


class _Charts:
    def __init__(self, rows):
        self.rows = rows

    def today_bars(self, sym):
        return [r for r in self.rows if r[0] < self.now_ts]

    def context(self, sym, m):
        return {"pdh": 110.0, "pdl": 89.5, "pdc": 100.0}


def _engine(rows, now, tmp_path):
    sent, recorded = [], []
    ch = _Charts(rows)
    ch.now_ts = now.timestamp()
    e = types.SimpleNamespace(
        clock=types.SimpleNamespace(now=lambda: now), charts=ch, metrics={"TEST": {"is_index": True, "vwap": 99.0}},
        daily={}, data_dir=tmp_path, source="upstox", log=lambda *a: None, _every=lambda *a, **k: True,
        recorder=types.SimpleNamespace(candles=lambda r: recorded.extend(r)), bench=object(),
        bench_signal=lambda sym, d, now, kind, variant, name: sent.append((sym, d, kind, variant, name)) or 1)
    return e, sent, recorded


def test_a_15_minute_engulfing_at_support_is_found_recorded_and_tested(tmp_path):
    # 15-minute candles: down, down, a small red one into yesterday's low (89.5), then a big green one engulfing it
    rows = _minutes([(15, 100, 96), (15, 96, 92), (15, 91, 90), (15, 89.8, 93.5), (10, 93.5, 94)])
    now = dt.datetime.fromtimestamp(T0 + 60 * 60 + 5, IST)            # 10:15:05: the 4th candle just closed
    e, sent, recorded = _engine(rows, now, tmp_path)
    sc = MC.CandleScanner(e)
    cfg = dict(MC.DEFAULTS, intraday_min=[15], five_min_all_stocks=False, daily=False, weekly=False)
    MC._C.update(cfg=cfg, mt=MC.FILE.stat().st_mtime)
    try:
        sc.tick(now)
    finally:
        MC._C.clear()
    x = sc.latest[("TEST", "15m")]
    assert x["patterns"].get("engulfing") == 1 and ("engulfing", 1) in x["ideas"]
    assert any(r[1] == "TEST" and r[2] == "15m" and r[4] == "engulfing" and r[5] == 1 for r in recorded)
    assert ("TEST", "up", "candle_engulfing", "15m", "Engulfing bar (15m candle)") in sent
    f = sc.facts("TEST", "up")
    assert f["cdl_15m_al"] == 1 and sc.facts("TEST", "down")["cdl_15m_al"] == -1
    n = len(sent)
    sc.tick(now)                                                     # the same candle is never processed twice
    assert len(sent) == n


def test_the_forming_candle_is_information_only(tmp_path):
    rows = _minutes([(15, 100, 96), (15, 96, 92), (15, 91, 90), (8, 89.8, 93.5)])
    now = dt.datetime.fromtimestamp(T0 + 53 * 60 + 5, IST)
    e, sent, _ = _engine(rows, now, tmp_path)
    sc = MC.CandleScanner(e)
    v = sc.now_view("TEST")
    r15 = next(r for r in v["rows"] if r["tf"] == "15m")
    assert r15["forming"] is not None and 6 < r15["mins_left"] < 8 and not sent


def test_one_pass_scan_matches_the_lab_functions_and_needs_only_recent_candles():
    from live import candles as CDL, registry
    rng = np.random.default_rng(7)
    spec = registry.candle_spec()
    for trial in range(40):
        n = 150
        c = 100 * np.cumprod(1 + rng.normal(0, 0.004, n))
        o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, 0.001, n))
        h = np.maximum(o, c) * (1 + abs(rng.normal(0, 0.002, n)))
        l = np.minimum(o, c) * (1 - abs(rng.normal(0, 0.002, n)))
        lv = [float(np.min(l[:40])), float(np.max(h[:40])), float(c[60])]
        pats, ideas = CDL.last_bar_scan(o, h, l, c, lv, spec)
        assert pats == dict(CDL.on_last_bar(o, h, l, c))
        assert ideas == CDL.trade_ideas(o, h, l, c, lv, spec, last_only=True)
        p2, i2 = CDL.last_bar_scan(o[-MC.KEEP:], h[-MC.KEEP:], l[-MC.KEEP:], c[-MC.KEEP:], lv, spec)
        assert p2 == pats and [(x["name"], x["d"], x["stop"]) for x in i2] == [(x["name"], x["d"], x["stop"]) for x in ideas]
