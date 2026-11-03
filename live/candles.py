"""Candlestick and price-action patterns (bullets), from the basics Mandeep gave the system:
"The Candlestick Trading Bible", Fidelity / Kirkpatrick "Identifying Chart Patterns", "A to Z of Stock Market".
Rules are written here in our own words from the standard definitions; being in a book is not evidence. Each pattern
becomes a fact the Strategy Lab tests (train / validation / holdout) and a shadow alert live, and only patterns that
hold up out of sample ever reach you as alerts.

Gun part (shared, rarely changes): `shape()` turns bars into body / wick / range facts and `levels_dist()` measures how
close price is to the day's key levels. Bullet part: one small function per pattern in PATTERNS; adding a pattern is one
function + one line in PATTERNS (+ one line in live/registry.py). The same code runs live and in the Lab backtest.

Every pattern returns an array with one value per bar: +1 bullish, -1 bearish, 1 for direction-free patterns (inside
bar, narrow range, doji), 0 none. A pattern on bar i uses only bars up to i (closed bars, no look-ahead).
"""
from __future__ import annotations

import numpy as np


def _arr(*xs):
    return [np.asarray(x, dtype=float) for x in xs]


def _roll(x, n, fn):
    """fn (np.nanmin / np.nanmax / np.nanmean) over the last n values ending at each bar (fewer at the start)."""
    x = np.asarray(x, dtype=float)
    if not len(x):
        return x
    pad = np.r_[np.full(n - 1, np.nan), x]
    w = np.lib.stride_tricks.sliding_window_view(pad, n)
    with np.errstate(invalid="ignore"), __import__("warnings").catch_warnings():
        __import__("warnings").simplefilter("ignore", RuntimeWarning)
        return fn(w, axis=1)


def atr(h, l, c, n=14):
    """Average true range over the last n bars (a plain mean; fewer bars at the start of the day)."""
    h, l, c = _arr(h, l, c)
    if not len(c):
        return c
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    return _roll(tr, n, np.nanmean)


def shape(o, h, l, c) -> dict:
    """Body, wicks and range of every bar, as shares of the bar's range, plus a 14-bar ATR."""
    o, h, l, c = _arr(o, h, l, c)
    rng = h - l
    safe = np.where(rng > 0, rng, np.nan)
    body = np.abs(c - o)
    top, bot = np.maximum(o, c), np.minimum(o, c)
    with np.errstate(invalid="ignore", divide="ignore"):
        return {"o": o, "h": h, "l": l, "c": c, "rng": rng, "body": body, "top": top, "bot": bot,
                "body_pct": body / safe, "up_pct": (h - top) / safe, "lo_pct": (bot - l) / safe,
                "bull": c > o, "bear": c < o, "atr": atr(h, l, c)}


def _prev(x, k=1, fill=np.nan):
    x = np.asarray(x, dtype=float)
    return np.r_[np.full(k, fill), x[:-k]] if len(x) > k else np.full(len(x), fill)


def _sig(cond_bull, cond_bear):
    return np.where(cond_bull, 1.0, np.where(cond_bear, -1.0, 0.0))


# ------------------------------------------------------------------ bullets: one function per pattern
def engulfing(s):
    """The second body covers the whole first body, in the other colour."""
    pb, pt, pbody = _prev(s["bot"]), _prev(s["top"]), _prev(s["body"])
    pbull, pbear = _prev(s["bull"].astype(float)) == 1, _prev(s["bear"].astype(float)) == 1
    big = s["body"] > pbody
    return _sig(s["bull"] & pbear & (s["bot"] <= pb) & (s["top"] >= pt) & big,
                s["bear"] & pbull & (s["top"] >= pt) & (s["bot"] <= pb) & big)


def harami(s):
    """A small body inside the previous large body (the 'pregnant' bar), against the previous colour."""
    pb, pt, pbody, prng = _prev(s["bot"]), _prev(s["top"]), _prev(s["body"]), _prev(s["rng"])
    pbull, pbear = _prev(s["bull"].astype(float)) == 1, _prev(s["bear"].astype(float)) == 1
    with np.errstate(invalid="ignore"):
        mother = (pbody >= 0.5 * prng) & (prng >= 0.8 * s["atr"])
        inside = (s["top"] <= pt) & (s["bot"] >= pb) & (s["body"] <= 0.5 * pbody)
    return _sig(mother & inside & pbear, mother & inside & pbull)


def doji(s):
    """Open and close almost equal (body at most 10% of the range)."""
    with np.errstate(invalid="ignore"):
        return np.where((s["body_pct"] <= 0.1) & (s["rng"] > 0), 1.0, 0.0)


