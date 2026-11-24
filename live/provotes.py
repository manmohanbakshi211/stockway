"""Vote Lab (3.08, Stockway Pro v2): every book, video and course turned into votes on Pro's own signals (gun).

Mandeep (25 Nov 2026): "jo humne transcript di hai unka best use kaha ho sakta hai ... 5 min ema or 10 min ema,
ghanshyam wale points, 51 stretegis wali book, candle patterens or baaki sab jod kar ..."

What the fusion test found (7 replayed days, 468 Pro signals, claude/v2_fusion.md): as stand-alone strategies these
inputs lost in the 5-year Lab, and adding every one of them to the score did not make money. As votes on a Pro signal
two of them helped: the price already beyond VWAP +/- 2 sigma in the trade's direction (Aseem's band) was a worse
entry (-0.02R vs +0.28R, 4 of 5 days), and a pause at the level before the break (Ghanshyam) a better one inside the
strict signals (+0.31R, 4 of 5 days). So every vote is computed and recorded on every signal (data.pro.votes, "x_"
names, weight 0: the score does not change); the Pro page's Checks tab and the nightly learned checks (3.07) grade
them on new days, and only a vote that wins there may ever be used. A new transcript becomes a vote here (a bullet in
rules/pro_votes.json + a few lines below), not a new strategy.

Each vote: +1 with the trade, -1 against it, 0 no view. Uses only the closed 5-minute bars of today (plus earlier
days' bars when the app already has them), yesterday's high / low / close, the signal's broken level and its ATR.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

RULES = Path(__file__).resolve().parents[1] / "rules" / "pro_votes.json"
_C: dict = {"m": None, "v": None}


def cfg() -> dict:
    try:
        m = RULES.stat().st_mtime
        if _C["m"] != m:
            _C.update(m=m, v=json.loads(RULES.read_text()))
        return _C["v"]
    except Exception:
        return {"on": True, "votes": {}}


def names() -> dict:
    """{vote name: {"source", "words"}} of the votes that are on."""
    return {k: v for k, v in (cfg().get("votes") or {}).items() if v.get("on", True)}


def _ema(x, n):
    a, out, e = 2.0 / (n + 1), np.empty(len(x)), None
    for i, v in enumerate(x):
        e = v if e is None else e + a * (v - e)
        out[i] = e
    return out


def _rsi(x, n=14):
    if len(x) < 2:
        return np.full(len(x), 50.0)
    d = np.diff(x, prepend=x[0])
    up, dn = np.clip(d, 0, None), np.clip(-d, 0, None)
    a = 1.0 / n
    ru = rd = None
    out = np.empty(len(x))
    for i in range(len(x)):
        ru = up[i] if ru is None else ru + a * (up[i] - ru)
        rd = dn[i] if rd is None else rd + a * (dn[i] - rd)
        out[i] = 50.0 if rd == 0 and ru == 0 else (100.0 if rd == 0 else 100 - 100 / (1 + ru / rd))
    return out


def _supertrend(h, l, c, n=7, m=3.0):
    tr = np.maximum(h - l, np.maximum(abs(h - np.roll(c, 1)), abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    atr = _ema(tr, 2 * n - 1)                              # Wilder smoothing
    hl2 = (h + l) / 2
    ub, lb = hl2 + m * atr, hl2 - m * atr
    fu, fl, dirn = ub.copy(), lb.copy(), np.ones(len(c))
    for i in range(1, len(c)):
        fu[i] = ub[i] if (ub[i] < fu[i - 1] or c[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lb[i] if (lb[i] > fl[i - 1] or c[i - 1] < fl[i - 1]) else fl[i - 1]
        dirn[i] = 1 if c[i] > fu[i - 1] else (-1 if c[i] < fl[i - 1] else dirn[i - 1])
    return dirn


def _swings(h, l, k=2):
    sh, sl = [], []
    for i in range(k, len(h) - k):
        if h[i] == max(h[i - k:i + k + 1]):
            sh.append(h[i])
        if l[i] == min(l[i - k:i + k + 1]):
            sl.append(l[i])
    return sh, sl


def _round_levels(spot):
    step = 500 if spot > 20000 else 100 if spot > 5000 else 50 if spot > 1000 else 10 if spot > 200 else 5
    base = int(spot // step) * step
    return [base - step, base, base + step, base + 2 * step]


def _sgn(x):
    return 1 if x > 0 else (-1 if x < 0 else 0)


def compute(b: dict, d: int, level=None, atr=None, prev: dict | None = None, hist: list | None = None) -> dict:
    """b: today's closed 5-minute bars {"o","h","l","c","v"}; d: +1 call / -1 put; level: the broken level; atr: the
    setup's ATR; prev: yesterday's {"h","l","c"}; hist: earlier days' 5-minute bars [(o, h, l, c) arrays], oldest
    first (only what the app already holds). Returns {vote: -1 / 0 / +1} for the votes that are on, plus "_cpr_w"."""
    on = names()
    if not on or not b or b.get("c") is None or len(b["c"]) < 2:
        return {}
    try:
        atr = float(atr)
    except (TypeError, ValueError):
        atr = 0.0
    if not atr or not math.isfinite(atr) or atr <= 0:
        return {}
    to, th, tl, tc = (np.asarray(b[k], float) for k in ("o", "h", "l", "c"))
    tv = np.asarray(b.get("v") if b.get("v") is not None else np.zeros(len(tc)), float)
    if hist:
        hh = np.concatenate([np.asarray(x[1], float) for x in hist] + [th])
        hl = np.concatenate([np.asarray(x[2], float) for x in hist] + [tl])
        hc = np.concatenate([np.asarray(x[3], float) for x in hist] + [tc])
    else:
        hh, hl, hc = th, tl, tc
    C = tc[-1]
    v: dict = {}
    # ---------- EMA videos (5 EMA alert candle, 10 EMA pullback)
    e5, e10 = _ema(hc, 5), _ema(hc, 10)
    v["ema_stack_5_10"] = d if (C > e5[-1] > e10[-1]) else (-d if (C < e5[-1] < e10[-1]) else 0)
    if len(hc) >= 4:
        lo_b = hl[-4:] if d > 0 else -hh[-4:]
        e10s = e10[-4:] if d > 0 else -e10[-4:]
        rising = (e10[-1] - e10[-4]) * d > 0
        touched = any(lo_b[i] <= e10s[i] * (1 + 0.0005) for i in range(len(lo_b) - 1))
        v["ema10_pullback"] = 1 if (rising and touched and (C - e10[-1]) * d > 0) else 0
    beyond = (hl[-1] > e5[-1]) if d > 0 else (hh[-1] < e5[-1])
    v["ema5_stretched"] = -1 if beyond else 0
    # ---------- Ghanshyam (breakout rules, round figure, yesterday's close, with / against the morning move)
    if level is not None:
        lvl = float(level)
        near = 0.15 * atr
        ph, pl, pc = th[:-1], tl[:-1], tc[:-1]
        touches = int(np.sum((ph >= lvl - near) & (pc <= lvl))) if d > 0 else int(np.sum((pl <= lvl + near) & (pc >= lvl)))
        v["gh_3touch"] = 1 if touches >= 2 else 0
        pre = tc[-5:-1]
        pause = int(np.sum((np.abs(pre - lvl) <= 0.3 * atr) & ((pre - lvl) * d <= 0.05 * atr)))
        v["gh_pause"] = 1 if pause >= 2 else 0
    run = (C - hc[-4]) * d / atr if len(hc) >= 4 else 0.0
    v["gh_longrun"] = -1 if run > 1.5 else 0
    v["gh_roundfig"] = -1 if any(0 < (x - C) * d <= 0.3 * atr for x in _round_levels(C)) else 0
    v["gh_with_morning"] = _sgn((C - to[0]) * d)
    pv = prev or {}
    ok_prev = all(pv.get(k) for k in ("h", "l", "c"))
    if ok_prev:
        H, L, PC = float(pv["h"]), float(pv["l"]), float(pv["c"])
        v["gh_near_yclose"] = -1 if abs(C - PC) <= 0.3 * atr else 0
        # ---------- Aseem 51 + PDFs
        P = (H + L + PC) / 3
        BC = (H + L) / 2
        TC = 2 * P - BC
        v["_cpr_w"] = round(abs(TC - BC) / P * 100, 4) if P else None
        R1, S1 = 2 * P - L, 2 * P - H
        if d > 0:
            v["as_pivot_zone"] = 1 if C > R1 else (-1 if C > S1 else 0)
            swept = tl.min() < L and C > L
        else:
            v["as_pivot_zone"] = 1 if C < S1 else (-1 if C < R1 else 0)
            swept = th.max() > H and C < H
        v["as_daily_sweep"] = 1 if swept else 0
    if len(hc) >= 30:
        v["as_supertrend"] = d * int(_supertrend(hh, hl, hc)[-1])
        e9, e21 = _ema(hc, 9), _ema(hc, 21)
        v["as_ema_9_21"] = d * _sgn(e9[-1] - e21[-1])
        r15 = _rsi(hc[::-1][::3][::-1])
        v["as_rsi15_side"] = d * _sgn(r15[-1] - 50)
        sh, sl = _swings(hh, hl)
        if len(sh) >= 2 and len(sl) >= 2:
            up = sh[-1] > sh[-2] and sl[-1] > sl[-2]
            dn = sh[-1] < sh[-2] and sl[-1] < sl[-2]
            v["as_dow"] = d if up else (-d if dn else 0)
    tp = (th + tl + tc) / 3
    w = tv if tv.sum() > 0 else np.ones(len(tp))
    vw = float((tp * w).sum() / w.sum())
    sd = float(np.sqrt(((tp - vw) ** 2 * w).sum() / w.sum()))
    v["as_vwap2_stretch"] = -1 if sd > 0 and (C - vw) * d > 2 * sd else 0
    if level is not None and len(tc) >= 4:
        lvl = float(level)
        bp = (tc[:-1] - lvl) * d > 0.05 * atr
        back = ((tl[:-1] - lvl) if d > 0 else (lvl - th[:-1])) <= 0.2 * atr
        if bp.any():
            fb = int(np.argmax(bp))
            v["as_retest"] = 1 if back[fb + 1:].any() else 0
        else:
            v["as_retest"] = 0
    # ---------- candles on the signal bar
    o_, h_, l_, c_ = to[-1], th[-1], tl[-1], tc[-1]
    rng = max(h_ - l_, 1e-9)
    body = c_ - o_
    v["cd_strong_body"] = 1 if body * d / rng >= 0.6 else 0
    wick = (h_ - max(o_, c_)) if d > 0 else (min(o_, c_) - l_)
    v["cd_wick_against"] = -1 if wick / rng >= 0.5 else 0
    v["cd_doji"] = -1 if abs(body) / rng <= 0.1 else 0
    pb = tc[-2] - to[-2]
    v["cd_engulf"] = 1 if (pb * d < 0 and body * d > 0 and abs(body) > abs(pb)
                           and ((c_ >= to[-2]) if d > 0 else (c_ <= to[-2]))) else 0
    if len(tc) >= 3:
        inside = th[-2] <= th[-3] and tl[-2] >= tl[-3]
        v["cd_inside_break"] = 1 if inside and ((c_ > th[-2]) if d > 0 else (c_ < tl[-2])) else 0
    out = {k: int(x) for k, x in v.items() if k in on and not k.startswith("_")}
    if "_cpr_w" in v:
        out["_cpr_w"] = v["_cpr_w"]
    return out


def as_pro_votes(v: dict) -> dict:
    """The votes as Pro factors ("x_" + name): recorded with the signal's own votes, weight 0 (no effect on the score)."""
    return {"x_" + k: int(x) for k, x in (v or {}).items() if not k.startswith("_") and isinstance(x, (int, np.integer))}
