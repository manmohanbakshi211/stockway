"""Price-action facts and level stops (2.68): gun pieces for the Ghanshyam option-buying course.

Mandeep, 11 Nov 2026: read the course, find what can be a check, a strategy, a support / resistance idea.
Gun (here, generic, no thresholds from the course): how far price has fallen / risen from today's open, a hammer or
shooting star at today's extreme confirmed by a close beyond it, the morning move, where the 14:15-14:20 candle sits in
the early-afternoon range, how much of today's biggest fall / rise has been taken back (Fibonacci-style), and the stop
levels those setups use (the 2:20 candle, the 0.38 retracement, the hammer / star candle).
Bullets (strategy files, e.g. data/packs ghanshyam_course.json): the thresholds (a 0.3% pull back, the 0.71 level,
against the morning move, not in the middle of the range), the exits and the trial.

Every fact is computed from today's 5-minute bars up to the bar itself (nothing from later bars). Bar i closes at
09:15 + 5*(i+1) minutes: bar 44 closes at 13:00, bar 59 at 14:15, bar 60 (the "2:20 candle") at 14:20.
"""
from __future__ import annotations

import numpy as np

FACTS = {
    "open_fall": ("fall from today's open to the lowest price so far", "%"),
    "open_rise": ("rise from today's open to the highest price so far", "%"),
    "hs_conf": ("hammer at today's low confirmed by a close above its high within 3 bars (+1); shooting star at "
                "today's high confirmed by a close below its low (-1); 0 otherwise", ""),
    "am_move": ("the morning move: today's open to the 13:00 close (known from 13:00)", "%"),
    "pm_rng": ("where the 14:15-14:20 candle's middle sits in the 13:00-14:15 range (0 its low, 1 its high, "
               "below 0 / above 1 outside)", ""),
    "pm_edge": ("how far the 14:15-14:20 candle is from the middle of the 13:00-14:15 range (0 the middle, 1 its "
                "edge, above 1 outside)", ""),
    "fib_dday": ("how much of the fall from today's high has been taken back (0 at the low after the high, "
                 "1 back at the high)", ""),
    "fall_day": ("size of that fall: today's high to the low after it", "%"),
    "fib_uday": ("how much of the rise from today's low has been given back (0 at the high after the low, "
                 "1 back at the low)", ""),
    "rise_day": ("size of that rise: today's low to the high after it", "%"),
    # 2.69 (course fixes): the 2:20 candle against the 4-5 candles just before it; the level a bar breaks or nears
    "pm_edge5": ("how far the 14:15-14:20 candle is from the middle of the 5 candles before it (0 middle, 1 edge, "
                 "above 1 outside)", ""),
    "lv_brk": ("close above today's high so far (+1) / below today's low so far (-1), 0 neither", ""),
    "lv_touches": ("how many separate times price touched that level (the one broken, else the nearer of today's "
                   "high / low) before this bar", ""),
    "lv_pause": ("candles in a row just before this one that waited near that level (within 0.15%)", ""),
    "lv_run": ("how far price ran into that level in the hour before the wait (the 'journey')", "%"),
}
# level stops (lab/exits.py): (stop for a buy, stop for a sell) on each bar
LEVELS = {
    "pm": "stop at the other side of the 14:15-14:20 candle",
    "fib38": "stop at the 0.38 retracement of the move (buy: of today's fall; sell: of today's rise)",
    "sig": "stop beyond the confirmed hammer's low (buy) / shooting star's high (sell)",
}

# the facts' fixed definitions (gun: what a hammer is and where the 2:20 candle is; changing them changes every table)
AM_BAR = 44            # closes at 13:00
PM_FROM, PM_BAR = 45, 60   # 13:00-14:15 range, the 14:15-14:20 candle
WICK_X_BODY = 2.0      # the long wick at least twice the body
OTHER_WICK = 0.25      # the other wick at most the body or a quarter of the range
MIN_RANGE = 0.0005     # a candle of at least 0.05% of the open
AT_EXTREME = 0.0003    # at today's low / high so far, within 0.03%
CONFIRM = 3            # the confirming close within 3 bars
FIB_STOP = 0.38
PM5_FROM = 55          # 13:50-14:15: the 5 candles before the 2:20 candle
TOUCH = 0.0005         # a touch: within 0.05% of the level
NEAR = 0.0015          # waiting near the level: within 0.15%
RUN_BARS = 12          # the journey: the 12 candles (1 hour) before the wait


def _nan(n):
    return np.full(n, np.nan)


def hammer_star(o, h, l, c):
    """(+1 / -1 on the confirming bar, the confirmed hammer's low so far, the confirmed star's high so far)."""
    n = len(c)
    out, sig_lo, sig_hi = np.zeros(n), _nan(n), _nan(n)
    if n < 2:
        return out, sig_lo, sig_hi
    px = o[0]
    pend = []                                    # (candle bar, side)
    lo_now = hi_now = np.nan
    run_lo, run_hi = np.inf, -np.inf             # today's low / high before bar j
    for j in range(n):
        up = dn = False
        keep = []
        for i, side in pend:
            if j - i > CONFIRM:
                continue
            if side > 0 and c[j] > h[i]:
                up, lo_now = True, l[i]
            elif side < 0 and c[j] < l[i]:
                dn, hi_now = True, h[i]
            else:
                keep.append((i, side))
        pend = keep
        out[j] = 1.0 if up and not dn else -1.0 if dn and not up else 0.0
        sig_lo[j], sig_hi[j] = lo_now, hi_now
        rng = h[j] - l[j]
        if j > 0 and rng >= MIN_RANGE * px:
            body = abs(c[j] - o[j])
            lw, uw = min(o[j], c[j]) - l[j], h[j] - max(o[j], c[j])
            if lw >= WICK_X_BODY * body and uw <= max(body, OTHER_WICK * rng) and l[j] <= run_lo + AT_EXTREME * px:
                pend.append((j, 1))
            elif uw >= WICK_X_BODY * body and lw <= max(body, OTHER_WICK * rng) and h[j] >= run_hi - AT_EXTREME * px:
                pend.append((j, -1))
        run_lo, run_hi = min(run_lo, l[j]), max(run_hi, h[j])
    return out, sig_lo, sig_hi


