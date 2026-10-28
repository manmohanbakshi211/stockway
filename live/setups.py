"""Classic intraday setups as exact rules, evaluated on each closed 5-minute bar.

Every signal has an entry, a stop (where the idea is wrong) and a target, so it can be graded live and tested on
history with the same code (lab/strategies.py uses evaluate()).

  ema_pullback  trend day (EMA 9 > 21, Supertrend up, above VWAP): dip into EMA21/VWAP that closes back up
  vwap_reclaim  30+ minutes below VWAP, then a strong close back above it with RSI > 50 (mirror for shorts)
  cpr_breakout  narrow CPR day (trend day likely): close beyond the CPR and the opening range
  pdh_break     first close above yesterday's high (or below yesterday's low) with the trend
  wall_fade     rejection at the biggest call/put OI wall with stretched RSI (mean reversion)
"""
from __future__ import annotations

import numpy as np

from . import ta

NAMES = {"ema_pullback": "EMA pullback", "vwap_reclaim": "VWAP reclaim", "cpr_breakout": "CPR breakout",
         "pdh_break": "Previous-day high/low break", "wall_fade": "OI wall rejection"}


def indicators(b: dict) -> dict:
    """b: 5-minute arrays t,o,h,l,c,v for today."""
    h, l, c, v = b["h"], b["l"], b["c"], b["v"]
    vw, u1, d1, u2, d2 = ta.vwap_bands(h, l, c, v)
    st, sd = ta.supertrend(h, l, c, 10, 3.0)
    return {"e9": ta.ema(c, 9), "e21": ta.ema(c, 21), "vw": vw, "u1": u1, "d1": d1, "u2": u2, "d2": d2,
            "st": st, "sd": sd, "rsi": ta.rsi(c, 14), "atr": ta.atr(h, l, c, 10)}


def _sig(name, d, entry, stop, target, why, min_risk):
    risk = abs(entry - stop)
    if risk < min_risk or (d > 0 and not (stop < entry < target)) or (d < 0 and not (target < entry < stop)):
        return None
    rr = abs(target - entry) / risk
    if rr < 1.2:
        return None
    return {"setup": name, "direction": "up" if d > 0 else "down", "entry": float(entry), "stop": float(stop),
            "target": float(target), "rr": round(rr, 2), "why": why}


def _target(d, entry, stop, levels, min_r=1.5, default_r=2.0):
    """Nearest meaningful level at least min_r x risk away, else default_r x risk."""
    risk = abs(entry - stop)
    cands = sorted(x for x in levels if x and (x - entry) * d >= min_r * risk)
    if cands:
        return cands[0] if d > 0 else cands[-1]
    return entry + d * default_r * risk


