"""Chart-level exit (3.08, Stockway Pro v2): targets and stops from the chart, not from a premium percentage.

Mandeep (25 Nov 2026): "system entry exit koi percentage se nahi chart, index/stock price levels, indicators,
Resistance support in cheejon se entry exit banaya ... high profit low loss and max green days."

Tested before it was built (7 replayed days, Pro's own entries, real option prices): on the strong index signals and
the confirmed stock signals it made more than the Trend Rider exit (index +Rs11,165 -> +Rs18,119 a lot, 36% -> 57%
won; confirmed stocks +Rs15,664 -> +Rs27,203, 29% -> 48%); on every stock signal it made less, so it goes with the v2
entries only.

The rules (numbers: rules/pro_v2.json "exit", an account's own "exit" on top):
  1. targets: T1 / T2 = the next support / resistance zones in the trade's direction (live/levels.key_levels over
     yesterday's high / low / close, pivots, last week's and 20-day high / low, today's day high / low and opening
     range, round numbers), at least t1_min_atr ATRs away; none -> T1 = 2R, T2 = T1 + (T1 - entry);
  2. stop: the setup's own chart stop, on a 5-minute close of the underlying (stop_on "1m": a 1-minute close, the
     early-breakout trades), never on a wick;
  3. before T1: out on a failed breakout (a close back beyond the broken level) and on no move (nomove_bars closes on
     the wrong side of the entry level);
  4. at T1 (any tick): the stop moves to the entry level and the trade becomes the runner (split_at_t1: a 2-lot trade
     in two parts, the "t1" part sold there; the 7-day replay found that part added ~nothing, so it is off);
  5. the runner (runner): "bar5" trails behind the last closed 5-minute bar and is sold at T2; "chand" trails
     chand_atr ATRs behind the best price on every tick, no T2 (the trailing account's Chandelier exit); "ema10"
     follows the 10 EMA of the 5-minute closes, no T2 (the 10 EMA video);
  6. emergency: the option down emergency_premium_pct from entry, on any tick; optional profit lock on a 1-lot trade
     (lock_premium_pct -> keep lock_keep_pct); out at exit_hhmm.

Gun: no symbol, date or number of its own beyond the defaults below; paper only.
"""
from __future__ import annotations

import datetime as dt
import math

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
STYLE = "chart"
RUNNERS = ("bar5", "chand", "ema10")
ROLES = ("solo", "t1", "run")
DEFAULT = {"runner": "bar5", "split_at_t1": False, "t1_min_atr": 0.25, "levels_n": 6, "stop_on": "5m",
           "failed_breakout": True, "nomove_bars": 6, "chand_atr": 2.5, "ema_n": 10, "emergency_premium_pct": 35.0,
           "exit_hhmm": "15:10", "lock_premium_pct": None, "lock_keep_pct": 5.0}
WORDS = {"bar5": "after T1 the stop goes to the entry level and trails behind the last 5-minute bar; out at T2",
         "chand": "after T1 the stop goes to the entry level, then trails 2.5 ATR behind the best price (Chandelier)",
         "ema10": "after T1 the stop goes to the entry level, then follows the 10 EMA of the 5-minute closes"}
REASONS = {"stop": "chart stop", "failed": "failed breakout", "no move": "no move", "t1": "target 1", "t2": "target 2",
           "trail": "trailing stop", "premium stop": "stop loss", "profit lock": "profit lock", "time": "time limit"}


def cfg(over: dict | None = None) -> dict:
    """rules/pro_v2.json "exit" on top of the defaults, an account's own exit on top of that."""
    base = dict(DEFAULT)
    try:
        from live import pro2
        base.update(pro2.cfg().get("exit") or {})
    except Exception:
        pass
    base.update(over or {})
    return base


def pivots(h: float, l: float, c: float) -> dict:
    p = (h + l + c) / 3
    return {"Pivot": p, "R1": 2 * p - l, "S1": 2 * p - h, "R2": p + (h - l), "S2": p - (h - l)}


