"""Chart pattern engine: reads a chart the way a trader does, with exact rules so every pattern can be drawn,
graded live and tested on years of history.

Swing points come from a zig-zag that only counts a turn when price reverses by at least `k` x ATR, so noise is
ignored and the same swings appear on any instrument or timeframe. Patterns are built from those swings:

  double_top / double_bottom     two equal highs (lows) with a real dip (rally) between; confirmed on a close
                                 through the neckline. Target: the pattern's height from the neckline.
  head_shoulders / inv_hs        three highs (lows), the middle one extreme, shoulders similar; confirmed on a close
                                 through the neckline drawn through the two troughs (peaks).
  triangle                       converging highs and lows over 4+ swings (ascending, descending or symmetric);
                                 confirmed on a close outside. Target: the triangle's opening height.
  flag                           a sharp move (the pole), then a tight drift against it; confirmed on a close beyond
                                 the flag in the pole's direction. Target: the pole's length.
  breakout_retest                a close through a key level, a pull-back to it that holds, and a close away again.
  rsi_divergence                 price makes a lower low (higher high) but RSI does not; confirmed when the bar
                                 after the swing closes in the new direction.
  candle_at_level                hammer / engulfing at support, shooting star / engulfing at resistance.

Every pattern returns entry, stop and target (so reward:risk is known), the lines to draw, and plain words.
"""
from __future__ import annotations


import numpy as np

from . import ta

NAMES = {"double_top": "Double top", "double_bottom": "Double bottom", "head_shoulders": "Head & shoulders",
         "inv_hs": "Inverse head & shoulders", "triangle": "Triangle breakout", "flag": "Flag breakout",
         "breakout_retest": "Breakout + retest", "rsi_divergence": "RSI divergence", "candle_at_level": "Reversal candle at level"}


def pivots(h, l, atr, k=1.2):
    """Zig-zag swing points [(index, price, 'H'|'L')]. A swing is confirmed once price reverses k x ATR from it.
    The last, still-unconfirmed extreme is returned separately."""
    n = len(h)
    if n < 3:
        return [], None
    piv = []
    mode = 0                      # 1 looking for a high, -1 for a low, 0 undecided
    hi_i, lo_i = 0, 0
    for i in range(1, n):
        a = atr[i] if atr[i] == atr[i] and atr[i] > 0 else (h[i] - l[i]) or 1e-9
        thr = k * a
        if h[i] > h[hi_i]:
            hi_i = i
        if l[i] < l[lo_i]:
            lo_i = i
        if mode >= 0 and h[hi_i] - l[i] >= thr and hi_i < i and (mode == 1 or hi_i >= lo_i):
            piv.append((hi_i, float(h[hi_i]), "H"))
            mode, lo_i = -1, i
            for j in range(hi_i, i + 1):
                if l[j] < l[lo_i]:
                    lo_i = j
        elif mode <= 0 and h[i] - l[lo_i] >= thr and lo_i < i and (mode == -1 or lo_i >= hi_i):
            piv.append((lo_i, float(l[lo_i]), "L"))
            mode, hi_i = 1, i
            for j in range(lo_i, i + 1):
                if h[j] > h[hi_i]:
                    hi_i = j
    last = (hi_i, float(h[hi_i]), "H") if mode == 1 else (lo_i, float(l[lo_i]), "L") if mode == -1 else None
    return piv, last


def _pat(name, d, i0, i1, entry, stop, target, lines, why, level=None, state="confirmed", sub=None):
    risk = abs(entry - stop)
    if risk <= 0 or (d > 0 and not (stop < entry < target)) or (d < 0 and not (target < entry < stop)):
        return None
    return {"pattern": name, "name": NAMES[name] + (f" ({sub})" if sub else ""), "direction": "up" if d > 0 else "down",
            "state": state, "i0": int(i0), "i1": int(i1), "entry": float(entry), "stop": float(stop), "target": float(target),
            "rr": round(float(abs(target - entry) / risk), 2), "level": None if level is None else float(level), "lines": lines, "why": why}


