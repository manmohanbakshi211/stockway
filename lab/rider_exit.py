"""Trend Rider exit engine (2.85): how a Trend Rider trade is managed, tick by tick, for paper and grading.

Mandeep (22 Nov 2026): "humara focus hai ki loss less ho, time limit tak sirf premium na ghista rahe agar koi movement
ka chance nahi hai toh, reversal ho toh trail humara profit bachaye rakhe but smart decision ne ye nahi ki sl hit
karwake aage ka achha move na le paaye. trade ka initial stoploss bhi achhi strong or confident ho."

The rules (numbers: rules/buy_setup.json "exit"; checked on 5-minute CLOSES of the underlying, never on a wick, except
the emergency stop on the premium which is checked on every tick):
  1. stop: a close beyond the setup's first stop (beyond the broken level plus an ATR buffer: lab/rider.py);
  2. lock: once the best move reaches lock_r x the risk, the stop moves to the entry (plus a small buffer), so a
     winner cannot turn into a loss; with 1 lot there is nothing to book half of, so locking is the booking;
  3. smart trail: after the lock, the stop follows the best price at trail_loose_atr ATRs while the trend holds, and
     tightens to trail_tight_atr when the closed bars show reversal signs (a rejection candle against the trade, two
     closes against it, a close beyond the previous bar's far end): a pullback alone does not shake it out;
  4. no move: before the lock, when nomove_bars closes after entry the underlying has not gone nomove_progress_atr
     ATRs the right way and the option is not above its entry, it is out: the premium does not melt to a time limit;
  5. emergency: the option down emergency_premium_pct from entry (a gap or a flash move), on any tick;
  6. time: out at exit_hhmm.
  7. 2.89, on trial (trail_mode "ema"; the default "atr" is exactly 2.85): the "10 EMA" video's trail. After the lock
     the stop stays at entry until the best move reaches ema_trail.after_r x the risk (the video's 1:2), then follows
     the EMA(ema_trail.n) of the 5-minute closes, carried from the setup's EMA at entry.

Gun: this module knows no symbol, date or threshold of its own. It is NOT part of the Lab bar table build (lab/factory
signature), so it can change without rebuilding the Lab's tables.
"""
from __future__ import annotations

import datetime as dt
import math

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
STYLE = "rider"
DEFAULT = {"lock_r": 1.0, "lock_buffer_atr": 0.05, "trail_loose_atr": 2.5, "trail_tight_atr": 1.5,
           "reversal_signs_for_tight": 2, "nomove_bars": 3, "nomove_progress_atr": 0.3, "emergency_premium_pct": 35.0,
           "exit_hhmm": "15:10", "trail_mode": "atr", "ema_trail": {"n": 10, "after_r": 2.0}}
WORDS = ("stop beyond the broken level + ATR buffer on 5-min closes; at +1R the stop moves to entry; then a trail of "
         "2.5 ATR that tightens to 1.5 ATR on reversal signs; out if no move in 3 bars; emergency stop -35% on the "
         "premium; out by 15:10")


def exit_cfg() -> dict:
    try:
        from .rider import cfg
        return {**DEFAULT, **(cfg().get("exit") or {})}
    except Exception:
        return dict(DEFAULT)


def _session_start(ts: float) -> float:
    d = dt.datetime.fromtimestamp(ts, IST)
    return d.replace(hour=9, minute=15, second=0, microsecond=0).timestamp()


