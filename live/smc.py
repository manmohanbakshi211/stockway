"""Smart-money structure on any bars (2.44, gun): swings, break of structure (BOS), change of character (CHoCH),
order blocks, fair value gaps, premium / discount and liquidity sweeps. The same code feeds the Strategy Lab
(lab/factory.py bar_features) and the live app, so a strategy file can test SMC setups (Laksh Fxt SMC guide, the
EMA 8/20 order-block setup, the daily 50% range) on years of history and then run them on the test bench.

No look-ahead: a swing high at bar j (the highest high of bars j-n .. j+n) is only known at bar j+n, and every value
at bar i uses bars up to i. Defaults follow LuxAlgo's idea (internal and swing structure) scaled to one day of
5-minute bars: internal swings n=3, swing structure n=6.

Facts (one value per bar, at its close):
  smc_trend      swing-structure trend: +1 after an up-break, -1 after a down-break, 0 before any break
  smc_brk        +1 / -1 on the bar whose close breaks the last internal swing high / low (BOS or CHoCH), else 0
  smc_choch      +1 / -1 only when that break is against the internal trend so far (change of character), else 0
  smc_pd         where the close sits in the current swing range: 0 = swing low (discount), 0.5 = equilibrium,
                 1 = swing high (premium); below 0 / above 1 when beyond it
  smc_ob         +1 the bar dipped into the latest unbroken bullish order block and closed above its bottom,
                 -1 the mirror for a bearish one, else 0
  smc_ob_recent  +1 / -1 such a touch happened in the last 12 bars (1 hour), else 0
  smc_fvg        +1 the bar dipped into an unfilled bullish fair value gap and closed above it, -1 mirror, else 0
  smc_sweep      +1 the bar's low took out the last internal swing low (or yesterday's low) and closed back above it
                 (liquidity grab below), -1 the mirror above, else 0
"""
from __future__ import annotations

import numpy as np

INTERNAL_N = 3
SWING_N = 6
RECENT_BARS = 12
FACTS = ("smc_trend", "smc_brk", "smc_choch", "smc_pd", "smc_ob", "smc_ob_recent", "smc_fvg", "smc_sweep")


def _confirmed(h, l, n, i):
    """Swing high / low at bar j = i - n if it is the extreme of bars j-n .. j+n (known at bar i). (high, low) or None."""
    j = i - n
    if j - n < 0:
        return None, None
    hs, ls = h[j - n:j + n + 1], l[j - n:j + n + 1]
    sh = h[j] if h[j] >= np.max(hs) else None
    sl = l[j] if l[j] <= np.min(ls) else None
    return sh, sl