def named_levels(daily: dict | None, bars: dict | None, t0: float | None = None, today: str | None = None) -> list:
    """[(source name, price)] known at entry: yesterday's high / low / close and pivots, last week's and 20-day high /
    low (finished days only), today's day high / low and opening range (first three 5-minute bars) so far."""
    out = []
    d = daily or {}
    prev = d.get("prev") or {}
    if prev.get("h") and prev.get("l") and prev.get("c"):
        h, l, c = float(prev["h"]), float(prev["l"]), float(prev["c"])
        out += [("Yesterday's high", h), ("Yesterday's low", l), ("Yesterday's close", c)]
        out += list(pivots(h, l, c).items())
    rows = [r for r in (d.get("d_ohlc") or []) if not today or str(r[0])[:10] < today]
    if len(rows) >= 5:
        out += [("Last week's high", max(float(x[2]) for x in rows[-5:])), ("Last week's low", min(float(x[3]) for x in rows[-5:]))]
    if len(rows) >= 20:
        out += [("20-day high", max(float(x[2]) for x in rows[-20:])), ("20-day low", min(float(x[3]) for x in rows[-20:]))]
    if bars and bars.get("h") is not None and len(bars["h"]):
        ts = list(bars.get("t") if bars.get("t") is not None else [])
        hs, ls = list(bars["h"]), list(bars["l"])
        if t0 is not None and ts:
            keep = [i for i, t in enumerate(ts) if float(t) + 300 <= float(t0) + 1]
            hs, ls = [hs[i] for i in keep], [ls[i] for i in keep]
        if hs:
            out += [("Day high", float(max(hs))), ("Day low", float(min(ls)))]
            if len(hs) >= 3:
                out += [("Day high", float(max(hs[:3]))), ("Day low", float(min(ls[:3])))]
    return out


def targets(s0: float, d: int, atr: float, stop: float, named: list, c: dict | None = None) -> tuple:
    """(T1, T2): the next two support / resistance zones in direction d at least t1_min_atr ATRs away."""
    from live.levels import key_levels
    c = {**DEFAULT, **(c or {})}
    R = abs(float(s0) - float(stop)) or (float(atr) if atr else float(s0) * 0.003)
    a = float(atr) if atr and math.isfinite(float(atr)) and atr > 0 else R
    kl = key_levels(float(s0), list(named or []), n=int(c.get("levels_n", 6)))
    side = kl["resistance"] if d > 0 else kl["support"]
    far = [z["price"] for z in side if (z["price"] - s0) * d >= float(c.get("t1_min_atr", 0.25)) * a]
    t1 = far[0] if far else s0 + d * 2 * R
    t2 = far[1] if len(far) > 1 else t1 + d * abs(t1 - s0)
    return round(float(t1), 2), round(float(t2), 2)


def _session_start(ts: float) -> float:
    x = dt.datetime.fromtimestamp(ts, IST)
    return x.replace(hour=9, minute=15, second=0, microsecond=0).timestamp()


