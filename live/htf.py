"""The bigger picture for every alert: daily and weekly trend, where price sits in its recent range, and events ahead.

Computed once a day from the daily candles the engine already loads (no extra data calls), only from finished days
(today's unfinished candle is never used), so the same facts can be rebuilt for any past day in the Lab.

Each alert records these facts aligned with its direction (+ = with the alert). They are recorded only: the score,
Focus, the risk check verdict and paper trades do not use them until a challenger conviction model that uses them
beats the current one on days it never saw (live/learn.py). The risk check shows them as warnings, never as a block.
"""
from __future__ import annotations

import datetime as dt

import numpy as np

from .clock import IST


def _ema(x: np.ndarray, n: int) -> np.ndarray:
    a = 2 / (n + 1)
    out = np.empty(len(x))
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def _rsi(c: np.ndarray, n: int = 14) -> float | None:
    if len(c) <= n:
        return None
    d = np.diff(c)
    up, dn = np.clip(d, 0, None), np.clip(-d, 0, None)
    au, ad = up[:n].mean(), dn[:n].mean()
    for i in range(n, len(d)):
        au = (au * (n - 1) + up[i]) / n
        ad = (ad * (n - 1) + dn[i]) / n
    return 100.0 if ad == 0 else float(100 - 100 / (1 + au / ad))


def _supertrend_dir(h, l, c, n=10, mult=3.0) -> int | None:
    if len(c) <= n + 1:
        return None
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    atr = np.empty(len(tr))
    atr[:n] = tr[:n].mean()
    for i in range(n, len(tr)):
        atr[i] = (atr[i - 1] * (n - 1) + tr[i]) / n
    hl2 = (h[1:] + l[1:]) / 2
    up, dn = hl2 - mult * atr, hl2 + mult * atr
    d, fu, fd = 1, up[0], dn[0]
    cc = c[1:]
    for i in range(1, len(cc)):
        fu = max(up[i], fu) if cc[i - 1] > fu else up[i]
        fd = min(dn[i], fd) if cc[i - 1] < fd else dn[i]
        if d == 1 and cc[i] < fu:
            d = -1
        elif d == -1 and cc[i] > fd:
            d = 1
    return d


def daily(candles: list, today: dt.date | None = None) -> dict:
    """Facts from daily candles ([ts, o, h, l, c, vol, ...], any order), using only days before `today`."""
    today = today or dt.datetime.now(IST).date()
    cs = sorted((x for x in candles if str(x[0])[:10] < today.isoformat()), key=lambda x: str(x[0]))
    if len(cs) < 30:
        return {}
    o, h, l, c = (np.array([float(x[k]) for x in cs]) for k in (1, 2, 3, 4))
    e20, e50 = _ema(c, 20), _ema(c, 50)
    e200 = _ema(c, 200) if len(c) >= 200 else None
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    atr = float(tr[-14:].mean())
    hi20, lo20 = float(h[-20:].max()), float(l[-20:].min())
    last = float(c[-1])
    trend = 1 if last > e20[-1] > e50[-1] else -1 if last < e20[-1] < e50[-1] else 0
    # weekly: Friday (last day of each week) closes, trend = close vs its 10-week average and that average rising
    days = [dt.date.fromisoformat(str(x[0])[:10]) for x in cs]
    wk = {}
    for d_, cl in zip(days, c):
        wk[d_.isocalendar()[:2]] = cl
    wc = np.array(list(wk.values()))
    w_trend = None
    if len(wc) >= 12:
        w10 = _ema(wc, 10)
        w_trend = 1 if wc[-1] > w10[-1] and w10[-1] >= w10[-2] else -1 if wc[-1] < w10[-1] and w10[-1] <= w10[-2] else 0
    # last full week's high and low (the week before the current one)
    wk_hl = {}
    for d_, hh, ll in zip(days, h, l):
        k = d_.isocalendar()[:2]
        a_, b_ = wk_hl.get(k, (-1e18, 1e18))
        wk_hl[k] = (max(a_, hh), min(b_, ll))
    wks = sorted(wk_hl)
    cur_wk = today.isocalendar()[:2]
    done_w = [k for k in wks if k < cur_wk]
    pwh, pwl = (wk_hl[done_w[-1]] if done_w else (None, None))
    yr = c[-250:]
    return {"asof": days[-1].isoformat(), "close": round(last, 2), "trend": trend,
            "ema20": round(float(e20[-1]), 2), "ema50": round(float(e50[-1]), 2),
            "ema200": round(float(e200[-1]), 2) if e200 is not None else None,
            "vs_ema20_pct": round((last / e20[-1] - 1) * 100, 2), "st": _supertrend_dir(h, l, c),
            "rsi": round(_rsi(c), 1) if _rsi(c) is not None else None, "w_trend": w_trend,
            "atr": round(atr, 2), "atr_pct": round(atr / last * 100, 2), "hi20": round(hi20, 2), "lo20": round(lo20, 2),
            "pos20": round((last - lo20) / (hi20 - lo20), 2) if hi20 > lo20 else None,
            "from_hi52_pct": round((last / float(yr.max()) - 1) * 100, 2), "from_lo52_pct": round((last / float(yr.min()) - 1) * 100, 2),
            "pwh": round(float(pwh), 2) if pwh else None, "pwl": round(float(pwl), 2) if pwl else None,
            "prev_h": round(float(h[-1]), 2), "prev_l": round(float(l[-1]), 2), "prev_c": round(last, 2)}


def aligned(f: dict, direction: str, spot: float | None) -> dict:
    """The daily facts signed with the alert (+1 = the bigger picture agrees). Empty when nothing is known."""
    if not f or direction not in ("up", "down"):
        return {}
    d = 1 if direction == "up" else -1
    s = lambda x: None if x is None else x * d
    out = {"htf_d_al": s(f.get("trend")), "htf_w_al": s(f.get("w_trend")), "htf_st_al": s(f.get("st")),
           "htf_ema20_al": s(f.get("vs_ema20_pct")), "htf_rsi": f.get("rsi"), "htf_pos20": f.get("pos20"),
           "htf_hi52": f.get("from_hi52_pct")}
    if spot and f.get("atr"):
        # room to run before the 20-day high (up) / low (down), in daily ATRs: little room = into resistance
        room = (f["hi20"] - spot) if d > 0 else (spot - f["lo20"])
        out["htf_room_atr"] = round(room / f["atr"], 2)
    return out


def words(f: dict, direction: str) -> str | None:
    """One short line for the alert: 'Bigger picture: daily ↑, weekly ↑, 1.2 ATR below the 20-day high'."""
    if not f:
        return None
    arrow = {1: "↑", -1: "↓", 0: "sideways", None: "?"}
    parts = [f"daily {arrow.get(f.get('trend'))}", f"weekly {arrow.get(f.get('w_trend'))}"]
    if f.get("pos20") is not None:
        parts.append("near the 20-day high" if f["pos20"] >= 0.9 else "near the 20-day low" if f["pos20"] <= 0.1 else
                     f"{int(f['pos20'] * 100)}% up its 20-day range")
    return "Bigger picture: " + ", ".join(parts)
