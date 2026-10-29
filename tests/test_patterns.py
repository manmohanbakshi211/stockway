"""Chart pattern engine on hand-made price paths."""
import numpy as np

from live import patterns as P


def _bars(path, wick=1.0):
    c = np.asarray(path, dtype=float)
    o = np.r_[c[0], c[:-1]]
    return {"o": o, "h": np.maximum(o, c) + wick, "l": np.minimum(o, c) - wick, "c": c}


def _walk(points, steps=4):
    out = []
    for a, b in zip(points[:-1], points[1:]):
        out += list(np.linspace(a, b, steps, endpoint=False))
    return out + [points[-1]]


def test_double_bottom_confirms_on_neckline_break():
    path = _walk([120, 100, 112, 100.5, 113.5], steps=5)
    found = P.detect(_bars(path, 0.5), recent=2, k=1.0)
    db = [p for p in found if p["pattern"] == "double_bottom"]
    assert db and db[0]["direction"] == "up" and db[0]["stop"] < 100.5 and db[0]["target"] > 112


def test_double_top_confirms_on_neckline_break():
    path = _walk([80, 100, 88, 99.5, 86.5], steps=5)
    dt_ = [p for p in P.detect(_bars(path, 0.5), recent=2, k=1.0) if p["pattern"] == "double_top"]
    assert dt_ and dt_[0]["direction"] == "down" and dt_[0]["target"] < 88


def test_flag_breakout():
    pole = list(np.linspace(100, 130, 6))
    flag = [129, 128, 127.5, 128, 127, 127.5]
    path = [100] * 4 + pole + flag + [131.5]
    fl = [p for p in P.detect(_bars(path, 0.3), recent=1) if p["pattern"] == "flag"]
    assert fl and fl[0]["direction"] == "up" and fl[0]["target"] > 140


def test_swings_and_structure():
    path = _walk([100, 110, 104, 116, 109, 122, 115, 128], steps=4)
    sw = P.swings_for_chart(_bars(path, 0.5), k=1.0)
    assert [s["label"] for s in sw if s["label"]][-3:] == ["HL", "HH", "HL"]
    assert P.structure(sw) == "uptrend"