class ChartPlan:
    """One part of a chart-exit trade, fed every tick (underlying spot, option price, time)."""

    def __init__(self, s0, d, stop, atr, entry, t0, t1, t2, level=None, role="solo", c=None, hit1=False, best=None,
                 bar=None, cur=None, n5=0, bar1=None, cur1=None, ema=None, bo=None, last5=None):
        self.s0, self.d, self.stop, self.atr = float(s0), int(d), float(stop), float(atr or 0) or abs(float(s0) - float(stop))
        self.entry, self.t0, self.t1, self.t2 = float(entry), float(t0), float(t1), float(t2)
        self.level = float(level) if level is not None else None
        self.role = role if role in ROLES else "solo"
        self.c = {**DEFAULT, **(c or {})}
        self.hit1, self.n5 = bool(hit1), int(n5)
        self.best = float(best) if best is not None else self.s0
        self.st0 = _session_start(self.t0)
        self.bar = bar if bar is not None else int((self.t0 - self.st0) // 300)
        self.cur = cur
        self.bar1 = bar1 if bar1 is not None else int((self.t0 - self.st0) // 60)
        self.cur1 = cur1
        self.ema = float(ema) if ema is not None else None
        self.bo = float(bo) if bo is not None else None
        self.last5 = last5                                   # the last closed 5-minute bar [o, h, l, c]

    def to_dict(self) -> dict:
        return {"style": STYLE, "s0": self.s0, "d": self.d, "stop": self.stop, "atr": self.atr, "entry": self.entry,
                "t0": self.t0, "t1": self.t1, "t2": self.t2, "level": self.level, "role": self.role, "c": self.c,
                "hit1": self.hit1, "best": self.best, "bar": self.bar, "cur": self.cur, "n5": self.n5, "bar1": self.bar1,
                "cur1": self.cur1, "ema": self.ema, "bo": self.bo, "last5": self.last5}

    @classmethod
    def from_dict(cls, x: dict) -> "ChartPlan":
        return cls(x["s0"], x["d"], x["stop"], x["atr"], x["entry"], x["t0"], x["t1"], x["t2"], x.get("level"),
                   x.get("role", "solo"), x.get("c"), x.get("hit1", False), x.get("best"), x.get("bar"), x.get("cur"),
                   x.get("n5", 0), x.get("bar1"), x.get("cur1"), x.get("ema"), x.get("bo"), x.get("last5"))

    # ------------------------------------------------------------------ the rules
    def _tighten(self, lvl: float):
        self.stop = max(self.stop, lvl) if self.d > 0 else min(self.stop, lvl)

    def _close_check(self, close: float, tf: str) -> str | None:
        """A closed bar of the stop's timeframe: the stop, then (before T1) a failed breakout."""
        d = self.d
        if d * (close - self.stop) <= 0:
            return "trail" if self.hit1 else "stop"
        if not self.hit1 and self.c.get("failed_breakout", True) and self.level is not None and d * (close - self.level) < 0:
            return "failed"
        return None

    def _on_close5(self, bar: list) -> str | None:
        o, h, l, c = bar
        d, c_ = self.d, self.c
        self.n5 += 1
        self.last5 = [o, h, l, c]
        n = int(c_.get("ema_n", 10))
        self.ema = c if self.ema is None else self.ema + 2.0 / (n + 1) * (c - self.ema)
        if str(c_.get("stop_on", "5m")) == "5m" or self.hit1:
            why = self._close_check(c, "5m")             # the stop as it stood before this bar
            if why:
                return why
        if self.hit1 and self.role != "t1":
            rn = c_.get("runner", "bar5")
            if rn == "bar5":
                self._tighten(l if d > 0 else h)
            elif rn == "ema10" and self.ema is not None:
                self._tighten(self.ema)
        if not self.hit1 and self.n5 >= int(c_.get("nomove_bars", 6)) and d * (c - self.s0) < 0:
            return "no move"
        return None

    def check(self, spot: float | None, opt: float | None, now_ts: float) -> str | None:
        """Feed every tick; returns a reason to exit (see REASONS) or None."""
        c_, d = self.c, self.d
        if opt and opt <= self.entry * (1 - float(c_["emergency_premium_pct"]) / 100):
            return "premium stop"
        lp = c_.get("lock_premium_pct")
        if lp and self.role == "solo" and opt and self.entry > 0:
            self.bo = max(self.bo if self.bo is not None else self.entry, float(opt))
            if self.bo >= self.entry * (1 + float(lp) / 100) and opt <= self.entry * (1 + float(c_.get("lock_keep_pct") or 0) / 100):
                return "profit lock"
        why = None
        if spot and math.isfinite(spot):
            # bars closed by this tick
            k1 = int((now_ts - self.st0) // 60)
            if self.cur1 is not None and k1 > self.bar1:
                if str(c_.get("stop_on", "5m")) == "1m" and not self.hit1:
                    why = self._close_check(self.cur1[3], "1m")
                self.cur1 = None
            self.bar1 = max(self.bar1, k1)
            self.cur1 = [spot, spot, spot, spot] if self.cur1 is None else \
                [self.cur1[0], max(self.cur1[1], spot), min(self.cur1[2], spot), spot]
            k = int((now_ts - self.st0) // 300)
            if self.cur is not None and k > self.bar:
                why = why or self._on_close5(self.cur)
                self.cur = None
            self.bar = max(self.bar, k)
            self.cur = [spot, spot, spot, spot] if self.cur is None else \
                [self.cur[0], max(self.cur[1], spot), min(self.cur[2], spot), spot]
            if why:
                return why
            fav = d * (spot - self.best) > 0
            if fav:
                self.best = spot
            if not self.hit1 and d * (spot - self.t1) >= 0:
                self.hit1 = True
                if self.role == "t1":
                    return "t1"
                self._tighten(self.s0)
            if self.hit1 and self.role != "t1":
                rn = c_.get("runner", "bar5")
                if rn == "bar5" and d * (spot - self.t2) >= 0:
                    return "t2"
                if rn == "chand":
                    self._tighten(self.best - d * float(c_.get("chand_atr", 2.5)) * self.atr)
                    if d * (spot - self.stop) <= 0:
                        return "trail"
        if dt.datetime.fromtimestamp(now_ts, IST).strftime("%H:%M") >= str(c_["exit_hhmm"]):
            return "time"
        return None

    def levels(self) -> dict:
        return {"stop": round(self.stop, 2), "target": round(self.t2 if self.hit1 else self.t1, 2), "t1": self.t1,
                "t2": self.t2, "hit1": self.hit1, "role": self.role}