def _line_at(p1, p2, x):
    (x1, y1), (x2, y2) = p1, p2
    return y1 + (y2 - y1) * (x - x1) / (x2 - x1) if x2 != x1 else y1


def detect(b: dict, levels: list | None = None, recent: int = 3, k: float = 1.2) -> list:
    """Patterns completed on one of the last `recent` closed bars. b: arrays o,h,l,c (closed bars only).
    levels: [(name, price), ...] key levels (yesterday's high/low, CPR, OI walls, ...)."""
    o, h, l, c = (np.asarray(b[x], dtype=float) for x in ("o", "h", "l", "c"))
    n = len(c)
    if n < 8:
        return []
    atr = ta.atr(h, l, c, 10)
    atr = np.where(np.isnan(atr), np.nanmean(h - l) if n else 1.0, atr)
    piv, last = pivots(h, l, atr, k)
    rsi = ta.rsi(c, 14)
    out = []
    i = n - 1
    a = float(atr[i])
    tol = 0.5 * a

    def fresh(j):                 # pattern confirmed on bar j, which must be one of the last `recent` bars
        return j >= n - recent

    def first_close(start, cond):
        """First bar index >= start whose close satisfies cond (the confirmation bar), else None."""
        for j in range(start, n):
            if cond(j):
                return j
        return None

    H = [p for p in piv if p[2] == "H"]
    L = [p for p in piv if p[2] == "L"]
    seq = piv[-6:]

    # ---- double top / bottom: H L H (or L H L) with the last swing being the second top
    if len(seq) >= 3:
        x, y, z = seq[-3], seq[-2], seq[-1]
        if x[2] == "H" and y[2] == "L" and z[2] == "H" and abs(x[1] - z[1]) <= tol and min(x[1], z[1]) - y[1] >= 1.5 * a:
            neck = y[1]
            j = first_close(z[0] + 1, lambda j: c[j] < neck)
            if j is not None and fresh(j):
                top = max(x[1], z[1])
                p = _pat("double_top", -1, x[0], j, c[j], top + 0.1 * a, neck - (top - neck),
                         [[(x[0], x[1]), (z[0], z[1])], [(y[0], neck), (j, neck)]], f"two highs near {top:,.1f}, closed below the neckline {neck:,.1f}", neck)
                p and out.append(p)
        if x[2] == "L" and y[2] == "H" and z[2] == "L" and abs(x[1] - z[1]) <= tol and y[1] - max(x[1], z[1]) >= 1.5 * a:
            neck = y[1]
            j = first_close(z[0] + 1, lambda j: c[j] > neck)
            if j is not None and fresh(j):
                bot = min(x[1], z[1])
                p = _pat("double_bottom", 1, x[0], j, c[j], bot - 0.1 * a, neck + (neck - bot),
                         [[(x[0], x[1]), (z[0], z[1])], [(y[0], neck), (j, neck)]], f"two lows near {bot:,.1f}, closed above the neckline {neck:,.1f}", neck)
                p and out.append(p)

    # ---- head & shoulders: H L H L H with the middle high highest
    if len(seq) >= 5:
        s1, t1, hd, t2, s2 = seq[-5:]
        if [s1[2], t1[2], hd[2], t2[2], s2[2]] == ["H", "L", "H", "L", "H"] and hd[1] > max(s1[1], s2[1]) + 0.5 * a \
                and abs(s1[1] - s2[1]) <= 1.0 * a:
            j = first_close(s2[0] + 1, lambda j: c[j] < _line_at((t1[0], t1[1]), (t2[0], t2[1]), j))
            if j is not None and fresh(j):
                nk = _line_at((t1[0], t1[1]), (t2[0], t2[1]), j)
                height = hd[1] - _line_at((t1[0], t1[1]), (t2[0], t2[1]), hd[0])
                p = _pat("head_shoulders", -1, s1[0], j, c[j], s2[1] + 0.1 * a, nk - height,
                         [[(s1[0], s1[1]), (t1[0], t1[1]), (hd[0], hd[1]), (t2[0], t2[1]), (s2[0], s2[1])], [(t1[0], t1[1]), (j, nk)]],
                         f"head at {hd[1]:,.1f}, shoulders near {s1[1]:,.1f}/{s2[1]:,.1f}; closed below the neckline", nk)
                p and out.append(p)
        if [s1[2], t1[2], hd[2], t2[2], s2[2]] == ["L", "H", "L", "H", "L"] and hd[1] < min(s1[1], s2[1]) - 0.5 * a \
                and abs(s1[1] - s2[1]) <= 1.0 * a:
            j = first_close(s2[0] + 1, lambda j: c[j] > _line_at((t1[0], t1[1]), (t2[0], t2[1]), j))
            if j is not None and fresh(j):
                nk = _line_at((t1[0], t1[1]), (t2[0], t2[1]), j)
                height = _line_at((t1[0], t1[1]), (t2[0], t2[1]), hd[0]) - hd[1]
                p = _pat("inv_hs", 1, s1[0], j, c[j], s2[1] - 0.1 * a, nk + height,
                         [[(s1[0], s1[1]), (t1[0], t1[1]), (hd[0], hd[1]), (t2[0], t2[1]), (s2[0], s2[1])], [(t1[0], t1[1]), (j, nk)]],
                         f"head at {hd[1]:,.1f}, shoulders near {s1[1]:,.1f}/{s2[1]:,.1f}; closed above the neckline", nk)
                p and out.append(p)

    # ---- triangle: last two highs falling or flat, last two lows rising or flat, converging
    if len(H) >= 2 and len(L) >= 2:
        h1, h2 = H[-2], H[-1]
        l1, l2 = L[-2], L[-1]
        start = min(h1[0], l1[0])
        if h2[1] <= h1[1] + 0.2 * a and l2[1] >= l1[1] - 0.2 * a and (h1[1] - l1[1]) > (h2[1] - l2[1]) + 0.3 * a \
                and max(h2[0], l2[0]) < n - 1:
            last_sw = max(h2[0], l2[0])
            up_line = lambda j: _line_at((h1[0], h1[1]), (h2[0], h2[1]), j)
            dn_line = lambda j: _line_at((l1[0], l1[1]), (l2[0], l2[1]), j)
            flat_top = abs(h2[1] - h1[1]) <= 0.2 * a
            flat_bot = abs(l2[1] - l1[1]) <= 0.2 * a
            sub = "ascending" if flat_top and not flat_bot else "descending" if flat_bot and not flat_top else "symmetric"
            height = h1[1] - l1[1]
            j = first_close(last_sw + 1, lambda j: c[j] > up_line(j) or c[j] < dn_line(j))
            if j is not None and fresh(j) and dn_line(j) < up_line(j):
                d = 1 if c[j] > up_line(j) else -1
                stop = dn_line(j) if d > 0 else up_line(j)
                p = _pat("triangle", d, start, j, c[j], stop, c[j] + d * height,
                         [[(h1[0], h1[1]), (j, up_line(j))], [(l1[0], l1[1]), (j, dn_line(j))]],
                         f"{sub} triangle ({height:,.1f} pts tall) broke {'up' if d > 0 else 'down'}", up_line(j) if d > 0 else dn_line(j), sub=sub)
                p and out.append(p)

    # ---- flag: pole of >= 3 ATR within 6 bars, then 3-10 bars drifting back <= 50% of the pole
    for d in (1, -1):
        for fl in range(3, 11):
            j = n - 1                                    # breakout bar = the last closed bar
            f0 = j - fl                                  # flag occupies f0 .. j-1
            p0 = f0 - 6
            if p0 < 0:
                break
            seg_h, seg_l = h[f0:j].max(), l[f0:j].min()
            if d > 0:
                pole = h[f0] - l[p0:f0 + 1].min()
                ok = pole >= 3 * a and (seg_h - seg_l) <= 0.5 * pole and c[j] > seg_h and c[j - 1] <= seg_h and seg_l > h[f0] - 0.6 * pole
            else:
                pole = h[p0:f0 + 1].max() - l[f0]
                ok = pole >= 3 * a and (seg_h - seg_l) <= 0.5 * pole and c[j] < seg_l and c[j - 1] >= seg_l and seg_h < l[f0] + 0.6 * pole
            if ok:
                stop = seg_l if d > 0 else seg_h
                p = _pat("flag", d, p0, j, c[j], stop, c[j] + d * pole,
                         [[(f0, seg_h), (j - 1, seg_h)], [(f0, seg_l), (j - 1, seg_l)]],
                         f"{pole:,.1f}-pt {'rally' if d > 0 else 'drop'}, {fl}-bar pause, then a close beyond it", seg_h if d > 0 else seg_l)
                if p:
                    out.append(p)
                    break

    # ---- breakout + retest of a key level (on the last bar)
    for name, lv in (levels or []):
        if not lv or i < 4:
            continue
        for d in (1, -1):
            look = range(max(1, i - 8), i - 1)
            brk = [j for j in look if (c[j] - lv) * d > 0.2 * a and (c[j - 1] - lv) * d <= 0]
            if not brk:
                continue
            j0 = brk[-1]
            touched = any(((l[x] if d > 0 else h[x]) - lv) * d <= 0.3 * a for x in range(j0 + 1, i))
            held = all((c[x] - lv) * d > -0.2 * a for x in range(j0, i + 1))
            if touched and held and (c[i] - c[i - 1]) * d > 0 and (c[i] - lv) * d > 0.2 * a:
                risk_lv = lv - d * 0.5 * a
                p = _pat("breakout_retest", d, j0, i, c[i], risk_lv, c[i] + d * 2 * abs(c[i] - risk_lv),
                         [[(j0, lv), (i, lv)]], f"broke {name} {lv:,.1f}, came back to test it, held, and moved away", lv)
                p and out.append(p)

    # ---- RSI divergence between the last two swing lows (highs)
    if len(L) >= 2:
        a1, a2 = L[-2], L[-1]
        if a2[1] < a1[1] - 0.2 * a and rsi[a2[0]] == rsi[a2[0]] and rsi[a1[0]] == rsi[a1[0]] and rsi[a2[0]] > rsi[a1[0]] + 3 \
                and a2[0] >= n - 1 - 6:
            j = first_close(a2[0] + 1, lambda j: c[j] > o[j] and c[j] > c[j - 1])
            if j is not None and fresh(j):
                p = _pat("rsi_divergence", 1, a1[0], j, c[j], a2[1] - 0.1 * a, c[j] + 2 * (c[j] - a2[1] + 0.1 * a),
                         [[(a1[0], a1[1]), (a2[0], a2[1])]], f"lower low {a2[1]:,.1f} but RSI higher ({rsi[a1[0]]:.0f} → {rsi[a2[0]]:.0f})", a2[1], sub="bullish")
                p and out.append(p)
    if len(H) >= 2:
        a1, a2 = H[-2], H[-1]
        if a2[1] > a1[1] + 0.2 * a and rsi[a2[0]] == rsi[a2[0]] and rsi[a1[0]] == rsi[a1[0]] and rsi[a2[0]] < rsi[a1[0]] - 3 \
                and a2[0] >= n - 1 - 6:
            j = first_close(a2[0] + 1, lambda j: c[j] < o[j] and c[j] < c[j - 1])
            if j is not None and fresh(j):
                p = _pat("rsi_divergence", -1, a1[0], j, c[j], a2[1] + 0.1 * a, c[j] - 2 * (a2[1] + 0.1 * a - c[j]),
                         [[(a1[0], a1[1]), (a2[0], a2[1])]], f"higher high {a2[1]:,.1f} but RSI lower ({rsi[a1[0]]:.0f} → {rsi[a2[0]]:.0f})", a2[1], sub="bearish")
                p and out.append(p)

    # ---- reversal candle at a level (last bar)
    body = abs(c[i] - o[i])
    rng = h[i] - l[i]
    if rng > 0.4 * a:
        lower = min(o[i], c[i]) - l[i]
        upper = h[i] - max(o[i], c[i])
        sup = [(nm, lv) for nm, lv in (levels or []) if lv and abs(l[i] - lv) <= 0.3 * a] + [("swing low", p[1]) for p in L[-3:] if abs(l[i] - p[1]) <= 0.3 * a and p[0] < i - 2]
        res = [(nm, lv) for nm, lv in (levels or []) if lv and abs(h[i] - lv) <= 0.3 * a] + [("swing high", p[1]) for p in H[-3:] if abs(h[i] - p[1]) <= 0.3 * a and p[0] < i - 2]
        hammer = lower >= 2 * max(body, 0.1 * a) and upper <= 0.6 * max(body, 0.1 * a) and c[i] >= l[i] + 0.6 * rng
        bull_eng = c[i] > o[i] and c[i - 1] < o[i - 1] and c[i] >= o[i - 1] and o[i] <= c[i - 1] and body > abs(c[i - 1] - o[i - 1])
        star = upper >= 2 * max(body, 0.1 * a) and lower <= 0.6 * max(body, 0.1 * a) and c[i] <= h[i] - 0.6 * rng
        bear_eng = c[i] < o[i] and c[i - 1] > o[i - 1] and c[i] <= o[i - 1] and o[i] >= c[i - 1] and body > abs(c[i - 1] - o[i - 1])
        if sup and (hammer or bull_eng):
            nm, lv = sup[0]
            p = _pat("candle_at_level", 1, i - 1, i, c[i], l[i] - 0.1 * a, c[i] + 2 * (c[i] - l[i] + 0.1 * a), [[(i - 4, lv), (i, lv)]],
                     f"{'hammer' if hammer else 'bullish engulfing'} at {nm} {lv:,.1f}", lv, sub="hammer" if hammer else "engulfing")
            p and out.append(p)
        if res and (star or bear_eng):
            nm, lv = res[0]
            p = _pat("candle_at_level", -1, i - 1, i, c[i], h[i] + 0.1 * a, c[i] - 2 * (h[i] + 0.1 * a - c[i]), [[(i - 4, lv), (i, lv)]],
                     f"{'shooting star' if star else 'bearish engulfing'} at {nm} {lv:,.1f}", lv, sub="shooting star" if star else "engulfing")
            p and out.append(p)
    return out