def doji_dir(s):
    """Dragonfly doji (long lower wick: buyers came back) +1; gravestone doji (long upper wick) -1."""
    with np.errstate(invalid="ignore"):
        d = s["body_pct"] <= 0.1
        return _sig(d & (s["lo_pct"] >= 0.6) & (s["up_pct"] <= 0.15), d & (s["up_pct"] >= 0.6) & (s["lo_pct"] <= 0.15))


def hammer(s):
    """Hammer after a fall (+1) / shooting star after a rise (-1): small body, one wick at least 60% of the range."""
    c = s["c"]
    fell, rose = c < _prev(c, 3), c > _prev(c, 3)
    with np.errstate(invalid="ignore"):
        small = s["body_pct"] <= 0.35
        return _sig(small & (s["lo_pct"] >= 0.6) & (s["up_pct"] <= 0.15) & fell,
                    small & (s["up_pct"] >= 0.6) & (s["lo_pct"] <= 0.15) & rose)


def pin_bar(s):
    """Pin bar: a tail of at least two thirds of the bar, the body in the other third, and a bar of normal size or more."""
    with np.errstate(invalid="ignore"):
        normal = s["rng"] >= 0.8 * s["atr"]
        return _sig(normal & (s["lo_pct"] >= 0.66) & (s["bot"] >= s["l"] + 0.66 * s["rng"]),
                    normal & (s["up_pct"] >= 0.66) & (s["top"] <= s["h"] - 0.66 * s["rng"]))


def star(s):
    """Morning star (+1): a big down bar, a small bar, then an up bar closing above the middle of the first body.
    Evening star (-1) the other way round."""
    o2, c2, b2, r2 = _prev(s["o"], 2), _prev(s["c"], 2), _prev(s["body"], 2), _prev(s["rng"], 2)
    bp1 = _prev(s["body_pct"])
    mid2 = (o2 + c2) / 2
    with np.errstate(invalid="ignore"):
        first_big = (b2 >= 0.5 * r2) & (r2 >= 0.8 * s["atr"])
        small_mid = bp1 <= 0.3
        return _sig(first_big & (c2 < o2) & small_mid & s["bull"] & (s["c"] > mid2),
                    first_big & (c2 > o2) & small_mid & s["bear"] & (s["c"] < mid2))


def tweezer(s):
    """Tweezer bottom (+1): two bars with the same low (within 5% of ATR) at a fresh 6-bar low, red then green.
    Tweezer top (-1) the other way round."""
    pl, ph = _prev(s["l"]), _prev(s["h"])
    pbull, pbear = _prev(s["bull"].astype(float)) == 1, _prev(s["bear"].astype(float)) == 1
    tol = 0.05 * s["atr"]
    lowest = s["l"] <= np.fmin(pl, _min_back(s["l"], 6)) + tol
    highest = s["h"] >= np.fmax(ph, _max_back(s["h"], 6)) - tol
    with np.errstate(invalid="ignore"):
        return _sig((np.abs(s["l"] - pl) <= tol) & pbear & s["bull"] & lowest,
                    (np.abs(s["h"] - ph) <= tol) & pbull & s["bear"] & highest)


def inside_bar(s):
    """Inside bar: the whole bar within the previous bar's high-low (a pause before the next move)."""
    return np.where((s["h"] <= _prev(s["h"])) & (s["l"] >= _prev(s["l"])), 1.0, 0.0)


def outside_bar(s):
    """Outside bar: a higher high and a lower low than the previous bar; direction by its close."""
    o = (s["h"] > _prev(s["h"])) & (s["l"] < _prev(s["l"]))
    return _sig(o & s["bull"], o & s["bear"])


def nr7(s):
    """Narrowest range of the last 7 bars (Crabel's NR7: quiet before a move)."""
    r = s["rng"]
    mn = _roll(r, 7, np.nanmin)
    mn[:6] = np.nan
    with np.errstate(invalid="ignore"):
        return np.where((r > 0) & (r <= mn), 1.0, 0.0)


def ib_break(s):
    """Inside-bar breakout: the bar before was inside its mother bar, and this bar closes beyond the mother's range."""
    ins = _prev(inside_bar(s)) == 1
    mh, ml = _prev(s["h"], 2), _prev(s["l"], 2)
    return _sig(ins & (s["c"] > mh), ins & (s["c"] < ml))


