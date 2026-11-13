"""Higher-timeframe EMA alert candles (5 EMA method), the rr exit (stop level + R target) and re-entries."""
import datetime as dt

import numpy as np
import pytest

from tanu.engine.core import Engine
from tanu.engine.tf import EmaAlerts, needed
from tanu.setups import registry
from tanu.setups.registry import Exit, Setup
from tanu.state.day import DayContext

DAY = "2026-11-13"
T0 = int(dt.datetime.fromisoformat(DAY + "T09:15:00+05:30").timestamp())


def test_needed_reads_timeframes_from_feature_names_only():
    assert needed(["d_ealert_5m_5", "prev:ealert_15m_9", "ealert_5m_5_stop", "move", None]) == {(5, 5), (15, 9)}


def _feed(a, candles):
    """candles: list of 5-min (o, h, l, c) built from 5 identical-shape minutes; returns per-minute (sig, stop)."""
    out, m = [], 0
    for o, h, l, c in candles:
        mins = [(o, max(o, c), min(o, c), c)] * 5
        mins[1] = (o, h, min(o, c), c)
        mins[2] = (c, max(o, c), l, c)
        for x in mins:
            out.append(a.on_bar(m, *x))
            m += 1
    return out


def test_alert_candle_above_ema_then_break_of_its_low_gives_minus_one_with_the_high_as_stop():
    a = EmaAlerts(5, 5, ema=100.0)
    # candle 1 closes at 110 with low 105 > EMA (103.3 after it): up-alert (high 111, low 105)
    # candle 2 dips to 104: breaks the alert low -> -1, stop 111
    res = _feed(a, [(106, 111, 105, 110), (109, 109, 104, 106)])
    sigs = [(i, s, st) for i, (s, st) in enumerate(res) if s]
    assert sigs == [(7, -1, 111)]                                 # minute 7 (09:22), the dip minute of candle 2
    assert a.up is None                                          # used up


def test_alert_that_touches_the_ema_or_lapses_gives_nothing():
    a = EmaAlerts(5, 5, ema=100.0)
    res = _feed(a, [(101, 104, 99, 103)])                       # closes above but low touches the EMA: no alert
    assert a.up is None and all(s == 0 for s, _ in res)
    b = EmaAlerts(5, 5, ema=100.0)
    _feed(b, [(106, 111, 105, 110)])
    assert b.up == (111, 105)
    _feed(b, [(110, 112, 106, 103)])                             # next candle: no break of 105, closes near EMA
    assert b.up is None                                          # lapsed after one candle


def test_down_alert_break_gives_plus_one_with_the_low_as_stop():
    a = EmaAlerts(15, 5, ema=100.0)
    res = []
    m = 0
    for o, h, l, c in [(95, 96, 90, 91)] * 15 + [(92, 97, 92, 96)] * 15:
        res.append(a.on_bar(m, o, h, l, c))
        m += 1
    assert a.ema is not None
    hits = [(i, s, st) for i, (s, st) in enumerate(res) if s]
    assert hits == [(15, 1, 90)]                                  # first minute of the next 15-min candle


def test_rr_exit_needs_its_fields():
    with pytest.raises(ValueError):
        Exit(type="rr")
    Exit(type="rr", stop_feature="ealert_5m_5_stop", target_r=3)


class Opt:
    def atm(self, sym, day, t, spot, side):
        return 100.0

    def bar(self, sym, day, strike, side, t):
        return (50.0, 51.0, 49.0, 50.0)


def _setup(max_per_day=1):
    return Setup(id="ema_test", name="t", why="t", source="t", valid_from=DAY, symbols=["NIFTY"], window=("09:15", "15:00"),
                 when=[("d_long", "<", 0), ("d_ealert_5m_5", ">", 0)],
                 exit=Exit(name="rr", type="rr", stop_feature="ealert_5m_5_stop", target_r=3, premium_stop_pct=-90),
                 max_per_day=max_per_day)


def _run(setup, path):
    eng = Engine([setup], Opt())
    eng.carry_ema[("NIFTY", 5, 5)] = 100.0
    eng.start_day("NIFTY", DAY, DayContext(prev_close=100.0, med_range=1.2, frac=np.linspace(0.2, 1, 375)))
    for m, (o, h, l, c) in enumerate(path):
        eng.on_bar("NIFTY", T0 + 60 * m, o, h, l, c)
    eng.end_day("NIFTY")
    return eng


def _alert_then_break():
    """09:15-09:19 a candle fully above the EMA (high 111, low 105); 09:20 breaks 105 and closes 104.5."""
    return [(106, 107, 105.5, 106.5), (106.5, 111, 106, 110), (110, 110, 105, 108), (108, 109, 107, 108.5),
            (108.5, 110.5, 108, 110), (110, 110, 104, 104.5)]


def test_rr_exit_hits_the_target_at_three_r():
    # entry spot 104.5 (signal close), stop 111 -> risk 6.5, target 104.5 - 19.5 = 85
    path = _alert_then_break() + [(104, 104, 100, 101), (101, 101, 90, 92), (92, 92, 84.5, 86), (86, 87, 85, 86)]
    eng = _run(_setup(), path)
    (p,) = eng.closed
    assert p.d == -1 and p.side == "PE" and p.stop == 111 and p.state["target_level"] == pytest.approx(85.0)
    assert p.reason == "target" and p.exit_t == T0 + 60 * 8


def test_rr_exit_stops_at_the_alert_high_and_never_trails():
    path = _alert_then_break() + [(104, 104, 95, 96), (96, 112, 96, 111.5)]
    eng = _run(_setup(), path)
    (p,) = eng.closed
    assert p.reason == "stop" and p.stop == 111                   # not trailed down after the move to 95


def test_max_per_day_allows_reentry_after_a_close_never_two_at_once():
    path = _alert_then_break() + [(104, 104, 103, 103.5), (103.5, 112, 103.5, 111.5)]      # stopped at 09:22
    path += [(111.5, 112, 111.2, 111.8)] * 2                                                  # candle 2: no alert
    path += [(111.8, 116, 111.5, 115), (115, 118, 114, 117), (117, 118, 116.5, 117.5),
             (117.5, 117.6, 113, 114), (114, 115, 113.5, 114.5)]                             # candle 3: alert
    path += [(114.5, 114.6, 111, 111.2)] + [(111.2, 111.3, 111, 111.1)] * 20                 # 09:30 breaks 111.5
    eng1 = _run(_setup(1), path)
    eng3 = _run(_setup(3), path)
    assert len(eng1.closed) == 1
    assert len(eng3.closed) == 2 and eng3.closed[1].signal_t == T0 + 60 * 15
    spans = sorted((p.signal_t, p.exit_t or p.signal_t) for p in eng3.closed)
    assert all(b[0] >= a[1] for a, b in zip(spans, spans[1:]))    # one position at a time


def test_library_has_both_5ema_setups_in_shadow():
    good, bad = registry.load()
    ids = {s.id: s for s in good}
    assert not bad
    for sid in ("ema5_sell_5m", "ema5_buy_15m"):
        s = ids[sid]
        assert s.status == "shadow" and s.exit.type == "rr" and s.exit.target_r == 3 and s.max_per_day == 3
