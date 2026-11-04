"""2.36: Fortune chart: honest ranges from the symbol's own history, checked live; buying at 70% tested on the bench."""
import datetime as dt
import types

import numpy as np

from live import fortune as F, mtfcandles as MC
from live.clock import IST

DAY = dt.date(2026, 11, 4)


def _walk(days, seed=1, start_day=dt.date(2026, 9, 1)):
    rng = np.random.default_rng(seed)
    rows, px, d, n = [], 1000.0, start_day, 0
    while n < days:
        if d.weekday() < 5:
            t0 = F._open_ts(d)
            for i in range(375):
                o = px
                px *= 1 + rng.normal(0, 0.0008)
                rows.append([t0 + 60 * i, o, max(o, px) * (1 + abs(rng.normal(0, 0.0002))), min(o, px) * (1 - abs(rng.normal(0, 0.0002))), px, 100])
            n += 1
        d += dt.timedelta(days=1)
    return rows


def test_candles_fraction_and_next_times():
    rows = _walk(2)
    g = F.groups(rows, "15m")
    assert len(g) == 50 and all(len(r) == 15 for _, r in g)
    c = F.ohlc(*g[0])
    assert c[1] == g[0][1][0][1] and c[4] == g[0][1][-1][4]
    s = F._open_ts(DAY) + 30 * 60
    assert F.fraction("15m", s, s + 10.5 * 60) == 0.7
    late = dt.datetime.combine(DAY, dt.time(15, 15), IST).timestamp()
    nt = F.next_times("15m", late, 2)
    assert dt.datetime.fromtimestamp(nt[0], IST).date() == DAY + dt.timedelta(days=1)          # after the close: next day 9:15
    fri = dt.datetime.combine(dt.date(2026, 11, 6), dt.time(9, 15), IST).timestamp()
    assert dt.datetime.fromtimestamp(F.next_times("1D", fri, 1)[0], IST).date() == dt.date(2026, 11, 9)   # weekend skipped
    assert dt.datetime.fromtimestamp(F.next_times("1D", F._open_ts(dt.date(2026, 11, 9)), 1)[0], IST).date() == dt.date(2026, 11, 11)  # holiday


def test_the_ranges_are_honest_on_unseen_days():
    rows = _walk(45, seed=3)
    g = F.groups(rows, "15m")
    c = np.asarray([F.ohlc(s, r) for s, r in g], float)
    atr = F.atr_before(c)
    cut = 30 * 25
    rp = F.replay(g[:cut], c[:cut], atr[:cut], 0.7)
    q = rp["rest_q"]
    hits = n = 0
    for i in range(cut, len(g)):
        start, r = g[i]
        p = F.ohlc(start, r[:int(round(0.7 * len(r)))])
        lo, hi = p[4] + q[1] * atr[i], p[4] + q[3] * atr[i]
        hits += int(lo <= c[i][4] <= hi)
        n += 1
    assert 0.58 <= hits / n <= 0.78                       # about 68% on days the history never saw
    assert 40 <= rp["up_share"] * 100 <= 60                # a random walk: no direction edge, and the page says so
    cn = F.cone(c, atr, 6)
    assert cn[6]["q"][3] > cn[1]["q"][3] > 0 > cn[1]["q"][1] > cn[6]["q"][1]   # the cone widens


def test_compute_and_live_grading(tmp_path, monkeypatch):
    rows = _walk(30, seed=5, start_day=DAY - dt.timedelta(days=50))
    today0 = F._open_ts(DAY)
    rng = np.random.default_rng(9)
    px, today = rows[-1][4], []
    for i in range(70):                                   # 10:25: 70 minutes of today
        o = px
        px *= 1 + rng.normal(0, 0.0008)
        today.append([today0 + 60 * i, o, max(o, px), min(o, px), px, 100])
    now = today0 + 70 * 60 + 30 + 10.5 * 60 - 60 * 10    # inside the 5th 15-min candle, ~70% through
    monkeypatch.setattr(F.time, "time", lambda: now)

    class Store(dict):
        def get(self, k, d=None):
            return dict.get(self, k, d)

        def put(self, k, v):
            self[k] = v
    e = types.SimpleNamespace(store=Store(), metrics={"ABC": {"spot": 1.0}}, events=None, cdl=None,
                              charts=types.SimpleNamespace(today_bars=lambda s: [r for r in today if r[0] < now]))
    mtf = types.SimpleNamespace(long_rows=lambda sym, base, days: ([r for r in rows if base == 1], "test"))
    fo = F.Fortune(e)
    d = fo.compute(mtf, "ABC", "15m")
    assert d["enough"] and d["forming"] and 0.5 <= d["fraction"] <= 0.9
    lo, hi = d["now"]["close_68"]
    lo95, hi95 = d["now"]["close_95"]
    assert lo95 <= lo < hi <= hi95 and len(d["cone"]) == 7 and d["cone"][-1]["hi95"] > d["cone"][1]["hi95"] - 1e-9
    assert fo.pending                                     # remembered for the live check
    later = now + 15 * 60                                  # the candle has closed
    monkeypatch.setattr(F.time, "time", lambda: later)
    for i in range(70, 90):
        o = px
        px *= 1 + rng.normal(0, 0.0008)
        today.append([today0 + 60 * i, o, max(o, px), min(o, px), px, 100])
    e.charts = types.SimpleNamespace(today_bars=lambda s: [r for r in today if r[0] < later])
    fo.compute(mtf, "ABC", "15m")
    g = fo.grades["15m"]
    assert g["n"] == 1 and g["in95"] in (0, 1) and e.store["fortune:grades"]


def test_buying_at_70_percent_is_paper_tested_and_checked_at_the_close(tmp_path, monkeypatch):
    from tests.test_mtfcandles import _engine, _minutes, T0
    rows = _minutes([(15, 100, 96), (15, 96, 92), (15, 91, 90), (15, 89.8, 93.5), (10, 93.5, 94)])
    at70 = dt.datetime.fromtimestamp(T0 + 45 * 60 + 11 * 60 + 5, IST)      # 70% into the 4th 15-min candle
    e, sent, recorded = _engine([r for r in rows if r[0] < at70.timestamp()], at70, tmp_path)
    sc = MC.CandleScanner(e)
    cfg = dict(MC.DEFAULTS, intraday_min=[15], five_min_all_stocks=False, daily=False, weekly=False, early_min=[15], early_at=0.7)
    MC._C.update(cfg=cfg, mt=MC.FILE.stat().st_mtime)
    try:
        sc.day, sc.done[15] = at70.date(), 3               # the first three candles were already scanned
        sc.tick(at70)
        early = [s for s in sent if s[3] == "15m@70%"]
        assert early and early[0][2].startswith("candle_") and sc.early_seen
        close = dt.datetime.fromtimestamp(T0 + 60 * 60 + 5, IST)
        e.charts.rows, e.charts.now_ts = rows, close.timestamp()
        e.clock.now = lambda: close
        sc.tick(close)
        st = sc.early_stats["15m"]
        assert st["n"] >= 1 and st["pct"] is not None and not sc.early_seen
    finally:
        MC._C.clear()