class RiderPlan:
    def __init__(self, s0: float, d: int, stop: float, atr: float, entry: float, t0: float, c: dict | None = None,
                 best: float | None = None, locked: bool = False, bar: int | None = None, cur: list | None = None,
                 bars: list | None = None, held: int = 0, tight: bool = False, ema: float | None = None):
        self.s0, self.d, self.stop, self.atr, self.entry, self.t0 = float(s0), int(d), float(stop), float(atr), float(entry), float(t0)
        self.c = {**DEFAULT, **(c or {})}
        self.R = abs(self.s0 - self.stop)
        self.best = float(best) if best is not None else self.s0
        self.locked, self.held, self.tight = bool(locked), int(held), bool(tight)
        self.st0 = _session_start(self.t0)
        self.bar = bar if bar is not None else int((self.t0 - self.st0) // 300)
        self.cur = cur                  # [o, h, l, c] of the bar being built from ticks
        self.bars = list(bars or [])    # the last closed bars since entry, [o, h, l, c]
        self.ema = float(ema) if ema is not None else None     # 2.89: the trail EMA (trail_mode "ema")

    def to_dict(self) -> dict:
        return {"style": STYLE, "s0": self.s0, "d": self.d, "stop": self.stop, "atr": self.atr, "entry": self.entry,
                "t0": self.t0, "c": self.c, "best": self.best, "locked": self.locked, "bar": self.bar, "cur": self.cur,
                "bars": self.bars[-4:], "held": self.held, "tight": self.tight,
                **({"ema": self.ema} if self.ema is not None else {})}

    @classmethod
    def from_dict(cls, x: dict) -> "RiderPlan":
        return cls(x["s0"], x["d"], x["stop"], x["atr"], x["entry"], x["t0"], x.get("c"), x.get("best"), x.get("locked", False),
                   x.get("bar"), x.get("cur"), x.get("bars"), x.get("held", 0), x.get("tight", False), x.get("ema"))

    # ------------------------------------------------------------------ the rules
    def _signs(self) -> int:
        """Reversal signs in the closed bars: a rejection candle against the trade, two closes against it, a close
        beyond the previous bar's far end."""
        d, b = self.d, self.bars
        n = 0
        if b:
            o, h, l, c = b[-1]
            rng = h - l
            if rng > 0:
                wick = (h - max(o, c)) if d > 0 else (min(o, c) - l)
                if wick >= 0.5 * rng and (c - l if d > 0 else h - c) <= 0.5 * rng:
                    n += 1
        if len(b) >= 3 and d * (b[-1][3] - b[-2][3]) < 0 and d * (b[-2][3] - b[-3][3]) < 0:
            n += 1
        if len(b) >= 2 and d * (b[-1][3] - (b[-2][2] if d > 0 else b[-2][1])) < 0:
            n += 1
        return n

    def _on_close(self, bar: list, opt: float | None) -> str | None:
        o, h, l, c = bar
        d, c_ = self.d, self.c
        self.bars = (self.bars + [bar])[-4:]
        self.held += 1
        self.best = max(self.best, h) if d > 0 else min(self.best, l)
        if d * (c - self.stop) <= 0:
            return "trail" if self.locked else "stop"
        if not self.locked and self.R > 0 and d * (self.best - self.s0) >= float(c_["lock_r"]) * self.R:
            self.locked = True
            be = self.s0 + d * float(c_["lock_buffer_atr"]) * self.atr
            self.stop = max(self.stop, be) if d > 0 else min(self.stop, be)
        if str(c_.get("trail_mode", "atr")) == "ema":          # 2.89: the 10 EMA video's trail (on trial)
            et = c_.get("ema_trail") or {}
            n = int(et.get("n", 10))
            self.ema = c if self.ema is None else self.ema + 2.0 / (n + 1) * (c - self.ema)
            if self.locked:
                if self.R > 0 and d * (self.best - self.s0) >= float(et.get("after_r", 2.0)) * self.R:
                    self.stop = max(self.stop, self.ema) if d > 0 else min(self.stop, self.ema)
                if d * (c - self.stop) <= 0:
                    return "trail"
                return None
        elif self.locked:
            self.tight = self._signs() >= int(c_["reversal_signs_for_tight"])
            k = float(c_["trail_tight_atr"] if self.tight else c_["trail_loose_atr"])
            lvl = self.best - d * k * self.atr
            self.stop = max(self.stop, lvl) if d > 0 else min(self.stop, lvl)
            if d * (c - self.stop) <= 0:
                return "trail"
            return None
        if self.held >= int(c_["nomove_bars"]) and d * (c - self.s0) < float(c_["nomove_progress_atr"]) * self.atr \
                and (opt is None or opt <= self.entry):
            return "no move"
        return None

    def check(self, spot: float | None, opt: float | None, now_ts: float) -> str | None:
        """Feed every tick (underlying spot, option price, time); returns a reason to exit or None."""
        c_ = self.c
        if opt and opt <= self.entry * (1 - float(c_["emergency_premium_pct"]) / 100):
            return "premium stop"
        why = None
        if spot and math.isfinite(spot):
            k = int((now_ts - self.st0) // 300)
            if self.cur is not None and k > self.bar:
                why = self._on_close(self.cur, opt)
                self.cur = None
            self.bar = max(self.bar, k)
            if self.cur is None:
                self.cur = [spot, spot, spot, spot]
            else:
                self.cur = [self.cur[0], max(self.cur[1], spot), min(self.cur[2], spot), spot]
        if why:
            return why
        if dt.datetime.fromtimestamp(now_ts, IST).strftime("%H:%M") >= str(c_["exit_hhmm"]):
            return "time"
        return None

    def levels(self) -> dict:
        return {"stop": round(self.stop, 2), "target": None, "one_r": round(self.s0 + self.d * self.R, 2),
                "locked": self.locked, "tight": self.tight}
