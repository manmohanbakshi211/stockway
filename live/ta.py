"""Technical indicators on numpy arrays. Pure functions: the same code feeds the chart, live setups and backtests."""
from __future__ import annotations

import math

import numpy as np


def resample(t, o, h, l, c, v, minutes: int, t0: float):
    """1-minute bars -> N-minute bars aligned to the session open t0 (epoch seconds)."""
    if minutes <= 1 or len(t) == 0:
        return t, o, h, l, c, v
    k = ((np.asarray(t) - t0) // (60 * minutes)).astype(int)
    out = {x: [] for x in "tohlcv"}
    start = 0
    for i in range(1, len(k) + 1):
        if i == len(k) or k[i] != k[start]:
            sl = slice(start, i)
            out["t"].append(t0 + k[start] * 60 * minutes)
            out["o"].append(o[start])
            out["h"].append(max(h[sl]))
            out["l"].append(min(l[sl]))
            out["c"].append(c[i - 1])
            out["v"].append(sum(v[sl]))
            start = i
    return tuple(np.asarray(out[x], dtype=float) for x in "tohlcv")


def ema(x, n):
    x = np.asarray(x, dtype=float)
    out = np.full(len(x), np.nan)
    if len(x) == 0:
        return out
    a = 2 / (n + 1)
    s = x[0]
    for i, v in enumerate(x):
        s = v if i == 0 else a * v + (1 - a) * s
        out[i] = s
    return out


def rma(x, n):
    out = np.full(len(x), np.nan)
    s = None
    for i, v in enumerate(x):
        if math.isnan(v):
            continue
        s = v if s is None else (s * (n - 1) + v) / n
        out[i] = s
    return out


def rsi(c, n=14):
    c = np.asarray(c, dtype=float)
    if len(c) < 2:
        return np.full(len(c), np.nan)
    d = np.diff(c, prepend=c[0])
    up, dn = rma(np.maximum(d, 0), n), rma(np.maximum(-d, 0), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = 100 - 100 / (1 + up / dn)
    r[dn == 0] = 100
    r[: min(n, len(r))] = np.nan
    return r


def atr(h, l, c, n=10):
    pc = np.concatenate([[c[0]], c[:-1]]) if len(c) else c
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    return rma(tr, n)


def supertrend(h, l, c, n=10, mult=3.0):
    """Returns (line, direction) with direction +1 up / -1 down."""
    m = len(c)
    line, dirn = np.full(m, np.nan), np.zeros(m)
    if m == 0:
        return line, dirn
    a = atr(h, l, c, n)
    hl2 = (h + l) / 2
    ub, lb = hl2 + mult * a, hl2 - mult * a
    fub, flb = ub.copy(), lb.copy()
    d = 1
    for i in range(m):
        if math.isnan(a[i]):
            continue
        if i > 0 and not math.isnan(fub[i - 1]):
            fub[i] = ub[i] if (ub[i] < fub[i - 1] or c[i - 1] > fub[i - 1]) else fub[i - 1]
            flb[i] = lb[i] if (lb[i] > flb[i - 1] or c[i - 1] < flb[i - 1]) else flb[i - 1]
            if d == 1 and c[i] < flb[i]:
                d = -1
            elif d == -1 and c[i] > fub[i]:
                d = 1
        dirn[i] = d
        line[i] = flb[i] if d == 1 else fub[i]
    return line, dirn


def macd(c, f=12, s=26, sig=9):
    m = ema(c, f) - ema(c, s)
    sg = ema(np.nan_to_num(m), sig)
    return m, sg, m - sg


def vwap_bands(h, l, c, v):
    """Session VWAP with 1 and 2 standard-deviation bands. Without volume (an index), each bar counts equally."""
    tp = (h + l + c) / 3
    w = np.where(np.asarray(v, dtype=float) > 0, v, 1.0) if np.nansum(v) > 0 else np.ones(len(tp))
    cw = np.cumsum(w)
    vw = np.cumsum(tp * w) / cw
    var = np.maximum(np.cumsum(w * tp * tp) / cw - vw * vw, 0)
    sd = np.sqrt(var)
    return vw, vw + sd, vw - sd, vw + 2 * sd, vw - 2 * sd


def cpr(ph, pl, pc):
    """Central pivot range and classic pivots from the previous day's high, low, close."""
    if not (ph and pl and pc):
        return {}
    p = (ph + pl + pc) / 3
    bc = (ph + pl) / 2
    tc = 2 * p - bc
    tc, bc = max(tc, bc), min(tc, bc)
    return {"P": p, "TC": tc, "BC": bc, "R1": 2 * p - pl, "S1": 2 * p - ph, "R2": p + (ph - pl), "S2": p - (ph - pl),
            "width_pct": (tc - bc) / p * 100}


def swing(h, l, i, look=6):
    a = max(0, i - look)
    return float(np.min(l[a:i + 1])), float(np.max(h[a:i + 1]))
