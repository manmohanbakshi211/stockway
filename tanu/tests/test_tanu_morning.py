"""Morning features (gap, first 5-minute candle) and the gap-down setups (Mandeep, 13 Nov 2026)."""
import datetime as dt

import numpy as np
import pytest

from tanu.engine.core import Engine
from tanu.evaluate.search import pretest_ids
from tanu.setups import registry
from tanu.state.day import DayContext

DAY = "2026-11-13"
T0 = int(dt.datetime.fromisoformat(DAY + "T09:15:00+05:30").timestamp())


class Opt:
    def atm(self, sym, day, t, spot, side):
        return round(spot / 50) * 50

    def bar(self, sym, day, strike, side, t):
        return (100.0, 101.0, 99.0, 100.0)


def _engine(ids):
    good, bad = registry.load()
    assert not bad
    return Engine([s for s in good if s.id in ids], Opt())


def _run(eng, path, prev=25300.0):
    eng.start_day("NIFTY", DAY, DayContext(prev_close=prev, med_range=1.27, frac=np.linspace(0.2, 1, 375)))
    feats = []
    for m, (o, h, l, c) in enumerate(path):
        eng.on_bar("NIFTY", T0 + 60 * m, o, h, l, c)
        feats.append(eng.sym["NIFTY"].prev_feat)
    return feats


def _today_like():
    """Gap down to 25,210, first 5-minute candle falls to 25,105 (red), dips to 25,078, back above 25,105 at 09:26."""
    p = [(25210, 25212, 25180, 25185), (25185, 25190, 25150, 25155), (25155, 25160, 25120, 25125),
         (25125, 25130, 25100, 25110), (25110, 25115, 25100, 25105)]            # 09:15-09:19
    p += [(25105, 25106, 25078, 25090), (25090, 25095, 25082, 25092), (25092, 25098, 25088, 25095),
          (25095, 25100, 25090, 25098), (25098, 25104, 25094, 25100), (25100, 25103, 25096, 25101),
          (25101, 25112, 25100, 25110)]                                           # 09:20-09:26: reclaim at 09:26
    p += [(25110 + i, 25112 + i, 25108 + i, 25111 + i) for i in range(40)]
    return p


def test_morning_features_are_known_only_from_0919():
    feats = _run(_engine(set()), _today_like())
    assert feats[0]["gap"] == pytest.approx((25210 - 25300) / 25300 * 100)
    assert feats[3]["first5"] is None and feats[3]["vs_first5"] is None          # 09:18: candle not closed yet
    assert feats[4]["first5"] == pytest.approx((25105 - 25300) / 25300 * 100)   # -0.77%
    assert feats[4]["first5_body"] == pytest.approx((25105 - 25210) / 25300 * 100)
    assert feats[5]["vs_first5"] < 0 and feats[11]["vs_first5"] > 0 and feats[4]["long"] == 1.0


def test_gap_down_setups_fire_calls_at_the_right_minutes_and_never_puts():
    eng = _engine({"gap_down_call_920", "gap_down_reversal"})
    _run(eng, _today_like())
    sig = {(s["setup"], s["d"]): s["m"] for s in eng.signals}
    assert sig == {("gap_down_call_920", 1): 4, ("gap_down_reversal", 1): 11}     # 09:19 close -> fill 09:20; 09:26
    # the mirror picture (gap up, big green first candle, falls back below) gives no put: calls-only bullets
    mirror = [(2 * 25300 - o, 2 * 25300 - l, 2 * 25300 - h, 2 * 25300 - c) for o, h, l, c in _today_like()]
    eng2 = _engine({"gap_down_call_920", "gap_down_reversal"})
    _run(eng2, mirror)
    assert eng2.signals == []


def test_small_gap_or_green_first_candle_does_not_fire():
    p = _today_like()
    small = [(o + 100, h + 100, l + 100, c + 100) for o, h, l, c in p]              # only -0.38% by 09:19
    eng = _engine({"gap_down_call_920", "gap_down_reversal"})
    _run(eng, small)
    assert eng.signals == []


def test_pretested_ideas_count_as_tried():
    ids = pretest_ids()
    assert {"pretest:gap_down_call_920", "pretest:gap_down_green_925", "pretest:gap_down_hammer_925",
            "pretest:gap_down_reclaim_930"} <= ids