def ib_fakey(s):
    """Inside-bar false breakout ("fakey"): this bar pokes beyond the inside bar but closes back inside: trade the
    other way (+1 after a false break down, -1 after a false break up)."""
    ins = _prev(inside_bar(s)) == 1
    ih, il = _prev(s["h"]), _prev(s["l"])
    return _sig(ins & (s["l"] < il) & (s["c"] > il) & (s["c"] < ih), ins & (s["h"] > ih) & (s["c"] < ih) & (s["c"] > il))


def _band(s, n):
    """High / low of the previous n bars (not counting this one), and whether that band is a clean rectangle:
    no wider than 2.5 ATR and touched near both edges at least twice."""
    h, l, a = s["h"], s["l"], s["atr"]
    N = len(h)
    hh, ll = _max_back(h, n), _min_back(l, n)
    tol = 0.2 * (hh - ll)
    th, tl = np.zeros(N), np.zeros(N)
    for k in range(1, n + 1):                          # vectorised over bars: compare each of the n previous bars
        hk, lk = _prev(h, k), _prev(l, k)
        with np.errstate(invalid="ignore"):
            th += np.nan_to_num(hk >= hh - tol).astype(float)
            tl += np.nan_to_num(lk <= ll + tol).astype(float)
    with np.errstate(invalid="ignore"):
        ok = (np.arange(N) >= n) & np.isfinite(a) & (hh > ll) & (hh - ll <= 2.5 * a) & (th >= 2) & (tl >= 2)
    return hh, ll, ok


def rect_break(s, n=12):
    """Rectangle breakout: the last `n` bars stayed in a band no wider than 2.5 ATR, touched near both edges at least
    twice, and this bar closes beyond it (Fidelity: a pattern is active only after the breakout)."""
    hh, ll, ok = _band(s, n)
    c = s["c"]
    with np.errstate(invalid="ignore"):
        return _sig(ok & (c > hh), ok & (c < ll))


def rect_fail(s, n=12, within=3):
    """Failed rectangle breakout: a breakout in the last `within` bars and now a close back inside the band: trade the
    other way (Fidelity: false breakouts, 'stop and reverse')."""
    hh, ll, ok = _band(s, n)
    c = s["c"]
    with np.errstate(invalid="ignore"):
        rb = _sig(ok & (c > hh), ok & (c < ll))
    out = np.zeros(len(c))
    done = np.zeros(len(c), dtype=bool)
    for k in range(1, within + 1):                     # the most recent breakout decides
        rbk, hk, lk = _prev(rb, k, 0.0), _prev(hh, k), _prev(ll, k)
        with np.errstate(invalid="ignore"):
            up = (rbk > 0) & (c < hk) & ~done
            dn = (rbk < 0) & (c > lk) & ~done
        out = np.where(up, -1.0, np.where(dn, 1.0, out))
        done |= (rbk != 0)
    return out


def _min_back(x, n):
    """Lowest value of the previous n bars (not counting this one)."""
    return _roll(_prev(x), n, np.nanmin)


def _max_back(x, n):
    return _roll(_prev(x), n, np.nanmax)


# name -> (function, Lab fact name, plain words)
PATTERNS = {
    "engulfing": (engulfing, "cdl_engulf", "Engulfing bar (+1 bullish, -1 bearish)"),
    "harami": (harami, "cdl_harami", "Harami (+1 bullish, -1 bearish)"),
    "doji": (doji, "cdl_doji", "Doji (1 = open and close almost equal)"),
    "doji_dir": (doji_dir, "cdl_doji_dir", "Dragonfly doji +1 / gravestone doji -1"),
    "hammer": (hammer, "cdl_hammer", "Hammer after a fall +1 / shooting star after a rise -1"),
    "pin_bar": (pin_bar, "cdl_pin", "Pin bar (+1 long lower tail, -1 long upper tail)"),
    "star": (star, "cdl_star", "Morning star +1 / evening star -1"),
    "tweezer": (tweezer, "cdl_tweezer", "Tweezer bottom +1 / tweezer top -1"),
    "inside_bar": (inside_bar, "cdl_inside", "Inside bar (1)"),
    "outside_bar": (outside_bar, "cdl_outside", "Outside bar (+1 closes up, -1 closes down)"),
    "nr7": (nr7, "nr7", "Narrowest bar of the last 7 (1)"),
    "ib_break": (ib_break, "ib_break", "Inside-bar breakout (+1 up, -1 down)"),
    "ib_fakey": (ib_fakey, "ib_fakey", "Inside-bar false breakout: trade the other way (+1 / -1)"),
    "rect_break": (rect_break, "rect_break", "Rectangle (1-hour range) breakout (+1 / -1)"),
    "rect_fail": (rect_fail, "rect_fail", "Failed rectangle breakout: trade the other way (+1 / -1)"),
}


