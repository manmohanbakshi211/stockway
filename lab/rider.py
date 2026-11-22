"""Trend Rider v1 (2.84): the option-buying setup's indicators and its decision, as plain functions.

Mandeep (22 Nov 2026): "ek achhi option buying ka set-up banana hai ... saare FNO stocks and index ... loss less ho,
time limit tak sirf premium na ghista rahe ... initial stoploss bhi achhi strong or confident ho". Plan:
claude/buying_setup_plan.md (the project doc) and the doc "Option Buying Setup v1".

Gun: the indicator maths and the decision below know nothing about one symbol, date or threshold. Bullets: every
number lives in rules/buy_setup.json (cfg()), with a valid-from date. The live engine (live/rider.py) and the Lab test
call the same functions, so a value on a chart, an alert and a backtest row can never disagree.

Inputs are closed 5-minute bars of one symbol for today: t, o, h, l, c, v (numpy arrays, oldest first).
Every indicator returns plain floats (or None when it cannot be known yet); nothing here raises on short or flat data.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

RULES = Path(__file__).resolve().parents[1] / "rules" / "buy_setup.json"
DEFAULTS = {
    "on": True,
    "windows": [[5, 60], [120, 240]],          # minutes since 09:15 at the bar's close: 09:20-10:15, 11:15-13:15
    "trend_day": {"min": 50, "weights": {"narrow": 1.0, "drive": 1.0, "extension": 1.0, "vwap_side": 1.5, "vwap_slope": 1.0}},
    "vwap": {"dist_atr": [0.1, 1.5], "slope_bars": 3},
    "breakout": {"min_quality": 55, "retest_bars": 6, "retest_atr": 0.3, "room_cap_atr": 2.0},
    "chase": {"max": 65},
    "agreement": {"min_families": 3, "weights": {"trend": 1.0, "momentum": 1.0, "structure": 1.0, "vwap": 1.0,
                                                 "flow": 1.0, "option": 1.0}},
    "move_vs_decay": {"horizon_min": 45, "min": 1.0, "charges_per_lot": 60.0},
    "tradability": {"min": 40, "max_spread_pct": 4.0, "lot_share_of_capital": 0.25},
    "stop": {"buffer_atr": 0.35, "max_premium_risk_pct": 30.0},
    "flow_gates": False,
    "ema_videos": {"on": True, "count_in_agreement": False, "as_trigger": False, "recent_bars": 3,
                   "alert": {"n": 5, "tf_bars": {"down": 1, "up": 3}}, "pullback": {"n": 10, "tf_bars": 1}},
    "cooldown_min": 30,
    "max_signals_per_symbol_day": 2,
}


def cfg() -> dict:
    """rules/buy_setup.json over the defaults (a missing or broken file = the defaults: a bullet never breaks the gun)."""
    out = json.loads(json.dumps(DEFAULTS))
    try:
        user = json.loads(RULES.read_text())
    except Exception:
        return out
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


# ------------------------------------------------------------------ small maths
def _f(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def clip01(x) -> float:
    return float(min(1.0, max(0.0, x)))


def ema(x, n: int) -> np.ndarray:
    x = np.asarray(x, float)
    out = np.empty(len(x))
    if not len(x):
        return out
    k = 2.0 / (n + 1)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = x[i] * k + out[i - 1] * (1 - k)
    return out


def atr(h, l, c, n: int = 14) -> float:
    """Wilder ATR of the 5-minute bars (the Lab's own atr14); early in the day the mean true range of what there is."""
    from .exits import atr14
    h, l, c = (np.asarray(x, float) for x in (h, l, c))
    if not len(c):
        return float("nan")
    a = atr14(h, l, c)
    if len(a) and math.isfinite(a[-1]):
        return float(a[-1])
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return float(np.mean(tr)) if len(tr) else float("nan")


def rsi(c, n: int = 7) -> float | None:
    c = np.asarray(c, float)
    if len(c) < n + 1:
        return None
    d = np.diff(c[-(n + 1):])
    up, dn = d[d > 0].sum(), -d[d < 0].sum()
    if up + dn == 0:
        return 50.0
    return float(100 * up / (up + dn))


def vwap(b) -> np.ndarray:
    """VWAP of the day's bars; an index has no volume, so its typical-price average (TWAP) stands in."""
    tp = (np.asarray(b["h"], float) + np.asarray(b["l"], float) + np.asarray(b["c"], float)) / 3
    v = np.nan_to_num(np.asarray(b.get("v", np.zeros(len(tp))), float))
    if v.sum() > 0:
        cv = np.cumsum(v)
        return np.where(cv > 0, np.cumsum(tp * v) / np.where(cv > 0, cv, 1), np.cumsum(tp) / np.arange(1, len(tp) + 1))
    return np.cumsum(tp) / np.arange(1, len(tp) + 1)


# ------------------------------------------------------------------ the indicators
def vwap_pull_go(b, a: float, slope_bars: int = 3) -> dict:
    """Which side of VWAP the close is (+1 / -1), how far (in ATRs) and which way VWAP leans (ATRs over slope_bars)."""
    vw = vwap(b)
    c = float(b["c"][-1])
    if not a or not math.isfinite(a) or a <= 0:
        return {"side": 0, "dist_atr": None, "slope_atr": None, "vwap": float(vw[-1])}
    k = min(slope_bars, len(vw) - 1)
    slope = (vw[-1] - vw[-1 - k]) / a if k > 0 else 0.0
    return {"side": 1 if c > vw[-1] else -1 if c < vw[-1] else 0, "dist_atr": round(float((c - vw[-1]) / a), 3),
            "slope_atr": round(float(slope), 3), "vwap": round(float(vw[-1]), 2)}


def trend_day(b, a: float, ctx: dict, weights: dict) -> dict:
    """Trend-Day Score 0-100: how much today looks like a trend day so far (initial balance narrow for the stock,
    an opening drive that never went back to the open, a move beyond the opening range, closes kept on one side of
    VWAP, VWAP leaning one way). Direction = the side of VWAP."""
    o, h, l, c = (np.asarray(b[k], float) for k in ("o", "h", "l", "c"))
    n = len(c)
    if n < 3 or not a or not math.isfinite(a) or a <= 0:
        return {"score": None, "dir": 0, "parts": {}}
    op = float(o[0])
    k = min(n, 12)                                       # initial balance = the first hour (12 bars), or what there is
    ib_w = float(h[:k].max() - l[:k].min())
    datr = _f(ctx.get("datr"))                           # daily ATR in points when known
    if datr and datr > 0:
        narrow = clip01((0.7 - ib_w / datr) / 0.35)      # IB under 35% of a day's ATR = 1, over 70% = 0
    else:
        narrow = clip01((4.0 - ib_w / a) / 2.0)          # no daily ATR: IB under 2 bar-ATRs = 1, over 4 = 0
    side_open = np.sign(c[2:] - op)
    one_side = abs(float(side_open.sum())) / max(1, len(side_open))
    drive = one_side * clip01(abs(c[-1] - op) / (1.5 * a))
    orh, orl = float(h[:3].max()), float(l[:3].min())
    ow = max(orh - orl, 1e-9)
    ext = clip01(max(c[-1] - orh, orl - c[-1], 0.0) / ow)          # one opening-range width beyond = full
    vw = vwap(b)
    sd = np.sign(c - vw)[-min(n, 12):]
    vside = abs(float(sd.sum())) / len(sd)
    kk = min(3, n - 1)
    vslope = clip01(abs(vw[-1] - vw[-1 - kk]) / (0.5 * a)) if kk > 0 else 0.0
    parts = {"narrow": narrow, "drive": drive, "extension": ext, "vwap_side": vside, "vwap_slope": vslope}
    wsum = sum(float(weights.get(p, 1.0)) for p in parts) or 1.0
    score = 100 * sum(parts[p] * float(weights.get(p, 1.0)) for p in parts) / wsum
    d = int(np.sign(c[-1] - vw[-1])) or int(np.sign(c[-1] - op))
    return {"score": round(float(score), 1), "dir": d, "parts": {p: round(v, 3) for p, v in parts.items()}}


def _levels(b, ctx: dict, d: int) -> list:
    """(name, price) levels a close can break in direction d, from what was known before this bar."""
    h, l = np.asarray(b["h"], float), np.asarray(b["l"], float)
    n = len(h)
    out = []
    if n >= 2:
        out.append(("day high" if d > 0 else "day low", float(h[:-1].max() if d > 0 else l[:-1].min())))
    if n >= 4:
        out.append(("opening range", float(h[:3].max() if d > 0 else l[:3].min())))
    if n >= 3 and h[-2] <= h[-3] and l[-2] >= l[-3]:          # the bar before was an inside bar: its mother's edge
        out.append(("inside bar", float(h[-3] if d > 0 else l[-3])))
    p = _f(ctx.get("pdh") if d > 0 else ctx.get("pdl"))
    if p:
        out.append(("yesterday's high" if d > 0 else "yesterday's low", p))
    return out


def breakout(b, a: float, ctx: dict, c_: dict) -> dict | None:
    """Breakout Quality 0-100 for the best fresh break (or break-and-retest) on the last closed bar, either direction:
    how clearly it closed beyond the level, how full the candle body was, the room to the next level and how often
    the level was tested before. None when nothing broke."""
    o, h, l, c = (np.asarray(b[k], float) for k in ("o", "h", "l", "c"))
    n = len(c)
    if n < 3 or not a or not math.isfinite(a) or a <= 0:
        return None
    best = None
    rb, ra, cap = int(c_.get("retest_bars", 6)), float(c_.get("retest_atr", 0.3)), float(c_.get("room_cap_atr", 2.0))
    for d in (1, -1):
        for name, lv in _levels(b, ctx, d):
            fresh = d * (c[-1] - lv) > 0 and d * (c[-2] - lv) <= 0
            retest = False
            if not fresh and d * (c[-1] - lv) > 0 and n > 3:
                back = range(max(1, n - rb), n - 1)
                broke_before = any(d * (c[j] - lv) > 0 for j in back)
                touched = d * ((l[-1] if d > 0 else h[-1]) - lv) <= ra * a
                retest = broke_before and touched and d * (c[-1] - c[-2]) > 0
            if not (fresh or retest):
                continue
            clear = clip01(d * (c[-1] - lv) / (0.6 * a))
            rng = h[-1] - l[-1]
            body = clip01(d * (c[-1] - o[-1]) / rng) if rng > 0 else 0.0
            nxt = [x for x in (_f(ctx.get("pdh")), _f(ctx.get("pdl")), _f(ctx.get("call_wall")), _f(ctx.get("put_wall")))
                   if x and d * (x - c[-1]) > 0]
            room = clip01((min(abs(x - c[-1]) for x in nxt) / a) / cap) if nxt else 1.0
            prior = h[:-1] if d > 0 else l[:-1]
            tests = int(np.sum(np.abs(prior - lv) <= 0.2 * a))
            tq = clip01(tests / 2)
            q = 100 * (1.5 * clear + 1.0 * body + 1.0 * room + 0.5 * tq) / 4.0
            if best is None or q > best["quality"]:
                best = {"dir": d, "level_name": ("retest of " + name) if retest else name, "level": round(lv, 2),
                        "quality": round(float(q), 1), "room_atr": round(room * cap, 2), "retest": retest}
    return best


def chase(b, a: float, d: int, ctx: dict) -> float | None:
    """Chase Meter 0-100 for direction d: how stretched the move already is (far from EMA 20 in ATRs, closes in a
    row the same way, a last candle much bigger than usual, the day's range against its normal). High = late."""
    if not d or not a or not math.isfinite(a) or a <= 0:
        return None
    o, h, l, c = (np.asarray(b[k], float) for k in ("o", "h", "l", "c"))
    e20 = ema(c, 20)
    dist = clip01(d * (c[-1] - e20[-1]) / a / 3.0)
    run = 0
    for i in range(len(c) - 1, 0, -1):
        if d * (c[i] - c[i - 1]) > 0:
            run += 1
        else:
            break
    runs = clip01(run / 5)
    last = clip01((h[-1] - l[-1]) / a / 2.5)
    adr = _f(ctx.get("adr"))
    dayr = clip01((h.max() - l.min()) / adr / 1.5) if adr and adr > 0 else 0.5
    return round(100 * (dist + runs + last + dayr) / 4, 1)


# ------------------------------------------------------------------ 2.89: the two EMA videos (13 Nov 2026), as gun pieces
def tf_groups(b, k: int) -> tuple[dict, int]:
    """k closed 5-minute bars -> one candle (k = 3: 15 minutes), aligned to 09:15. Returns the complete candles and the
    index of the candle the last 5-minute bar belongs to (it may still be forming)."""
    k = max(1, int(k))
    o, h, l, c = (np.asarray(b[x], float) for x in ("o", "h", "l", "c"))
    n = len(c) // k
    if n == 0:
        return {"o": o[:0], "h": h[:0], "l": l[:0], "c": c[:0]}, 0
    sl = slice(0, n * k)
    g = {"o": o[sl][::k], "c": c[sl][k - 1::k], "h": h[sl].reshape(n, k).max(1), "l": l[sl].reshape(n, k).min(1)}
    return g, (len(c) - 1) // k


def ema_alert(b, n: int = 5, k: int = 1) -> dict:
    """The "5 EMA" alert candle (video sent by Mandeep, 13 Nov 2026; rule copied from Tanu 0.11.0, engine/tf.py), on
    candles of k x 5 minutes and an EMA(n) of their closes. A candle that closes above the EMA with its low off it is a
    down-alert (the put side); one that closes below with its high off it is an up-alert (the call side). During the
    NEXT candle, a break of the down-alert's low fires -1 (stop: the alert's high), a break of the up-alert's high fires
    +1 (stop: the alert's low); the alert is then used up and lasts one candle. Judged on closed 5-minute bars: fired =
    the last 5-minute bar made the first break. Returns {+1: {...} | None, -1: {...} | None, "ema": value}."""
    out = {1: None, -1: None, "ema": None}
    g, cur = tf_groups(b, k)
    j = cur - 1                                   # the candle before the one the last bar belongs to
    if j < 0 or j >= len(g["c"]):
        return out
    e = ema(g["c"][: j + 1], n)[-1]
    out["ema"] = round(float(e), 4)
    ah, al, ac = float(g["h"][j]), float(g["l"][j]), float(g["c"][j])
    h, l = np.asarray(b["h"], float), np.asarray(b["l"], float)
    first = cur * max(1, int(k))                  # the first 5-minute bar of the following candle
    last = len(h) - 1
    if al > e and ac > e:                         # down-alert (put side)
        before = l[first:last]
        if l[last] < al and (not len(before) or before.min() >= al):
            out[-1] = {"level": al, "stop": ah, "alert": "5 EMA alert" if n == 5 else f"{n} EMA alert"}
    if ah < e and ac < e:                         # up-alert (call side)
        before = h[first:last]
        if h[last] > ah and (not len(before) or before.max() <= ah):
            out[1] = {"level": ah, "stop": al, "alert": "5 EMA alert" if n == 5 else f"{n} EMA alert"}
    return out


def ema_pullback(b, n: int = 10, k: int = 1) -> dict:
    """The "10 EMA" trend pullback trigger (video sent by Mandeep, 13 Nov 2026; Tanu 0.12.0): above the EMA a RED
    candle whose low stays off the EMA is a long trigger; below it a GREEN candle whose high stays off it is a short
    trigger. During the next candle, a break of the long trigger's high fires +1, of the short trigger's low -1; the
    stop is the EMA at the trigger candle's close; a trigger lasts one candle. Returns {+1, -1, "ema"} as ema_alert."""
    out = {1: None, -1: None, "ema": None}
    g, cur = tf_groups(b, k)
    j = cur - 1
    if j < 0 or j >= len(g["c"]):
        return out
    e = float(ema(g["c"][: j + 1], n)[-1])
    out["ema"] = round(e, 4)
    to, th, tl, tc = (float(g[x][j]) for x in ("o", "h", "l", "c"))
    h, l = np.asarray(b["h"], float), np.asarray(b["l"], float)
    first, last = cur * max(1, int(k)), len(h) - 1
    if tc < to and tl > e:                        # red, above the EMA: long trigger
        before = h[first:last]
        if h[last] > th and (not len(before) or before.max() <= th):
            out[1] = {"level": th, "stop": e, "alert": f"{n} EMA pullback"}
    if tc > to and th < e:                        # green, below the EMA: short trigger
        before = l[first:last]
        if l[last] < tl and (not len(before) or before.min() >= tl):
            out[-1] = {"level": tl, "stop": e, "alert": f"{n} EMA pullback"}
    return out


def ema_videos(b, c_: dict, hist: list | None = None) -> dict:
    """Both video rules on the last closed bar, with what the bullets say (rules/buy_setup.json "ema_videos"): which
    fired now, per direction, and the family vote (+1 / -1 fired within the last recent_bars bars, from hist = the
    earlier bars' fired lists, newest last)."""
    ev = c_.get("ema_videos") or {}
    al, pb = ev.get("alert") or {}, ev.get("pullback") or {}
    tfa = al.get("tf_bars") or {}
    a_up = ema_alert(b, int(al.get("n", 5)), int(tfa.get("up", 3)))
    a_dn = ema_alert(b, int(al.get("n", 5)), int(tfa.get("down", 1))) if int(tfa.get("down", 1)) != int(tfa.get("up", 3)) else a_up
    p = ema_pullback(b, int(pb.get("n", 10)), int(pb.get("tf_bars", 1)))
    fired = {1: [x for x in (a_up[1], p[1]) if x], -1: [x for x in (a_dn[-1], p[-1]) if x]}
    c = np.asarray(b["c"], float)
    e_n = int(pb.get("n", 10))
    e10 = float(ema(c, e_n)[-1]) if len(c) else None
    e5 = float(ema(c, int(al.get("n", 5)))[-1]) if len(c) else None
    recent = int(ev.get("recent_bars", 3))
    past = list(hist or [])[-(recent - 1):] if recent > 1 else []
    vote = {d: bool(fired[d]) or any(d in (x or []) for x in past) for d in (1, -1)}
    return {"fired": fired, "vote": vote, "ema_fast": round(e5, 4) if e5 is not None else None,
            "ema_slow": round(e10, 4) if e10 is not None else None,
            "now": [d for d in (1, -1) if fired[d]]}


def agreement(b, d: int, vp: dict, w: dict, flow: float | None = None, thrust: float | None = None) -> dict:
    """Which independent families point the same way as d: trend (EMA 9 over 21 and price over EMA 21), momentum
    (RSI 7 and the last 3-bar move), structure (rising lows or falling highs), VWAP (side and lean), flow (when
    known), the option itself (when known). Weighted sum and the count of families that agree."""
    c = np.asarray(b["c"], float)
    h, l = np.asarray(b["h"], float), np.asarray(b["l"], float)
    fam = {}
    if len(c) >= 3 and d:
        e9, e21 = ema(c, 9), ema(c, 21)
        fam["trend"] = d * (e9[-1] - e21[-1]) > 0 and d * (c[-1] - e21[-1]) > 0
        r = rsi(c, 7)
        fam["momentum"] = r is not None and (r > 55 if d > 0 else r < 45) and d * (c[-1] - c[-3]) > 0
        fam["structure"] = (l[-1] > l[-2] >= l[-3]) if d > 0 else (h[-1] < h[-2] <= h[-3])
        fam["vwap"] = vp.get("side") == d and (vp.get("slope_atr") or 0) * d > 0
    if flow is not None:
        fam["flow"] = flow * d > 0.15
    if thrust is not None:
        fam["option"] = thrust >= 1.0
    yes = [k for k, v in fam.items() if v]
    return {"families": len(yes), "weighted": round(sum(float(w.get(k, 1.0)) for k in yes), 2), "agree": yes,
            "checked": list(fam)}


def rel_strength(b, nifty: dict | None) -> float | None:
    """% move since the open minus NIFTY's (+ = stronger than the market). NIFTY itself: 0."""
    if not nifty or not len(nifty.get("c", [])):
        return None
    c, o = float(b["c"][-1]), float(b["o"][0])
    nc, no = float(nifty["c"][-1]), float(nifty["o"][0])
    if o <= 0 or no <= 0:
        return None
    return round(100 * ((c / o - 1) - (nc / no - 1)), 3)


def move_vs_decay(a: float, opt: dict | None, horizon: float, tds: float | None, charges_per_lot: float) -> float | None:
    """What the option is likely to earn from the underlying's usual move over `horizon` minutes (ATR scaled by
    time and by how trendy the day is, times delta) against what it loses in the same time (theta, half the spread,
    charges). Above 1 = the trade makes sense; below 1 = the premium mostly melts."""
    if not opt or not a or not math.isfinite(a) or a <= 0:
        return None
    px = _f(opt.get("ltp"))
    if not px or px <= 0:
        return None
    delta = abs(_f(opt.get("delta")) or 0.5)
    move = a * math.sqrt(horizon / 5.0) * (0.5 + (tds or 50) / 100.0)
    gain = delta * move
    th = _f(opt.get("theta"))
    if th:
        decay = abs(th) * horizon / 375.0                      # broker theta is per day; 375 market minutes a day
    else:
        dte = max(_f(opt.get("dte")) or 1.0, 0.25)
        decay = px * horizon / (2 * dte * 375.0)               # at-the-money approximation
    bid, ask = _f(opt.get("bid")), _f(opt.get("ask"))
    half = (ask - bid) / 2 if bid and ask and ask >= bid else 0.0
    lot = _f(opt.get("lot")) or 1.0
    cost = decay + half + charges_per_lot / lot
    return round(gain / cost, 3) if cost > 0 else None


def tradability(opt: dict | None, ctx: dict, c_: dict, capital: float | None) -> dict:
    """Tradability 0-100: can this option be bought now without the spread or the lot size eating the edge? Spread
    as % of the price, one lot against the account's capital, an event or results day, the ban list."""
    if not opt:
        return {"score": 0.0, "why": "no option price"}
    px, bid, ask = _f(opt.get("ltp")), _f(opt.get("bid")), _f(opt.get("ask"))
    lot = _f(opt.get("lot")) or 0
    if not px or px <= 0:
        return {"score": 0.0, "why": "no option price"}
    sp = (ask - bid) / px * 100 if bid and ask and ask >= bid else None
    mx = float(c_.get("max_spread_pct", 4.0))
    s1 = clip01((mx - sp) / mx) if sp is not None else 0.5
    cost = (ask or px) * lot
    share = float(c_.get("lot_share_of_capital", 0.25))
    s2 = 1.0 if not capital or cost <= capital * share else 0.0
    ev = 0.5 if ctx.get("event_day") else 1.0
    ban = 0.0 if ctx.get("ban") else 1.0
    score = 100 * s1 * s2 * ev * ban
    why = ("ban list" if not ban else "1 lot costs more than the capital allows" if not s2 else
           f"spread {sp:.1f}%" if sp is not None and s1 < 0.5 else "event day" if ev < 1 else "ok")
    return {"score": round(score, 1), "spread_pct": round(sp, 2) if sp is not None else None,
            "lot_cost": round(cost, 0), "why": why}


def structure_stop(b, a: float, d: int, level: float | None, buffer_atr: float) -> float | None:
    """The trade's first stop: beyond the level the trigger broke (or the breakout candle's far end, whichever is
    further), plus a buffer of ATR, so ordinary noise does not take it. Checked on closes, never on a wick."""
    if not a or not math.isfinite(a) or a <= 0 or not d:
        return None
    lo, hi = float(b["l"][-1]), float(b["h"][-1])
    cand = [lo if d > 0 else hi]
    if level is not None:
        cand.append(float(level))
    base = min(cand) if d > 0 else max(cand)
    return round(base - d * buffer_atr * a, 2)


# ------------------------------------------------------------------ all of them for one symbol, and the decision
def readings(b, ctx: dict, c_: dict | None = None, nifty: dict | None = None, opt_up: dict | None = None,
             opt_dn: dict | None = None, capital: float | None = None, flow: float | None = None,
             thrust: dict | None = None, ema_hist: list | None = None) -> dict:
    """Every indicator for the last closed bar. opt_up / opt_dn = the call / put an alert would suggest (ltp, bid,
    ask, delta, theta, lot, dte); thrust = {+1: x, -1: y} when known; flow = -1..+1 (+ = buyers) when known;
    ema_hist = the directions the EMA video rules fired on the earlier bars today (newest last), for their vote."""
    c_ = c_ or cfg()
    a = atr(b["h"], b["l"], b["c"])
    vp = vwap_pull_go(b, a, int(c_["vwap"].get("slope_bars", 3)))
    td = trend_day(b, a, ctx, c_["trend_day"].get("weights") or {})
    bo = breakout(b, a, ctx, c_["breakout"])
    out = {"atr": round(a, 3) if math.isfinite(a) else None, "vwap": vp, "trend_day": td, "breakout": bo,
           "rs": rel_strength(b, nifty), "flow": flow, "tod": None}
    ev = ema_videos(b, c_, ema_hist) if (c_.get("ema_videos") or {}).get("on", True) else None
    out["ema"] = ev
    for d, opt in ((1, opt_up), (-1, opt_dn)):
        key = "up" if d > 0 else "down"
        th = (thrust or {}).get(d)
        ag = agreement(b, d, vp, c_["agreement"].get("weights") or {}, flow, th)
        if ev is not None:                        # 2.89: the "ema" family (the two videos), counted only when switched on
            yes = ev["vote"][d]
            ag_e = {**ag, "checked": ag["checked"] + ["ema"]}
            if yes:
                ag_e.update(families=ag["families"] + 1, agree=ag["agree"] + ["ema"],
                            weighted=round(ag["weighted"] + float((c_["agreement"].get("weights") or {}).get("ema", 1.0)), 2))
            ag["ema_vote"] = yes
        out[key] = {"chase": chase(b, a, d, ctx),
                    "agree": ag, "agree_ema": ag_e if ev is not None else ag,
                    "mvd": move_vs_decay(a, opt, float(c_["move_vs_decay"].get("horizon_min", 45)), td.get("score"),
                                         float(c_["move_vs_decay"].get("charges_per_lot", 60))),
                    "trad": tradability(opt, ctx, c_["tradability"], capital), "thrust": th}
    return out


def in_window(tod: float, c_: dict) -> bool:
    return any(float(w[0]) <= tod <= float(w[1]) for w in (c_.get("windows") or []))


def decide(b, r: dict, tod: float, c_: dict | None = None, opt: dict | None = None, opts: dict | None = None) -> dict:
    """The Trend Rider decision on the last closed bar: go (True / False), direction, trigger, the first stop, and
    the plain reasons (why, and which checks failed). opt = the option for the breakout's direction (for the stop's
    premium risk); opts = {+1: call, -1: put} instead, when the trigger's direction is not known before (2.89).
    2.89 bullets (rules/buy_setup.json "ema_videos", both off = exactly 2.84): count_in_agreement counts the EMA
    videos' family; as_trigger lets a video trigger that fired on this bar stand in for a missing breakout (every
    other check still applies; its stop is the video's stop: the alert candle's far end or the EMA)."""
    c_ = c_ or cfg()
    ev_c = c_.get("ema_videos") or {}
    bo = r.get("breakout")
    fails, why = [], []
    if not in_window(tod, c_):
        fails.append("outside the time windows")
    vid = None
    if not bo and ev_c.get("as_trigger") and r.get("ema") and r["ema"].get("now"):
        d0 = r["ema"]["now"][0]
        x = r["ema"]["fired"][d0][0]
        vid = {"dir": d0, "level_name": x["alert"], "level": x["stop"], "quality": None, "video": True}
    if not bo and not vid:
        return {"go": False, "dir": 0, "fails": fails + ["no breakout"], "why": []}
    bo = bo or vid
    d = bo["dir"]
    if opts is not None:
        opt = opts.get(d)
    side = r["up" if d > 0 else "down"]
    td = r["trend_day"].get("score")
    if bo.get("video"):
        why.append(f"{bo['level_name']} fired (video rule, on trial)")
    elif bo["quality"] < float(c_["breakout"]["min_quality"]):
        fails.append(f"breakout quality {bo['quality']:.0f}")
    else:
        why.append(f"{bo['level_name']} broke, quality {bo['quality']:.0f}")
    if td is None or td < float(c_["trend_day"]["min"]):
        fails.append(f"trend-day score {td if td is not None else '-'}")
    else:
        why.append(f"trend-day score {td:.0f}")
    vp = r["vwap"]
    lo, hi = (float(x) for x in c_["vwap"]["dist_atr"])
    dist = (vp.get("dist_atr") or 0) * d
    if vp.get("side") != d or not (lo <= dist <= hi):
        fails.append("VWAP side or distance")
    else:
        why.append(f"{'above' if d > 0 else 'below'} VWAP by {dist:.1f} ATR")
    ch = side.get("chase")
    if ch is not None and ch > float(c_["chase"]["max"]):
        fails.append(f"chase meter {ch:.0f} (late)")
    ag = side.get("agree_ema", side["agree"]) if ev_c.get("count_in_agreement") else side["agree"]
    if ag["families"] < int(c_["agreement"]["min_families"]):
        fails.append(f"{ag['families']} families agree")
    else:
        why.append(f"{ag['families']} families agree ({', '.join(ag['agree'])})")
    mvd = side.get("mvd")
    if mvd is None or mvd < float(c_["move_vs_decay"]["min"]):
        fails.append(f"move vs decay {mvd if mvd is not None else '-'}")
    else:
        why.append(f"move vs decay {mvd:.2f}")
    tr = side.get("trad") or {}
    if (tr.get("score") or 0) < float(c_["tradability"]["min"]):
        fails.append(f"tradability: {tr.get('why')}")
    if c_.get("flow_gates") and r.get("flow") is not None and r["flow"] * d <= 0:
        fails.append("flow against")
    a = r.get("atr")
    stop = structure_stop(b, a or 0, d, bo.get("level"), float(c_["stop"]["buffer_atr"]))
    risk_pct = None
    if stop is not None and opt and _f(opt.get("ltp")):
        risk_pct = abs(float(b["c"][-1]) - stop) * abs(_f(opt.get("delta")) or 0.5) / float(opt["ltp"]) * 100
        if risk_pct > float(c_["stop"]["max_premium_risk_pct"]):
            fails.append(f"stop would risk {risk_pct:.0f}% of the premium")
    return {"go": not fails, "dir": d, "trigger": bo["level_name"], "level": bo.get("level"), "stop": stop,
            "risk_pct": round(risk_pct, 1) if risk_pct is not None else None, "fails": fails, "why": why}
