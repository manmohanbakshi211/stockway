"""Candlestick / price-action bullets: each pattern found on a textbook example, and only there."""
import numpy as np

from live import candles as C

BASE = [(100, 101, 99, 100.5), (100.5, 101.5, 99.5, 100), (100, 101, 99, 100.6), (100.6, 101.4, 99.6, 100.1),
        (100.1, 101.2, 99.4, 100.4), (100.4, 101.3, 99.5, 100.2)]        # quiet bars, ATR about 2


def run(extra):
    o, h, l, c = (np.array([b[k] for b in BASE + extra], float) for k in range(4))
    return C.all_patterns(o, h, l, c)


def last(extra, fact):
    return run(extra)[fact][-1]


def test_engulfing():
    assert last([(100.5, 100.8, 99.2, 99.4), (99.3, 101.2, 99.1, 100.9)], "cdl_engulf") == 1
    assert last([(99.4, 100.8, 99.2, 100.6), (100.7, 100.9, 99.0, 99.2)], "cdl_engulf") == -1
    assert last([(100.5, 100.8, 99.2, 99.4), (99.5, 100.0, 99.3, 99.8)], "cdl_engulf") == 0


def test_pin_bar_and_hammer():
    bull_pin = (100.0, 100.4, 97.0, 100.3)                                  # long lower tail, body at the top
    assert last([bull_pin], "cdl_pin") == 1
    assert last([(100.0, 103.0, 99.7, 99.8)], "cdl_pin") == -1
    falling = [(100, 100.2, 98.8, 99.0), (99.0, 99.1, 97.9, 98.0), (98.0, 98.1, 96.9, 97.0)]
    assert last(falling + [(97.0, 97.2, 94.5, 97.1)], "cdl_hammer") == 1


def test_doji_and_direction():
    assert last([(100.0, 101.0, 99.0, 100.05)], "cdl_doji") == 1
    assert last([(100.0, 100.1, 97.5, 100.05)], "cdl_doji_dir") == 1       # dragonfly
    assert last([(100.0, 102.5, 99.95, 100.02)], "cdl_doji_dir") == -1     # gravestone


def test_morning_and_evening_star():
    assert last([(101, 101.2, 98.0, 98.2), (98.1, 98.4, 97.6, 98.0), (98.1, 100.6, 98.0, 100.4)], "cdl_star") == 1
    assert last([(98.2, 101.2, 98.0, 101.0), (101.0, 101.4, 100.7, 101.1), (101.0, 101.1, 98.5, 98.8)], "cdl_star") == -1


def test_inside_bar_breakout_and_fakey():
    mother, inside = (100, 102.0, 98.0, 101.5), (101.0, 101.5, 99.5, 100.5)
    assert last([mother, inside], "cdl_inside") == 1
    assert last([mother, inside, (100.5, 102.6, 100.4, 102.4)], "ib_break") == 1
    assert last([mother, inside, (100.6, 101.9, 100.2, 101.0)], "ib_fakey") == -1      # pokes above, closes back inside
    assert last([mother, inside, (100.4, 100.6, 99.2, 100.0)], "ib_fakey") == 1


def test_rectangle_break_and_failure():
    box = [(100, 101, 99, 100.8), (100.8, 101, 99.1, 99.2), (99.2, 100.9, 99.0, 100.7), (100.7, 101.0, 99.2, 99.3)] * 3
    assert last(box + [(99.5, 102.5, 99.4, 102.3)], "rect_break") == 1
    assert last(box + [(99.5, 102.5, 99.4, 102.3), (102.3, 102.4, 100.2, 100.4)], "rect_fail") == -1


def test_no_look_ahead():
    """A pattern on bar i never changes when later bars arrive."""
    rng = np.random.default_rng(5)
    c = 100 + np.cumsum(rng.normal(0, 0.4, 120))
    o = np.r_[c[0], c[:-1]]
    h, l = np.maximum(o, c) + rng.uniform(0.05, 0.6, 120), np.minimum(o, c) - rng.uniform(0.05, 0.6, 120)
    full = C.all_patterns(o, h, l, c)
    for cut in (40, 77, 100):
        part = C.all_patterns(o[:cut], h[:cut], l[:cut], c[:cut])
        for k in full:
            assert np.array_equal(full[k][:cut], part[k]), (k, cut)