def all_patterns(o, h, l, c) -> dict:
    """{Lab fact name: array} for every pattern. A pattern that fails is left out (the rest still compute)."""
    s = shape(o, h, l, c)
    out = {}
    for name, (fn, fact, _) in PATTERNS.items():
        try:
            out[fact] = np.nan_to_num(fn(s), nan=0.0)
        except Exception:
            out[fact] = np.zeros(len(s["c"]))
    return out


def levels_dist(c, h, l, levels: list) -> tuple:
    """Distance from the close to the nearest key level, in ATRs, and whether that level is below (+1, support) or
    above (-1, resistance). levels: [array or number per bar]. NaN where no level is known."""
    c, h, l = _arr(c, h, l)
    a = atr(h, l, c)
    n = len(c)
    best, side = np.full(n, np.nan), np.full(n, np.nan)
    for lv in levels:
        v = np.full(n, float(lv)) if np.isscalar(lv) else np.asarray(lv, dtype=float)
        with np.errstate(invalid="ignore", divide="ignore"):
            d = np.abs(c - v) / a
        better = np.isfinite(d) & ~(d >= best)
        best = np.where(better, d, best)
        side = np.where(better, np.where(v <= c, 1.0, -1.0), side)
    return best, side


def level_touch(c, h, l, levels: list, near_atr: float = 0.6) -> np.ndarray:
    """+1 where the bar came down to a support level (within `near_atr` ATRs of its low, or through it) and closed
    above it; -1 where it went up to a resistance level and closed below it; 0 otherwise (or both at once).
    This is "the candle formed at the level". levels: [array or number per bar]; NaN levels are ignored."""
    c, h, l = _arr(c, h, l)
    a = atr(h, l, c)
    n = len(c)
    sup, res = np.zeros(n, bool), np.zeros(n, bool)
    for lv in levels:
        v = np.full(n, float(lv)) if np.isscalar(lv) else np.asarray(lv, dtype=float)
        with np.errstate(invalid="ignore"):
            sup |= np.isfinite(v) & (v <= c) & (l - v <= near_atr * a)
            res |= np.isfinite(v) & (v >= c) & (v - h <= near_atr * a)
    return sup.astype(float) - res.astype(float)


def on_last_bar(o, h, l, c) -> list:
    """[(pattern name, value)] for the last closed bar (live)."""
    s = shape(o, h, l, c)
    out = []
    for name, (fn, _, _) in PATTERNS.items():
        try:
            v = float(np.nan_to_num(fn(s), nan=0.0)[-1])
        except Exception:
            continue
        if v:
            out.append((name, v))
    return out


def trade_ideas(o, h, l, c, levels: list, spec: dict, last_only: bool = False, near_atr: float = 0.6) -> list:
    """Candle patterns as trade ideas, the same way live (trial alerts) and on the chart (overlays).

    spec: {pattern name: {"at_level": bool, "bars": n}} from the registry's candle family. at_level patterns count only
    within `near_atr` ATRs of a key level on the right side (support under a bullish candle, resistance over a bearish
    one: level_touch). Entry = the close, stop beyond the pattern's `bars` candles, target 2R.
    Returns [{"i", "name", "d", "entry", "stop", "target", "lvl_atr"}] (lvl_atr: close to the nearest level, in ATRs)."""
    o, h, l, c = _arr(o, h, l, c)
    n = len(c)
    if n < 3 or not spec:
        return []
    s = shape(o, h, l, c)
    dist = levels_dist(c, h, l, levels)[0] if levels else np.full(n, np.nan)
    touch = level_touch(c, h, l, levels, near_atr) if levels else np.zeros(n)
    out = []
    for name, sp in spec.items():
        if name not in PATTERNS:
            continue
        try:
            v = np.nan_to_num(PATTERNS[name][0](s), nan=0.0)
        except Exception:
            continue
        idx = [n - 1] if last_only else range(n)
        for i in idx:
            if not v[i]:
                continue
            d = 1 if v[i] > 0 else -1
            if sp.get("at_level") and touch[i] != d:
                continue
            k = max(1, int(sp.get("bars") or 1))
            lo, hi = max(0, i - k + 1), i + 1
            e = float(c[i])
            stop = float(l[lo:hi].min()) if d > 0 else float(h[lo:hi].max())
            R = abs(e - stop)
            if not R > 0:
                continue
            out.append({"i": int(i), "name": name, "d": d, "entry": e, "stop": stop, "target": e + 2 * R * d,
                        "lvl_atr": float(dist[i]) if np.isfinite(dist[i]) else None})
    out.sort(key=lambda x: x["i"])
    return out
