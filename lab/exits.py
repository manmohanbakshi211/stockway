"""Where to get out: stop-loss and targets set on the chart, the way professional traders do it.

Each style is a challenger to the app's current exits (a fixed % of the option premium). They are tested on the same
history in the Strategy Lab and graded live in shadow; one replaces the current exits only if it wins out of sample.

The stop and target are levels on the underlying (index or stock) chart; the trade is an option, so the result is the
option's price at the moment a level is touched. Every style also keeps a safety stop on the premium (−40%).

    atr2r  Van Tharp's R-multiples with J. Welles Wilder's ATR: stop 1 ATR away, target 2R.
    bar2r  Linda Raschke / Al Brooks: stop just beyond the signal candle (kept between 0.5 and 2 ATR), target 2R.
    be3r   Stop 1 ATR; at +1R the stop moves to entry (a free trade); target 3R. One lot can't be split, so this is
           the small-account form of "book half at 1R".
    chand  Chuck LeBeau's Chandelier exit: stop 1.5 ATR, then trails the best price minus 2.5 ATR; no fixed target.

ATR = Average True Range, 14 bars of 5 minutes (Wilder smoothing), in points. R = the distance from entry to the stop.
"""
from __future__ import annotations

import math

import numpy as np

PREM_STOP = 40.0              # % of the premium: the safety stop every style keeps (theta / IV crush)

STYLES = {
    "atr2r": {"stop": ("atr", 1.0), "target_r": 2.0, "be_at": None, "trail_atr": None, "max": 120,
              "who": "Van Tharp (R-multiples) + Wilder ATR",
              "words": "chart stop 1×ATR(14, 5-min), target 2R, max 2 h (premium stop −40%)"},
    "bar2r": {"stop": ("bar", None), "target_r": 2.0, "be_at": None, "trail_atr": None, "max": 120,
              "who": "Linda Raschke / Al Brooks (signal candle)",
              "words": "stop just beyond the signal candle, target 2R, max 2 h (premium stop −40%)"},
    "be3r": {"stop": ("atr", 1.0), "target_r": 3.0, "be_at": 1.0, "trail_atr": None, "max": 180,
             "who": "R-multiples with a breakeven stop",
             "words": "chart stop 1×ATR; at +1R stop to entry (risk-free); target 3R, max 3 h (premium stop −40%)"},
    "chand": {"stop": ("atr", 1.5), "target_r": None, "be_at": None, "trail_atr": 2.5, "max": 400,
              "who": "Chuck LeBeau (Chandelier exit)",
              "words": "stop 1.5×ATR, then trail best price − 2.5×ATR; no fixed target; out by 15:15 (premium stop −40%)"},
}


def atr14(h, l, c) -> np.ndarray:
    """Wilder ATR(14) of 5-minute bars, in points (NaN for the first 13 bars)."""
    h, l, c = (np.asarray(x, float) for x in (h, l, c))
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    out = np.full(len(tr), np.nan)
    n = 14
    if len(tr) < n:
        return out
    out[n - 1] = np.nanmean(tr[:n])
    for i in range(n, len(tr)):
        out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


def risk_pts(style: str, s0: float, d: int, atr: float, bar_lo: float | None = None, bar_hi: float | None = None) -> float:
    """R: distance from entry to the initial stop, in points of the underlying."""
    st = STYLES[style]
    if not atr or not math.isfinite(atr) or atr <= 0:
        return math.nan
    kind, k = st["stop"]
    if kind == "atr":
        return k * atr
    lvl = (bar_lo - 0.1 * atr) if d > 0 else (bar_hi + 0.1 * atr)
    r = (s0 - lvl) * d if lvl is not None and math.isfinite(lvl) else atr
    return float(min(2 * atr, max(0.5 * atr, r)))


def first_exit(style: str, spot, opt, s0: float, entry: float, d: int, R: float):
    """Backtest: index of the first minute an exit fires in spot/opt (minutes after entry), and why. (None, 'time')
    if nothing fires. Same rule as LivePlan.check, minute by minute."""
    st = STYLES[style]
    spot = np.asarray(spot, float)
    opt = np.asarray(opt, float)
    if not len(spot) or not math.isfinite(R) or R <= 0:
        return None, "time"
    move = d * (spot - s0)
    best = np.maximum.accumulate(np.nan_to_num(move, nan=-1e18))
    prev_best = np.r_[0.0, best[:-1]]
    prev_best = np.maximum(prev_best, 0.0)
    stop = np.full(len(move), -R)
    if st["be_at"]:
        stop = np.where(prev_best >= st["be_at"] * R, 0.0, stop)
    if st["trail_atr"]:
        atr = R / st["stop"][1]
        stop = np.maximum(stop, prev_best - st["trail_atr"] * atr)
    reasons = [("stop", move <= stop), ("premium stop", np.nan_to_num(opt, nan=1e18) <= entry * (1 - PREM_STOP / 100))]
    if st["target_r"]:
        reasons.append(("target", move >= st["target_r"] * R))
    first, why = None, "time"
    for name, hit in reasons:
        hit = hit & ~np.isnan(move) if name != "premium stop" else hit
        if hit.any():
            k = int(np.argmax(hit))
            if first is None or k < first:
                first, why = k, name
    return first, why