def swings(h, l, c):
    """Today's biggest fall from the high and rise from the low, from the bars before each bar.
    Returns fib_dday, fall_day, fib_uday, rise_day and the 0.38 stop levels (buy, sell)."""
    n = len(c)
    fd, fs, fu, rs, s_buy, s_sell = (_nan(n) for _ in range(6))
    H, L = -np.inf, np.inf                       # the high so far and the low after it
    Lo, Hi = np.inf, -np.inf                     # the low so far and the high after it
    for j in range(1, n):
        if h[j - 1] > H:
            H, L = h[j - 1], l[j - 1]
        else:
            L = min(L, l[j - 1])
        if l[j - 1] < Lo:
            Lo, Hi = l[j - 1], h[j - 1]
        else:
            Hi = max(Hi, h[j - 1])
        if H > L:
            fd[j] = (c[j] - L) / (H - L)
            fs[j] = (H - L) / H * 100
            s_buy[j] = L + FIB_STOP * (H - L)
        if Hi > Lo:
            fu[j] = (Hi - c[j]) / (Hi - Lo)
            rs[j] = (Hi - Lo) / Lo * 100
            s_sell[j] = Hi - FIB_STOP * (Hi - Lo)
    return fd, fs, fu, rs, s_buy, s_sell


def facts(o, h, l, c) -> dict:
    """Arrays (one value per 5-minute bar) of every fact in FACTS."""
    o, h, l, c = (np.asarray(x, float) for x in (o, h, l, c))
    n = len(c)
    f = {k: _nan(n) for k in FACTS}
    if not n:
        return f
    f["open_fall"] = (o[0] - np.minimum.accumulate(l)) / o[0] * 100
    f["open_rise"] = (np.maximum.accumulate(h) - o[0]) / o[0] * 100
    f["hs_conf"], _, _ = hammer_star(o, h, l, c)
    if n > AM_BAR:
        f["am_move"][AM_BAR:] = (c[AM_BAR] / o[0] - 1) * 100
    if n > PM_BAR:
        ah, al = h[PM_FROM:PM_BAR].max(), l[PM_FROM:PM_BAR].min()
        if ah > al:
            pos = ((h[PM_BAR] + l[PM_BAR]) / 2 - al) / (ah - al)
            f["pm_rng"][PM_BAR:] = pos
            f["pm_edge"][PM_BAR:] = abs(pos - 0.5) * 2
    f["fib_dday"], f["fall_day"], f["fib_uday"], f["rise_day"], _, _ = swings(h, l, c)
    if n > PM_BAR:
        ah, al = h[PM5_FROM:PM_BAR].max(), l[PM5_FROM:PM_BAR].min()
        if ah > al:
            f["pm_edge5"][PM_BAR:] = abs(((h[PM_BAR] + l[PM_BAR]) / 2 - al) / (ah - al) - 0.5) * 2
    f["lv_brk"], f["lv_touches"], f["lv_pause"], f["lv_run"] = level_facts(h, l, c)
    return f


def level_facts(h, l, c):
    """For each bar: does it close beyond today's high / low so far, and for that level (or the nearer one): how many
    separate touches before, how many candles waited near it just before, and how far price ran into it."""
    n = len(c)
    brk, tch, pau, run = np.zeros(n), _nan(n), _nan(n), _nan(n)
    brk[0] = np.nan
    for i in range(1, n):
        R, S = h[:i].max(), l[:i].min()
        if c[i] > R:
            brk[i], lv, up = 1.0, R, True
        elif c[i] < S:
            brk[i], lv, up = -1.0, S, False
        else:
            up = (R - c[i]) <= (c[i] - S)
            lv = R if up else S
        hit = h[:i] >= lv * (1 - TOUCH) if up else l[:i] <= lv * (1 + TOUCH)
        tch[i] = float(hit[0]) + float(np.sum(hit[1:] & ~hit[:-1]))
        k = 0
        while k < i and k < RUN_BARS and ((h[i - 1 - k] >= lv * (1 - NEAR)) if up else (l[i - 1 - k] <= lv * (1 + NEAR))):
            k += 1
        pau[i] = float(k)
        a, b = max(0, i - k - RUN_BARS), i - k
        if b > a:
            run[i] = ((lv - l[a:b].min()) if up else (h[a:b].max() - lv)) / lv * 100
    return brk, tch, pau, run


def levels(o, h, l, c) -> dict:
    """{stop kind: (array of buy stops, array of sell stops)} for the level stops in LEVELS."""
    o, h, l, c = (np.asarray(x, float) for x in (o, h, l, c))
    n = len(c)
    pm_lo, pm_hi = _nan(n), _nan(n)
    if n > PM_BAR:
        pm_lo[PM_BAR:], pm_hi[PM_BAR:] = l[PM_BAR], h[PM_BAR]
    _, sig_lo, sig_hi = hammer_star(o, h, l, c)
    *_, s_buy, s_sell = swings(h, l, c)
    return {"pm": (pm_lo, pm_hi), "fib38": (s_buy, s_sell), "sig": (sig_lo, sig_hi)}
