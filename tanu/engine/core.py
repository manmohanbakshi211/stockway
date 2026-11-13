"""The engine (ADR-0004): one code path for history replay and live paper. It receives 1-minute underlying bars in
order, keeps each symbol's day state incrementally, evaluates the setups, opens option positions through an option
price provider and manages their exits. Time comes only from the bars (never the wall clock), so the same bars give
the same trades.

Features per minute m (all known at the close of minute m):
  m (minute of the session), day / rel / move (tanu.state.day.classify), burst (15-minute move / normal day range),
  twap_rel (distance from the day's average price / normal day range), or_pos (position vs the 15-minute opening
  range, +-0.5 at its edges). Direction-adjusted copies: d_move, d_burst, d_twap_rel, d_or_pos.
  Morning (0.10.0, % of the previous close): gap (09:15 open vs previous close), first5 (close of the first 5-minute
  candle, 09:19, vs previous close; from 09:19), first5_body (that candle's close - open; negative = red),
  vs_first5 (now vs that candle's close). long = 1, so d_long is the direction (d_long > 0: calls only).
  Higher timeframes (0.11.0, tanu.engine.tf): ealert_<tf>m_<n> / ealert_<tf>m_<n>_stop, built only when a setup
  names them.
Fills: a signal at the close of minute m fills at the option's open of minute m+1, paying half the spread plus one
tick; exits receive half the spread minus one tick; charges at the day's rates. Theta is inside the option prices.
"""
from __future__ import annotations

import collections
import datetime as dt
from dataclasses import dataclass, field

from ..data.facts import Facts
from ..setups.registry import OPS, Setup
from ..state.day import DayContext, classify
from .tf import EmaAlerts, needed

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
OPEN_MIN = 9 * 60 + 15


def minute_of(t: float) -> int:
    d = dt.datetime.fromtimestamp(t, IST)
    return d.hour * 60 + d.minute - OPEN_MIN


def hm_to_min(hm: str) -> int:
    h, m = hm.split(":")
    return int(h) * 60 + int(m) - OPEN_MIN


@dataclass
class Position:
    setup: str
    sym: str
    day: str
    d: int
    strike: float
    side: str
    signal_t: float
    state: dict
    entry_t: float | None = None
    entry: float | None = None
    und_entry: float | None = None
    stop: float | None = None
    best: float | None = None
    peak: float | None = None
    trough: float | None = None
    und_exit: float | None = None
    exit_t: float | None = None
    exit: float | None = None
    reason: str | None = None


@dataclass
class SymState:
    sym: str
    ctx: DayContext
    day: str
    open_: float | None = None
    hi: float = float("-inf")
    lo: float = float("inf")
    twap_sum: float = 0.0
    n: int = 0
    closes: collections.deque = field(default_factory=lambda: collections.deque(maxlen=16))
    or_hi: float = float("-inf")
    or_lo: float = float("inf")
    bar5: list = field(default_factory=list)
    atr: float | None = None
    prev_feat: dict = field(default_factory=dict)
    dte: int | None = None
    traded: collections.Counter = field(default_factory=collections.Counter)
    c5: float | None = None
    tfs: list = field(default_factory=list)        # EmaAlerts trackers the setups ask for                     # close of the first 5-minute candle (minute 4, 09:19)