class LivePlan:
    """The same exit, tick by tick, for paper trades and live shadow grading."""

    def __init__(self, style: str, s0: float, d: int, R: float, entry: float, t0: float, best: float = 0.0):
        self.style, self.s0, self.d, self.R, self.entry, self.t0, self.best = style, s0, d, R, entry, t0, best

    def to_dict(self) -> dict:
        return {"style": self.style, "s0": self.s0, "d": self.d, "R": self.R, "entry": self.entry, "t0": self.t0, "best": self.best}

    @classmethod
    def from_dict(cls, x: dict) -> "LivePlan":
        return cls(x["style"], x["s0"], x["d"], x["R"], x["entry"], x["t0"], x.get("best", 0.0))

    def stop_move(self) -> float:
        st = STYLES[self.style]
        stop = -self.R
        if st["be_at"] and self.best >= st["be_at"] * self.R:
            stop = 0.0
        if st["trail_atr"]:
            stop = max(stop, self.best - st["trail_atr"] * self.R / st["stop"][1])
        return stop

    def check(self, spot: float | None, opt: float | None, now_ts: float) -> str | None:
        st = STYLES[self.style]
        why = None
        if opt and opt <= self.entry * (1 - PREM_STOP / 100):
            why = "premium stop"
        if spot:
            move = self.d * (spot - self.s0)
            if move <= self.stop_move():
                why = why or "stop"
            elif st["target_r"] and move >= st["target_r"] * self.R:
                why = why or "target"
            self.best = max(self.best, move)
        if not why and (now_ts - self.t0) / 60 >= st["max"]:
            why = "time"
        return why

    def levels(self) -> dict:
        st = STYLES[self.style]
        d = self.d
        return {"stop": round(self.s0 + d * self.stop_move(), 2),
                "target": round(self.s0 + d * st["target_r"] * self.R, 2) if st["target_r"] else None,
                "one_r": round(self.s0 + d * self.R, 2)}


def plan(style: str, s0: float, d: int, atr: float, opt_ltp: float | None, delta: float | None, lot: int | None,
         bar_lo=None, bar_hi=None, capital: float = 40000, risk_pct: float = 5.0, lot_max: float = 10000) -> dict | None:
    """A trade plan you can act on: entry, stop and targets on the chart and (estimated) on the option."""
    R = risk_pts(style, s0, d, atr, bar_lo, bar_hi)
    if not math.isfinite(R) or R <= 0:
        return None
    st = STYLES[style]
    out = {"style": style, "who": st["who"], "words": st["words"], "atr": round(atr, 2), "R_pts": round(R, 2),
           "und_entry": round(s0, 2), "und_stop": round(s0 - d * R, 2), "und_1r": round(s0 + d * R, 2),
           "und_target": round(s0 + d * st["target_r"] * R, 2) if st["target_r"] else None,
           "rr": st["target_r"], "max_min": min(st["max"], 360)}
    if opt_ltp and lot:
        dl = abs(delta) if delta else 0.5
        o_stop = max(opt_ltp * (1 - PREM_STOP / 100), opt_ltp - dl * R)
        o_tgt = opt_ltp + dl * st["target_r"] * R if st["target_r"] else None
        loss = (opt_ltp - o_stop) * lot
        out.update(opt_entry=round(opt_ltp, 2), opt_entry_max=round(opt_ltp * 1.02, 2), opt_stop=round(o_stop, 2),
                   opt_target=round(o_tgt, 2) if o_tgt else None, cost=round(opt_ltp * lot), max_loss=round(loss),
                   lots=1 if (opt_ltp * lot <= lot_max and loss <= capital * risk_pct / 100) else 0)
        cost, cap_risk = opt_ltp * lot, capital * risk_pct / 100
        out["fits"] = (f"₹{capital / 1000:.0f}K: 1 lot fits" if out["lots"] else
                       f"too big for ₹{capital / 1000:.0f}K (lot ₹{cost / 1000:.1f}K > ₹{lot_max / 1000:.0f}K)" if cost > lot_max else
                       f"too risky for ₹{capital / 1000:.0f}K (loss ₹{loss:,.0f} > ₹{cap_risk:,.0f})")
    return out