def structure(o, h, l, c, prev_high=None, prev_low=None, n_int: int = INTERNAL_N, n_sw: int = SWING_N) -> dict:
    o, h, l, c = (np.asarray(x, float) for x in (o, h, l, c))
    n = len(c)
    out = {k: np.zeros(n) for k in FACTS}
    out["smc_pd"] = np.full(n, np.nan)
    if not n:
        return out
    rng = np.maximum(h - l, 1e-9)
    # internal level: breaks, CHoCH, order blocks, sweeps
    ish = isl = None                      # (price, index, broken?)
    i_trend = 0
    bull_ob = bear_ob = None              # (bottom, top)
    fvgs: list = []                       # [dir, bottom, top]
    last_touch = {1: -10 ** 9, -1: -10 ** 9}
    # swing level: trend and range for premium / discount
    ssh = ssl = None
    s_trend = 0
    for i in range(n):
        sh, sl = _confirmed(h, l, n_int, i)
        if sh is not None:
            ish = [sh, i - n_int, False]
        if sl is not None:
            isl = [sl, i - n_int, False]
        wh, wl = _confirmed(h, l, n_sw, i)
        if wh is not None:
            ssh = [wh, i - n_sw, False]
        if wl is not None:
            ssl = [wl, i - n_sw, False]
        # liquidity sweep of the last internal swing (or yesterday's level), closing back inside
        lows = [x for x in ((isl[0] if isl else None), prev_low) if x]
        highs = [x for x in ((ish[0] if ish else None), prev_high) if x]
        if any(l[i] < x < c[i] for x in lows):
            out["smc_sweep"][i] = 1.0
        elif any(c[i] < x < h[i] for x in highs):
            out["smc_sweep"][i] = -1.0
        # internal break of structure / change of character, and the order block it leaves
        if ish and not ish[2] and c[i] > ish[0]:
            ish[2] = True
            out["smc_brk"][i] = 1.0
            if i_trend == -1:
                out["smc_choch"][i] = 1.0
            i_trend = 1
            start = isl[1] if isl else max(0, i - 2 * n_int)
            downs = [j for j in range(start, i) if c[j] < o[j]]
            j = downs[-1] if downs else int(start + np.argmin(l[start:i])) if i > start else None
            if j is not None:
                bull_ob = (float(l[j]), float(h[j]))
        elif isl and not isl[2] and c[i] < isl[0]:
            isl[2] = True
            out["smc_brk"][i] = -1.0
            if i_trend == 1:
                out["smc_choch"][i] = -1.0
            i_trend = -1
            start = ish[1] if ish else max(0, i - 2 * n_int)
            ups = [j for j in range(start, i) if c[j] > o[j]]
            j = ups[-1] if ups else int(start + np.argmax(h[start:i])) if i > start else None
            if j is not None:
                bear_ob = (float(l[j]), float(h[j]))
        # order block touches (an order block is gone once price trades through it)
        if bull_ob and out["smc_brk"][i] != 1.0:
            if l[i] < bull_ob[0]:
                bull_ob = None
            elif l[i] <= bull_ob[1] and c[i] > bull_ob[0]:
                out["smc_ob"][i] = 1.0
                last_touch[1] = i
        if bear_ob and out["smc_brk"][i] != -1.0:
            if h[i] > bear_ob[1]:
                bear_ob = None
            elif h[i] >= bear_ob[0] and c[i] < bear_ob[1]:
                out["smc_ob"][i] = -1.0 if out["smc_ob"][i] == 0 else out["smc_ob"][i]
                last_touch[-1] = i
        age_b, age_s = i - last_touch[1], i - last_touch[-1]
        if age_b <= RECENT_BARS and age_b <= age_s:
            out["smc_ob_recent"][i] = 1.0
        elif age_s <= RECENT_BARS:
            out["smc_ob_recent"][i] = -1.0
        # fair value gaps: a 3-bar gap bigger than a quarter of the average bar; filled once price crosses it
        avg = float(np.mean(rng[max(0, i - 20):i + 1]))
        for g in fvgs:
            if g[0] > 0 and l[i] <= g[2] and c[i] > g[1]:
                out["smc_fvg"][i] = 1.0
            elif g[0] < 0 and h[i] >= g[1] and c[i] < g[2]:
                out["smc_fvg"][i] = -1.0 if out["smc_fvg"][i] == 0 else out["smc_fvg"][i]
        fvgs = [g for g in fvgs if not ((g[0] > 0 and l[i] < g[1]) or (g[0] < 0 and h[i] > g[2]))]
        if i >= 2:
            if l[i] > h[i - 2] and l[i] - h[i - 2] > 0.25 * avg:
                fvgs.append([1, float(h[i - 2]), float(l[i])])
            elif h[i] < l[i - 2] and l[i - 2] - h[i] > 0.25 * avg:
                fvgs.append([-1, float(h[i]), float(l[i - 2])])
            fvgs = fvgs[-3:]
        # swing trend and premium / discount
        if ssh and not ssh[2] and c[i] > ssh[0]:
            ssh[2], s_trend = True, 1
        elif ssl and not ssl[2] and c[i] < ssl[0]:
            ssl[2], s_trend = True, -1
        out["smc_trend"][i] = s_trend
        top = max(x for x in (ssh[0] if ssh else None, ish[0] if ish else None) if x is not None) if (ssh or ish) else None
        bot = min(x for x in (ssl[0] if ssl else None, isl[0] if isl else None) if x is not None) if (ssl or isl) else None
        if top is not None and bot is not None and top > bot:
            out["smc_pd"][i] = (c[i] - bot) / (top - bot)
    return out


def last_swings(h, l, n: int = INTERNAL_N):
    """(lows, highs): the last confirmed internal swing low / high known at each bar (NaN before the first one).
    Used for 'stop beyond the last swing' exits (lab/exits.py "swing")."""
    h, l = np.asarray(h, float), np.asarray(l, float)
    m = len(h)
    lo, hi = np.full(m, np.nan), np.full(m, np.nan)
    cur_lo = cur_hi = np.nan
    for i in range(m):
        sh, sl = _confirmed(h, l, n, i)
        if sh is not None:
            cur_hi = sh
        if sl is not None:
            cur_lo = sl
        lo[i], hi[i] = cur_lo, cur_hi
    return lo, hi