def evaluate(b: dict, ind: dict, ctx: dict) -> list:
    """Signals on the last closed bar. ctx: is_index, pdh, pdl, pdc, cpr, or_high, or_low, resistance, support,
    gamma ('pinning'/'accelerating'/None), rel_vol (last bar volume vs today's average)."""
    n = len(b["c"])
    i = n - 1
    if n < 4:
        return []
    o, h, l, c = b["o"], b["h"], b["l"], b["c"]
    e9, e21, vw, st, sd, r = ind["e9"], ind["e21"], ind["vw"], ind["st"], ind["sd"], ind["rsi"]
    idx = ctx.get("is_index")
    min_risk = c[i] * (0.0008 if idx else 0.0015)
    vol_ok = idx or (ctx.get("rel_vol") or 0) >= 1.5
    cp = ctx.get("cpr") or {}
    up_lv = [ctx.get("pdh"), cp.get("R1"), cp.get("R2"), ctx.get("resistance"), ind["u2"][i]]
    dn_lv = [ctx.get("pdl"), cp.get("S1"), cp.get("S2"), ctx.get("support"), ind["d2"][i]]
    out = []
    bull, bear = c[i] > o[i], c[i] < o[i]

    # 1. EMA pullback in a trend
    if n >= 6 and not np.isnan(st[i]):
        if e9[i] > e21[i] and sd[i] > 0 and c[i] > vw[i] and l[i] <= max(e21[i], vw[i]) * 1.0005 and c[i] > e9[i] and bull:
            stop = min(min(l[i - 2:i + 1]), st[i]) - min_risk * 0.1
            s = _sig("ema_pullback", 1, c[i], stop, _target(1, c[i], stop, up_lv), ["EMA 9 above 21", "Supertrend up",
                     "dipped to EMA21/VWAP and closed back up"], min_risk)
            out.append(s)
        if e9[i] < e21[i] and sd[i] < 0 and c[i] < vw[i] and h[i] >= min(e21[i], vw[i]) * 0.9995 and c[i] < e9[i] and bear:
            stop = max(max(h[i - 2:i + 1]), st[i]) + min_risk * 0.1
            s = _sig("ema_pullback", -1, c[i], stop, _target(-1, c[i], stop, dn_lv), ["EMA 9 below 21", "Supertrend down",
                     "bounced to EMA21/VWAP and closed back down"], min_risk)
            out.append(s)

    # 2. VWAP reclaim / loss
    if n >= 8 and not np.isnan(r[i]):
        below = sum(c[i - 6:i] < vw[i - 6:i])
        if below >= 5 and c[i] > vw[i] and bull and r[i] > 50 and vol_ok:
            stop = min(l[i - 2:i + 1])
            out.append(_sig("vwap_reclaim", 1, c[i], stop, _target(1, c[i], stop, up_lv),
                            ["30 min below VWAP, now reclaimed", f"RSI {r[i]:.0f}"], min_risk))
        if below <= 1 and c[i] < vw[i] and bear and r[i] < 50 and vol_ok:
            stop = max(h[i - 2:i + 1])
            out.append(_sig("vwap_reclaim", -1, c[i], stop, _target(-1, c[i], stop, dn_lv),
                            ["30 min above VWAP, now lost", f"RSI {r[i]:.0f}"], min_risk))

    # 3. CPR breakout on a narrow-CPR day (09:30 - 11:30)
    if cp and ctx.get("or_high") and 3 <= i <= 27:
        narrow = cp["width_pct"] < (0.25 if idx else 0.4)
        top, bot = max(cp["TC"], ctx["or_high"]), min(cp["BC"], ctx["or_low"])
        if narrow and c[i] > top >= c[i - 1] and sd[i] > 0:
            stop = max(cp["BC"], ctx["or_low"])
            out.append(_sig("cpr_breakout", 1, c[i], stop, _target(1, c[i], stop, [cp["R1"], cp["R2"], ctx.get("pdh")], 1.2),
                            [f"narrow CPR ({cp['width_pct']:.2f}%)", "closed above CPR and opening range"], min_risk))
        if narrow and c[i] < bot <= c[i - 1] and sd[i] < 0:
            stop = min(cp["TC"], ctx["or_high"])
            out.append(_sig("cpr_breakout", -1, c[i], stop, _target(-1, c[i], stop, [cp["S1"], cp["S2"], ctx.get("pdl")], 1.2),
                            [f"narrow CPR ({cp['width_pct']:.2f}%)", "closed below CPR and opening range"], min_risk))

    # 4. previous-day high / low break (from 09:30)
    pdh, pdl = ctx.get("pdh"), ctx.get("pdl")
    if i >= 3 and pdh and pdl and vol_ok:
        if c[i] > pdh >= c[i - 1] and sd[i] > 0:
            stop = min(l[i - 1:i + 1])
            out.append(_sig("pdh_break", 1, c[i], stop, _target(1, c[i], stop, [cp.get("R1"), cp.get("R2"), ctx.get("resistance")]),
                            ["first close above yesterday's high", "Supertrend up"], min_risk))
        if c[i] < pdl <= c[i - 1] and sd[i] < 0:
            stop = max(h[i - 1:i + 1])
            out.append(_sig("pdh_break", -1, c[i], stop, _target(-1, c[i], stop, [cp.get("S1"), cp.get("S2"), ctx.get("support")]),
                            ["first close below yesterday's low", "Supertrend down"], min_risk))

    # 5. rejection at an OI wall (not when dealers accelerate moves)
    rng = h[i] - l[i]
    if rng > 0 and not np.isnan(r[i]) and ctx.get("gamma") != "accelerating" and i >= 6:
        W = ctx.get("resistance")
        if W and h[i] >= W * 0.999 and c[i] < W and (h[i] - max(o[i], c[i])) >= 0.5 * rng and r[i] >= 65:
            stop = max(h[i], W * 1.002)
            out.append(_sig("wall_fade", -1, c[i], stop, _target(-1, c[i], stop, [vw[i], ind["d1"][i]], 1.2),
                            [f"rejected at call OI wall {W:g}", f"RSI {r[i]:.0f}", "long upper wick"], min_risk))
        W = ctx.get("support")
        if W and l[i] <= W * 1.001 and c[i] > W and (min(o[i], c[i]) - l[i]) >= 0.5 * rng and r[i] <= 35:
            stop = min(l[i], W * 0.998)
            out.append(_sig("wall_fade", 1, c[i], stop, _target(1, c[i], stop, [vw[i], ind["u1"][i]], 1.2),
                            [f"held at put OI wall {W:g}", f"RSI {r[i]:.0f}", "long lower wick"], min_risk))
    return [s for s in out if s]