def swings_for_chart(b: dict, k: float = 1.2) -> list:
    """Confirmed swing points (for drawing the zig-zag and labelling HH/HL/LH/LL)."""
    h, l, c = (np.asarray(b[x], dtype=float) for x in ("h", "l", "c"))
    if len(c) < 8:
        return []
    atr = ta.atr(h, l, c, 10)
    atr = np.where(np.isnan(atr), np.nanmean(h - l), atr)
    piv, _ = pivots(h, l, atr, k)
    out = []
    last = {"H": None, "L": None}
    for i, p, t in piv:
        prev = last[t]
        lab = None
        if prev is not None:
            lab = ("HH" if p > prev else "LH") if t == "H" else ("HL" if p > prev else "LL")
        out.append({"i": i, "price": p, "type": t, "label": lab})
        last[t] = p
    return out


def structure(sw: list) -> str | None:
    """Market structure from the last swings: 'uptrend' (HH + HL), 'downtrend' (LH + LL), or 'range'."""
    labs = [s["label"] for s in sw[-4:] if s["label"]]
    if not labs:
        return None
    up = sum(x in ("HH", "HL") for x in labs)
    dn = sum(x in ("LH", "LL") for x in labs)
    return "uptrend" if up >= 3 else "downtrend" if dn >= 3 else "range"
