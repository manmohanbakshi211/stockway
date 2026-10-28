"""Strategy book: clear, testable rules for index options. The same code runs in the backtest and live.

Every strategy looks at a Ctx (one index, one minute) and may return a Plan: what to trade, where the idea is
wrong, the target, and when to give up. Positions and exits are handled by live/positions.py.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

import numpy as np

from .clock import IST


class DayArrays:
    """Today's 1-minute bars of one underlying as arrays, with running high/low/VWAP precomputed."""

    def __init__(self, bars, day: dt.date):
        a = np.asarray(bars, dtype=float).reshape(-1, 5)
        self.ts, self.o, self.h, self.l, self.c = (a[:, k] for k in range(5))
        self.n = len(a)
        t0 = dt.datetime.combine(day, dt.time(9, 15), IST).timestamp()
        self.minute = ((self.ts - t0) // 60).astype(int)
        self.hi = np.maximum.accumulate(self.h) if self.n else self.h
        self.lo = np.minimum.accumulate(self.l) if self.n else self.l
        tp = (self.h + self.l + self.c) / 3
        self.vw = np.cumsum(tp) / np.arange(1, self.n + 1) if self.n else tp
        self.above = (self.c > self.vw).astype(int)
        self.above_cum = np.concatenate([[0], np.cumsum(self.above)])
        self._or = {}

    def opening_range(self, n):
        if n not in self._or:
            m = self.minute < n
            self._or[n] = (float(self.h[m].max()), float(self.l[m].min())) if m.sum() >= max(3, n - 3) else (None, None)
        return self._or[n]


@dataclass
class Ctx:
    """One underlying at bar index i of today (completed bars only)."""
    sym: str
    d: DayArrays
    i: int
    step: float
    lot: int
    dte: int
    prev_close: float | None = None
    prev_high: float | None = None
    prev_low: float | None = None
    vix: float | None = None
    vix_prev: float | None = None
    quote: object = None           # quote(strike, side) -> price or None
    iv_of: object = None           # iv_of(strike, side) -> iv % or None
    _cache: dict = field(default_factory=dict)

    @property
    def minute(self):
        return int(self.d.minute[self.i])

    def bar(self, k=0):
        j = self.i + k
        d = self.d
        return d.ts[j], d.o[j], d.h[j], d.l[j], d.c[j]

    @property
    def spot(self):
        return float(self.d.c[self.i])

    @property
    def day_open(self):
        return float(self.d.o[0])

    @property
    def day_high(self):
        return float(self.d.hi[self.i])

    @property
    def day_low(self):
        return float(self.d.lo[self.i])

    def prior_high(self):
        return float(self.d.hi[self.i - 1]) if self.i else self.day_high

    def prior_low(self):
        return float(self.d.lo[self.i - 1]) if self.i else self.day_low

    @property
    def vwap(self):
        return float(self.d.vw[self.i])

    def opening_range(self, n=15):
        if self.minute < n:
            return None, None
        return self.d.opening_range(n)

    def recent_low(self, n):
        return float(self.d.l[max(0, self.i - n + 1): self.i + 1].min())

    def recent_high(self, n):
        return float(self.d.h[max(0, self.i - n + 1): self.i + 1].max())

    def vwap_side_count(self, n=30, above=True):
        a = self.d.above_cum
        lo = max(0, self.i + 1 - n)
        up = int(a[self.i + 1] - a[lo])
        return up if above else (self.i + 1 - lo) - up

    @property
    def atm(self):
        return round(self.spot / self.step) * self.step

    @property
    def atm_iv(self):
        if "iv" not in self._cache:
            xs = [self.iv_of(self.atm, s) for s in ("CE", "PE")] if self.iv_of else []
            xs = [x for x in xs if x and x == x and 0 < x < 200]
            self._cache["iv"] = sum(xs) / len(xs) if xs else None
        return self._cache["iv"]

    @property
    def daily_move(self):
        """One-day move (%) priced by options; falls back to India VIX."""
        if "dm" not in self._cache:
            iv = self.atm_iv
            if iv:
                v = iv / math.sqrt(252)
            elif self.vix:
                v = self.vix / math.sqrt(252) * (1.2 if self.sym == "BANKNIFTY" else 1.0)
            else:
                v = 0.8
            self._cache["dm"] = max(0.2, v)
        return self._cache["dm"]

    @property
    def range_x(self):
        return (self.day_high - self.day_low) / self.day_open * 100 / self.daily_move

    @property
    def efficiency(self):
        rng = self.day_high - self.day_low
        return abs(self.spot - self.day_open) / rng if rng > 0 else 0.0

    @property
    def vix_chg(self):
        if self.vix and self.vix_prev:
            return (self.vix / self.vix_prev - 1) * 100
        return None


@dataclass
class Plan:
    strategy: str
    direction: str                  # up / down / neutral
    structure: str                  # long / debit / credit_ic
    legs: list                      # [(strike, side, +1 buy / -1 sell)]
    stop_und: float | None = None   # underlying level where the idea is wrong
    target_und: float | None = None
    tp_frac: float | None = None    # credit: take profit at this share of the credit
    sl_mult: float | None = None    # credit: stop when the loss reaches this many times the credit
    trail_r: float | None = None    # after +1R move the stop to breakeven, then trail this many R behind the best price
    exit_min: int = 345             # minutes after 9:15 (345 = 15:00)
    note: str = ""


def _hm(h, m):
    return h * 60 + m - (9 * 60 + 15)


def _legs(ctx: Ctx, direction: str, structure: str, width_steps=2):
    side = "CE" if direction == "up" else "PE"
    k = ctx.atm
    if structure == "debit":
        far = k + width_steps * ctx.step if direction == "up" else k - width_steps * ctx.step
        return [(k, side, 1), (far, side, -1)]
    return [(k, side, 1)]


def _tgt(ctx, direction, stop, r):
    risk = max(abs(ctx.spot - stop), ctx.spot * 0.0008)
    return ctx.spot + r * risk if direction == "up" else ctx.spot - r * risk


# ---------------------------------------------------------------- strategies
def orb(ctx: Ctx, p: dict, st: dict):
    """Opening-range breakout: a narrow first 15/30 minutes, then a clean close beyond it."""
    n = p["or_min"]
    m = ctx.minute
    if m < n or m > _hm(11, 30) or st.get("done"):
        return None
    hi, lo = ctx.opening_range(n)
    if hi is None:
        return None
    if (hi - lo) / ctx.day_open * 100 > p["max_or_x"] * ctx.daily_move:
        st["done"] = True
        return None
    c = ctx.spot
    buf = p["buf"] / 100
    direction = "up" if c > hi * (1 + buf) else "down" if c < lo * (1 - buf) else None
    if not direction:
        return None
    st["done"] = True
    stop = (hi + lo) / 2 if p["stop"] == "mid" else (lo if direction == "up" else hi)
    return Plan("orb", direction, p["structure"], _legs(ctx, direction, p["structure"]), stop_und=stop,
                target_und=_tgt(ctx, direction, stop, p["tgt_r"]), trail_r=p.get("trail_r"), exit_min=_hm(15, 0),
                note=f"Broke the {n}-minute range {lo:,.0f}–{hi:,.0f}")


def vwap_pullback(ctx: Ctx, p: dict, st: dict):
    """Trend pullback: price has lived on one side of VWAP, dips to it, and turns back."""
    m = ctx.minute
    if m < _hm(10, 0) or m > _hm(14, 0) or st.get("n", 0) >= p["max_per_day"] or st.get("cool", -1) > m:
        return None
    vw = ctx.vwap
    _, _, h, l, c = ctx.bar(0)
    _, _, ph, pl, _ = ctx.bar(-1)
    move = (ctx.spot / ctx.day_open - 1) * 100
    for direction in ("up", "down"):
        above = direction == "up"
        if ctx.vwap_side_count(30, above) < p["side_bars"]:
            continue
        if (move if above else -move) < p["min_move_x"] * ctx.daily_move:
            continue
        touch = (l <= vw * (1 + p["touch"] / 100)) if above else (h >= vw * (1 - p["touch"] / 100))
        turn = (c > vw and c > ph) if above else (c < vw and c < pl)
        if touch and turn:
            st["n"] = st.get("n", 0) + 1
            st["cool"] = m + 30
            stop = ctx.recent_low(6) * (1 - 0.0005) if above else ctx.recent_high(6) * (1 + 0.0005)
            return Plan("vwap_pullback", direction, p["structure"], _legs(ctx, direction, p["structure"]),
                        stop_und=stop, target_und=_tgt(ctx, direction, stop, p["tgt_r"]), trail_r=p.get("trail_r"),
                        exit_min=_hm(15, 0), note=f"Trend pullback to VWAP {vw:,.0f}")
    return None


def gap_fade(ctx: Ctx, p: dict, st: dict):
    """A big gap that fails to extend in the first minutes tends to drift back toward yesterday's close."""
    if st.get("done") or not ctx.prev_close or ctx.minute < p["at_min"]:
        return None
    st["done"] = True
    gap = (ctx.day_open / ctx.prev_close - 1) * 100
    if abs(gap) < p["min_gap_x"] * ctx.daily_move:
        return None
    retraced = (ctx.day_open - ctx.spot) / (ctx.day_open - ctx.prev_close)
    if retraced < p["min_retrace"]:
        return None
    direction = "down" if gap > 0 else "up"
    stop = ctx.day_high * 1.0005 if direction == "down" else ctx.day_low * 0.9995
    tgt = ctx.spot + (ctx.prev_close - ctx.spot) * p["fill_frac"]
    return Plan("gap_fade", direction, p["structure"], _legs(ctx, direction, p["structure"]), stop_und=stop,
                target_und=tgt, exit_min=_hm(13, 0), note=f"Gap {gap:+.2f}% failing; target toward {ctx.prev_close:,.0f}")


def trend_day(ctx: Ctx, p: dict, st: dict):
    """Established trend day: ride a fresh day high/low after the morning."""
    m = ctx.minute
    if st.get("done") or m < _hm(11, 30) or m > _hm(13, 45):
        return None
    if ctx.efficiency < p["min_eff"] or ctx.range_x < p["min_range_x"]:
        return None
    c = ctx.spot
    up = c > ctx.day_open
    if up and c > ctx.prior_high() and c > ctx.vwap:
        direction = "up"
    elif not up and c < ctx.prior_low() and c < ctx.vwap:
        direction = "down"
    else:
        return None
    st["done"] = True
    stop = ctx.vwap
    return Plan("trend_day", direction, p["structure"], _legs(ctx, direction, p["structure"]), stop_und=stop,
                target_und=_tgt(ctx, direction, stop, p["tgt_r"]), trail_r=p.get("trail_r"), exit_min=_hm(15, 10),
                note=f"Trend day: {ctx.efficiency:.0%} efficient, {ctx.range_x:.1f}× priced move")


def expiry_breakout(ctx: Ctx, p: dict, st: dict):
    """Expiry afternoon: cheap options, big gamma. Trade a clean break of the day's range."""
    m = ctx.minute
    if ctx.dte != 0 or st.get("done") or m < _hm(13, 0) or m > _hm(14, 45):
        return None
    c = ctx.spot
    hi, lo = ctx.prior_high(), ctx.prior_low()
    if c > hi * (1 + p["buf"] / 100):
        direction = "up"
    elif c < lo * (1 - p["buf"] / 100):
        direction = "down"
    else:
        return None
    st["done"] = True
    risk = max(abs(c - ctx.vwap), c * 0.0015)
    stop = c - risk if direction == "up" else c + risk
    return Plan("expiry_breakout", direction, "long", _legs(ctx, direction, "long"), stop_und=stop,
                target_und=_tgt(ctx, direction, stop, p["tgt_r"]), trail_r=p.get("trail_r"), exit_min=_hm(15, 15),
                note="Expiry-day range break")


def range_condor(ctx: Ctx, p: dict, st: dict):
    """Hedged premium selling on a quiet range day: iron condor outside the day's range, wings cap the risk."""
    m = ctx.minute
    if st.get("done") or m < p["start_min"] or m > _hm(13, 30) or ctx.dte > p["max_dte"]:
        return None
    if ctx.range_x > p["max_range_x"] or ctx.efficiency > p["max_eff"]:
        return None
    if ctx.vix_chg is not None and ctx.vix_chg > p["max_vix_chg"]:
        return None
    s = ctx.step
    k_up = max(ctx.atm + p["short_steps"] * s, math.ceil(ctx.day_high / s) * s)
    k_dn = min(ctx.atm - p["short_steps"] * s, math.floor(ctx.day_low / s) * s)
    w = p["wing_steps"] * s
    legs = [(k_up, "CE", -1), (k_up + w, "CE", 1), (k_dn, "PE", -1), (k_dn - w, "PE", 1)]
    if ctx.quote:
        px = [ctx.quote(k, sd) for k, sd, _ in legs]
        if any(x is None for x in px):
            return None
        credit = px[0] - px[1] + px[2] - px[3]
        if credit < p["min_credit_frac"] * w:
            return None
    st["done"] = True
    return Plan("range_condor", "neutral", "credit_ic", legs, tp_frac=p["tp_frac"], sl_mult=p["sl_mult"],
                exit_min=_hm(15, 15), note=f"Quiet day ({ctx.range_x:.1f}× priced move): sell {k_dn:g} PE / {k_up:g} CE, wings {w:g}")


# name -> (function, default params, search grid for the backtest)
BOOK = {
    "orb": (orb, {"or_min": 15, "max_or_x": 0.6, "buf": 0.03, "stop": "mid", "tgt_r": 2.0, "trail_r": 1.0, "structure": "long"},
            {"or_min": [15, 30], "max_or_x": [0.4, 0.7, 9], "tgt_r": [1.5, 2.5], "structure": ["long", "debit"]}),
    "vwap_pullback": (vwap_pullback, {"side_bars": 26, "min_move_x": 0.3, "touch": 0.03, "tgt_r": 2.0, "trail_r": 1.0,
                                      "max_per_day": 2, "structure": "long"},
                      {"side_bars": [24, 28], "min_move_x": [0.2, 0.4], "tgt_r": [1.5, 2.5], "structure": ["long", "debit"]}),
    "gap_fade": (gap_fade, {"at_min": 15, "min_gap_x": 0.5, "min_retrace": 0.3, "fill_frac": 0.8, "structure": "long"},
                 {"min_gap_x": [0.4, 0.7], "min_retrace": [0.2, 0.4], "fill_frac": [0.6, 1.0], "structure": ["long", "debit"]}),
    "trend_day": (trend_day, {"min_eff": 0.6, "min_range_x": 0.7, "tgt_r": 2.0, "trail_r": 1.0, "structure": "long"},
                  {"min_eff": [0.55, 0.7], "min_range_x": [0.6, 0.9], "tgt_r": [1.5, 3.0], "structure": ["long", "debit"]}),
    "expiry_breakout": (expiry_breakout, {"buf": 0.02, "tgt_r": 3.0, "trail_r": 1.0},
                        {"buf": [0.0, 0.05], "tgt_r": [2.0, 4.0]}),
    "range_condor": (range_condor, {"start_min": 75, "max_range_x": 0.6, "max_eff": 0.5, "max_vix_chg": 3.0, "max_dte": 6,
                                    "short_steps": 2, "wing_steps": 2, "min_credit_frac": 0.12, "tp_frac": 0.5, "sl_mult": 1.0},
                     {"max_range_x": [0.5, 0.8], "short_steps": [2, 3], "tp_frac": [0.4, 0.6], "sl_mult": [1.0, 1.5]}),
}

LABELS = {"orb": "Opening-range breakout", "vwap_pullback": "VWAP trend pullback", "gap_fade": "Gap fade",
          "trend_day": "Trend-day continuation", "expiry_breakout": "Expiry-day breakout",
          "range_condor": "Range-day iron condor (hedged selling)"}