def chart_read(b: dict, ind: dict, ctx: dict) -> list:
    """Plain-language reading of the chart (always available, no AI needed)."""
    n = len(b["c"])
    if n < 3:
        return ["Not enough bars yet."]
    i = n - 1
    c = b["c"][i]
    out = []
    trend = "up" if ind["e9"][i] > ind["e21"][i] and ind["sd"][i] > 0 else "down" if ind["e9"][i] < ind["e21"][i] and ind["sd"][i] < 0 else "mixed"
    out.append({"up": "Trend up: EMA 9 above 21 and Supertrend up.", "down": "Trend down: EMA 9 below 21 and Supertrend down.",
                "mixed": "No clear trend: EMAs and Supertrend disagree."}[trend])
    vw = ind["vw"][i]
    dist = (c / vw - 1) * 100
    band = "above +2σ (stretched)" if c > ind["u2"][i] else "above +1σ" if c > ind["u1"][i] else "below −2σ (stretched)" if c < ind["d2"][i] else "below −1σ" if c < ind["d1"][i] else "inside the bands"
    out.append(f"Price {dist:+.2f}% from VWAP, {band}.")
    r = ind["rsi"][i]
    if not np.isnan(r):
        out.append(f"RSI {r:.0f}: {'overbought' if r >= 70 else 'oversold' if r <= 30 else 'strong' if r >= 55 else 'weak' if r <= 45 else 'neutral'}.")
    cp = ctx.get("cpr") or {}
    if cp:
        loc = "above the CPR" if c > cp["TC"] else "below the CPR" if c < cp["BC"] else "inside the CPR"
        out.append(f"{'Narrow' if cp['width_pct'] < (0.25 if ctx.get('is_index') else 0.4) else 'Wide'} CPR ({cp['width_pct']:.2f}%), price {loc}"
                   f"{': trend day likely' if cp['width_pct'] < 0.25 and loc != 'inside the CPR' else ''}.")
    lv = {k: v for k, v in {"yesterday's high": ctx.get("pdh"), "yesterday's low": ctx.get("pdl"), "R1": cp.get("R1"), "S1": cp.get("S1"),
                             "call wall": ctx.get("resistance"), "put wall": ctx.get("support"), "gamma flip": ctx.get("flip")}.items() if v}
    above = sorted(((v, k) for k, v in lv.items() if v > c))
    below = sorted(((v, k) for k, v in lv.items() if v < c), reverse=True)
    if above:
        out.append(f"Next resistance: {above[0][1]} {above[0][0]:,.1f} ({(above[0][0] / c - 1) * 100:+.2f}%).")
    if below:
        out.append(f"Next support: {below[0][1]} {below[0][0]:,.1f} ({(below[0][0] / c - 1) * 100:+.2f}%).")
    return out