class Engine:
    def __init__(self, setups: list[Setup], options, *, spread_pct: dict | None = None, tick: float = 0.05,
                 facts: Facts | None = None, lots: dict | None = None):
        """options: provider with atm(sym, day, t, spot, side) -> strike | None and
        bar(sym, day, strike, side, t) -> (o, h, l, c) | None."""
        self.setups = setups
        self.opt = options
        self.spread_pct = spread_pct or {}
        self.tick = tick
        self.facts = facts or Facts()
        self.lots = lots or {}
        self.sym: dict[str, SymState] = {}
        self.carry_atr: dict[str, float] = {}
        self.open: list[Position] = []
        self.closed: list[Position] = []
        self.signals: list[dict] = []
        self.carry_ema: dict[tuple, float] = {}
        names = [c[0] for s in setups for c in s.when] + [s.exit.stop_feature for s in setups]
        self.tf_needed = sorted(needed(names))
        self.listeners: list = []           # objects with any of on_signal / on_fill / on_close(engine, position)

    def _emit(self, what: str, p: Position) -> None:
        for li in self.listeners:
            fn = getattr(li, what, None)                # a listener implements only the events it needs
            if fn is None:
                continue
            try:
                fn(self, p)
            except Exception as e:                      # a listener (account, journal, alert) can never stop the engine
                p.state.setdefault("listener_errors", []).append(f"{type(li).__name__}.{what}: {e}")

    # ---------------------------------------------------------------- day lifecycle
    def start_day(self, sym: str, day: str, ctx: DayContext) -> None:
        dte = None
        if hasattr(self.opt, "expiry"):
            exp = self.opt.expiry(sym, day)
            dte = (dt.date.fromisoformat(exp) - dt.date.fromisoformat(day)).days if exp else None
        self.sym[sym] = SymState(sym=sym, ctx=ctx, day=day, atr=self.carry_atr.get(sym), dte=dte,
                                 tfs=[EmaAlerts(tf, n, self.carry_ema.get((sym, tf, n))) for tf, n in self.tf_needed])

    def end_day(self, sym: str) -> None:
        st = self.sym.pop(sym, None)
        if st and st.atr:
            self.carry_atr[sym] = st.atr
        for a in (st.tfs if st else []):
            if a.ema is not None:
                self.carry_ema[(sym, a.tf, a.n)] = a.ema
        for p in [p for p in self.open if p.sym == sym]:      # anything still open (no prices): closed unfilled
            p.reason = p.reason or "no_exit_price"
            self.open.remove(p)
            self.closed.append(p)
            if p.entry is not None:
                self._emit("on_close", p)

    # ---------------------------------------------------------------- bars
    def on_bar(self, sym: str, t: float, o: float, h: float, l: float, c: float) -> None:
        st = self.sym[sym]
        m = minute_of(t)
        self._update(st, m, o, h, l, c)
        feat = self._features(st, m, c)
        for a in st.tfs:
            sig, stop = a.on_bar(m, o, h, l, c)
            feat[a.key], feat[a.key + "_stop"] = sig, stop
        self._manage(st, t, m, h, l, c)
        self._signals(st, t, m, c, feat)
        st.prev_feat = feat

    def _update(self, st: SymState, m: int, o, h, l, c) -> None:
        if st.open_ is None:
            st.open_ = o
        st.hi, st.lo = max(st.hi, h), min(st.lo, l)
        st.twap_sum += c
        st.n += 1
        st.closes.append(c)
        if m == 4:
            st.c5 = c
        if m < 15:
            st.or_hi, st.or_lo = max(st.or_hi, h), min(st.or_lo, l)
        st.bar5.append((h, l, c))
        if len(st.bar5) == 5:                                    # a 5-minute bar closed: Wilder ATR(14)
            hh, ll = max(b[0] for b in st.bar5), min(b[1] for b in st.bar5)
            prev_c = st.closes[-6] if len(st.closes) >= 6 else st.bar5[0][2]
            tr = max(hh - ll, abs(hh - prev_c), abs(ll - prev_c))
            st.atr = tr if st.atr is None else st.atr + (tr - st.atr) / 14
            st.bar5 = []

    def _features(self, st: SymState, m: int, c: float) -> dict:
        pc, med = st.ctx.prev_close, st.ctx.med_range
        rng = (st.hi - st.lo) / pc * 100
        move = (c - st.open_) / pc * 100
        day, rel, sign = classify(rng, move, m, st.ctx)
        f = {"m": m, "day": day, "rel": rel, "move": sign, "dte": st.dte}
        f["burst"] = ((c - st.closes[0]) / pc * 100 / med) if len(st.closes) == 16 and med else None
        f["twap_rel"] = ((c - st.twap_sum / st.n) / pc * 100 / med) if med else None
        f["or_pos"] = ((c - (st.or_hi + st.or_lo) / 2) / (st.or_hi - st.or_lo)) if m >= 15 and st.or_hi > st.or_lo else None
        f["gap"] = (st.open_ - pc) / pc * 100
        f["first5"] = (st.c5 - pc) / pc * 100 if st.c5 is not None else None
        f["first5_body"] = (st.c5 - st.open_) / pc * 100 if st.c5 is not None else None
        f["vs_first5"] = (c - st.c5) / pc * 100 if st.c5 is not None else None
        f["long"] = 1.0
        return f

    @staticmethod
    def _value(name: str, feat: dict, prev: dict, d: int):
        src = prev if name.startswith("prev:") else feat
        key = name[5:] if name.startswith("prev:") else name
        if key.startswith("d_"):
            v = src.get(key[2:])
            return None if v is None else v * d
        return src.get(key)

    def _signals(self, st: SymState, t: float, m: int, c: float, feat: dict) -> None:
        for s in self.setups:
            if st.sym not in s.symbols:
                continue
            a, b = s.window_minutes()
            if not (a <= m <= b):
                continue
            for d in (1, -1):
                if st.traded[(s.id, d)] >= s.max_per_day:
                    continue
                if s.max_per_day > 1 and any(p.setup == s.id and p.sym == st.sym and p.d == d for p in self.open):
                    continue
                ok = True
                for name, op, val in s.when:
                    v = self._value(name, feat, st.prev_feat, d)
                    if v is None or not OPS[op](v, val):
                        ok = False
                        break
                if not ok:
                    continue
                st.traded[(s.id, d)] += 1
                side = "CE" if d > 0 else "PE"
                strike = self.opt.atm(st.sym, st.day, t, c, side)
                sig = {"setup": s.id, "sym": st.sym, "day": st.day, "d": d, "t": t, "m": m, "spot": c,
                       **{k: feat[k] for k in ("day", "rel", "burst", "twap_rel")}}
                self.signals.append(sig)
                if s.exit.type == "rr":
                    sig["stop_level"] = feat.get(s.exit.stop_feature)
                if strike is None:
                    sig["skipped"] = "no option prices"
                    continue
                if s.exit.type == "rr" and (sig["stop_level"] is None or (c - sig["stop_level"]) * d <= 0):
                    sig["skipped"] = "no valid stop level"
                    continue
                p = Position(setup=s.id, sym=st.sym, day=st.day, d=d, strike=strike, side=side, signal_t=t, state=sig)
                self.open.append(p)
                self._emit("on_signal", p)                       # live alerts go out at the signal, before the fill

    # ---------------------------------------------------------------- positions
    def _setup(self, sid: str) -> Setup:
        return next(s for s in self.setups if s.id == sid)

    def _manage(self, st: SymState, t: float, m: int, h: float, l: float, c: float) -> None:
        sp = self.spread_pct.get(st.sym, 0.5) / 100
        for p in [p for p in self.open if p.sym == st.sym]:
            ob = self.opt.bar(p.sym, p.day, p.strike, p.side, t)
            ex = self._setup(p.setup).exit
            if p.entry is None:                                  # fill at this minute's option open
                if ob is None:
                    continue
                p.entry_t, p.entry = t, ob[0] * (1 + sp / 2) + self.tick
                spot = p.state["spot"]                           # the stop is set from the signal bar, not this one
                p.und_entry = spot
                atr = st.atr or (st.hi - st.lo) / 3
                p.stop = spot - p.d * ex.atr_init * atr
                if ex.type == "rr":                              # fixed stop level and an R-multiple target
                    p.stop = p.state["stop_level"]
                    p.state["target_level"] = spot + p.d * ex.target_r * abs(spot - p.stop)
                p.best = spot
                p.peak = p.trough = p.entry
                self._emit("on_fill", p)
            if ob is None:
                continue
            p.trough = min(p.trough, ob[2])
            atr = st.atr or (st.hi - st.lo) / 3
            reason, px = self._exit_reason(p, ex, t, m, h, l, ob)
            if reason:
                p.exit_t, p.exit = t, max(px * (1 - sp / 2) - self.tick, 0.0)
                p.reason, p.und_exit = reason, c
                self.open.remove(p)
                self.closed.append(p)
                self._emit("on_close", p)
                continue
            p.peak = max(p.peak, ob[1])
            p.best = max(p.best, h) if p.d > 0 else min(p.best, l)
            if ex.type == "rr":                                  # fixed levels: no trail
                continue
            trail = p.best - p.d * ex.atr_trail * atr
            p.stop = max(p.stop, trail) if p.d > 0 else min(p.stop, trail)

    @staticmethod
    def _exit_reason(p: Position, ex, t: float, m: int, h: float, l: float, ob) -> tuple[str | None, float]:
        """First exit that triggers in this minute, and the option price it fills at (before the spread). Level
        exits (premium stop / trail) fill at their level; the others at the minute's close."""
        o_, oh, ol, oc = ob
        if ex.type in ("chandelier", "rr") and ((l <= p.stop) if p.d > 0 else (h >= p.stop)):
            return "stop", oc
        if ex.type == "rr" and ((h >= p.state["target_level"]) if p.d > 0 else (l <= p.state["target_level"])):
            return "target", oc
        stop_lvl = p.entry * (1 + ex.premium_stop_pct / 100)
        if ol <= stop_lvl:
            return "premium_stop", min(stop_lvl, o_)
        if ex.prem_trail_pct and p.peak >= p.entry * (1 + ex.trail_after_pct / 100):
            lvl = p.peak * (1 - ex.prem_trail_pct / 100)
            if ol <= lvl:
                return "premium_trail", min(lvl, o_)
        age = (t - p.entry_t) / 60
        if ex.no_move_min and age >= ex.no_move_min and max(p.peak, oh) < p.entry * (1 + ex.no_move_gain_pct / 100):
            return "no_move", oc
        if ex.max_minutes and age >= ex.max_minutes:
            return "time", oc
        if m >= hm_to_min(ex.flat_by):
            return "flat_by", oc
        return None, 0.0

    # ---------------------------------------------------------------- results
    def trades(self) -> list[dict]:
        out = []
        for p in self.closed:
            row = {"setup": p.setup, "sym": p.sym, "day": p.day, "d": p.d, "side": p.side, "strike": p.strike,
                   "entry_t": p.entry_t, "exit_t": p.exit_t, "entry": p.entry, "exit": p.exit, "reason": p.reason,
                   **{f"at_{k}": v for k, v in p.state.items() if k in ("day", "rel", "burst", "twap_rel", "m")}}
            if p.entry and p.peak is not None:
                row.update(mfe_pct=round((p.peak / p.entry - 1) * 100, 2), mae_pct=round((p.trough / p.entry - 1) * 100, 2))
            if p.entry and p.exit is not None:
                row["minutes"] = round((p.exit_t - p.entry_t) / 60)
                lot = self.lots.get(p.sym) or self.facts.lot(p.sym, p.day) or 1
                charges = self.facts.option_charges(p.entry, p.exit, lot, p.day)
                row.update(lot=lot, pnl_rs=round((p.exit - p.entry) * lot - charges, 2),
                           pct=round(((p.exit - p.entry) * lot - charges) / (p.entry * lot) * 100, 3))
            out.append(row)
        return out
