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

2.43: strategy files (lab/packs.py) can define their own exits from a fixed vocabulary, checked by spec_check(): a stop
(ATR multiple, the signal candle, the other side of the first 5-min candle, or a % of price), an optional target in R,
breakeven, ATR trail, a momentum exit (the first 5-min candle that closes beyond the previous candle against the trade,
or makes a new extreme against it and closes against it) and a time limit. Such an exit gets a key made from its
content ("x" + hash), so the same exit is the same key in every file and every Lab; built-in styles never change.
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


PACK_STYLES: dict = {}        # 2.43: exits defined in strategy files (key -> spec), registered by lab/packs.py
STOP_KINDS = ("atr", "bar", "fc", "pct", "or15", "or30", "swing")    # 2.44: opening ranges and the last swing
MOMENTUM = ("close", "wick")
SPEC_KEYS = {"stop", "target_r", "be_at", "trail_atr", "momentum", "max", "words",
             "target_pts", "target_pct", "opt_target_pts", "opt_target_pct", "sell",             # 2.44
             "exit_if", "opt_stop_pct"}                                                         # 2.47
NEW_244 = ("target_pts", "target_pct", "opt_target_pts", "opt_target_pct", "sell")
NEW_247 = ("exit_if", "opt_stop_pct")
LEVEL_STOPS = ("fc", "or15", "or30", "swing")       # stop at the other side of a level pair (low for a buy, high for a sell)


def spec_of(style: str) -> dict:
    """The exit spec of a built-in style or a strategy-file exit (KeyError if neither)."""
    return STYLES[style] if style in STYLES else PACK_STYLES[style]


def spec_words(sp: dict) -> str:
    kind = sp["stop"][0]
    k = sp["stop"][1] if len(sp["stop"]) > 1 else None
    stop = (f"stop {k:g}×ATR" if kind == "atr" else f"stop {k:g}% away" if kind == "pct" else
            "stop at the other side of the first 5-min candle" if kind == "fc" else
            "stop at the other side of the first 15 minutes' range" if kind == "or15" else
            "stop at the other side of the first 30 minutes' range" if kind == "or30" else
            "stop beyond the last swing low (buy) / high (sell)" if kind == "swing" else
            "stop just beyond the signal candle (0.5-2 ATR)")
    parts = [("SELL the opposite at-the-money option (the view: same direction); " if sp.get("sell") else "") + stop]
    if sp.get("be_at"):
        parts.append(f"at +{sp['be_at']:g}R stop to entry")
    if sp.get("trail_atr"):
        parts.append(f"trail best price − {sp['trail_atr']:g}×ATR")
    tg = [f"{sp['target_r']:g}R"] if sp.get("target_r") else []
    if sp.get("target_pts"):
        tg.append(f"{sp['target_pts']:g} index points")
    if sp.get("target_pct"):
        tg.append(f"{sp['target_pct']:g}% on the index")
    if sp.get("opt_target_pts"):
        tg.append(f"{sp['opt_target_pts']:g} points on the option")
    if sp.get("opt_target_pct"):
        tg.append(f"{sp['opt_target_pct']:g}% on the option")
    parts.append("target " + " or ".join(tg) if tg else "no fixed target")
    if sp.get("opt_stop_pct"):
        parts.append(f"option stop {sp['opt_stop_pct']:g}% against")
    if sp.get("exit_if"):
        parts.append("out when " + " or ".join(_cond_words(c) for c in sp["exit_if"]) + " (at a 5-min close)")
    if sp.get("momentum") == "close":
        parts.append("out when a 5-min candle closes beyond the previous candle against the trade")
    elif sp.get("momentum") == "wick":
        parts.append("out when a 5-min candle makes a new extreme against the trade and closes against it")
    mx = int(sp.get("max") or 375)
    parts.append("out by 15:15" if mx >= 360 else f"max {mx} min")
    return ", ".join(parts) + (" (safety stop: option +40% against)" if sp.get("sell") else " (premium stop −40%)")


def _cond_words(c) -> str:
    try:
        from .factory import FEATURES
        name = FEATURES.get(c[0], (c[0], ""))[0]
    except Exception:
        name = c[0]
    return f"{name} {'above' if c[1] == '>' else 'below'} {c[2]:g}"


def _check_exit_if(v) -> list:
    """2.47: 1-3 conditions [fact, '>' or '<', number] on the facts of each 5-min close after entry (any one exits)."""
    from .factory import FEATURES
    if not isinstance(v, list) or not 1 <= len(v) <= 3:
        raise ValueError("exit_if must be a list of 1-3 conditions")
    out = []
    for c in v:
        if not (isinstance(c, list) and len(c) == 3 and c[1] in (">", "<")):
            raise ValueError("each exit_if condition is [fact, '>' or '<', number]")
        if c[0] not in FEATURES:
            raise ValueError(f"exit_if fact unknown: {str(c[0])[:40]}")
        x = float(c[2])
        if not math.isfinite(x):
            raise ValueError("exit_if value must be a finite number")
        out.append([str(c[0]), c[1], round(x, 6)])
    return out


def spec_check(raw) -> tuple[dict | None, str | None]:
    """Clean an exit from a strategy file: (spec, None) or (None, why it can't be used). Data only, fixed vocabulary."""
    if not isinstance(raw, dict):
        return None, "an exit must be an object"
    extra = set(raw) - SPEC_KEYS
    if extra:
        return None, f"unknown exit fields: {', '.join(sorted(map(str, extra)))[:80]}"
    stop = raw.get("stop")
    if not isinstance(stop, list) or not stop or stop[0] not in STOP_KINDS:
        return None, f"stop must be one of {', '.join(STOP_KINDS)}"
    kind = stop[0]
    try:
        if kind in ("atr", "pct"):
            k = float(stop[1])
            if not (0.05 <= k <= (5 if kind == "atr" else 3)):
                return None, f"{kind} stop size out of range"
            stop = [kind, round(k, 4)]
        else:
            stop = [kind]
        def num(key, lo, hi):
            v = raw.get(key)
            if v in (None, 0, False):
                return None
            v = float(v)
            if not (lo <= v <= hi):
                raise ValueError(f"{key} out of range ({lo}-{hi})")
            return round(v, 4)
        sp = {"stop": stop, "target_r": num("target_r", 0.25, 20), "be_at": num("be_at", 0.25, 10),
              "trail_atr": num("trail_atr", 0.25, 10), "momentum": raw.get("momentum") or None,
              "max": int(min(375, max(5, int(raw.get("max") or 375))))}
        for key, lo, hi in (("target_pts", 1, 5000), ("target_pct", 0.02, 10), ("opt_target_pts", 0.5, 2000),
                            ("opt_target_pct", 1, 500)):
            v = num(key, lo, hi)
            if v is not None:
                sp[key] = v
        if raw.get("sell") not in (None, False, True):
            raise ValueError("sell must be true or false")
        if raw.get("sell"):
            sp["sell"] = True
        v = num("opt_stop_pct", 2, 95)                          # 2.47
        if v is not None:
            sp["opt_stop_pct"] = v
        if raw.get("exit_if"):
            sp["exit_if"] = _check_exit_if(raw["exit_if"])
    except (TypeError, ValueError, IndexError) as e:
        return None, str(e)[:120]
    if sp["momentum"] is not None and sp["momentum"] not in MOMENTUM:
        return None, f"momentum must be one of {', '.join(MOMENTUM)}"
    if sp["trail_atr"] and kind != "atr":
        return None, "an ATR trail needs an ATR stop"
    sp["words"] = spec_words(sp)
    sp["who"] = "strategy file"
    return sp, None


def spec_key(sp: dict) -> str:
    """Same exit, same key, everywhere (content hash of the spec without its words)."""
    import hashlib
    import json
    core = {k: sp.get(k) for k in ("stop", "target_r", "be_at", "trail_atr", "momentum", "max")}
    core.update({k: sp[k] for k in NEW_244 if sp.get(k)})        # 2.44 fields only when used: 2.43 keys unchanged
    core.update({k: sp[k] for k in NEW_247 if sp.get(k)})        # 2.47 likewise
    return "x" + hashlib.sha1(json.dumps(core, sort_keys=True).encode()).hexdigest()[:7]


def momentum_k(o, h, l, c, i: int, d: int, mode: str | None, m0: int, end: int) -> int | None:
    """Index (in minutes after m0, as in first_exit's arrays) of a momentum exit: the first 5-min bar j > i that closes
    beyond bar j-1 against the trade ("close") or makes a new extreme against it and closes against it ("wick").
    Decided at bar j's close, filled at the next minute (5j + 5)."""
    if not mode:
        return None
    for j in range(i + 1, len(c)):
        mj = 5 * j + 5
        if mj > end:
            return None
        if mode == "close":
            hit = c[j] < l[j - 1] if d > 0 else c[j] > h[j - 1]
        else:
            hit = (l[j] < l[j - 1] and c[j] < o[j]) if d > 0 else (h[j] > h[j - 1] and c[j] > o[j])
        if hit:
            return mj - (m0 + 1)
    return None


def signal_k(facts: dict | None, conds, i: int, m0: int, end: int) -> int | None:
    """2.47: index (in minutes after m0, as in first_exit's arrays) of the first 5-min bar j > i whose close meets any
    exit_if condition; decided at bar j's close, filled at the next minute (5j + 5)."""
    if not conds or not facts:
        return None
    n = len(facts.get("tod", []))
    for j in range(i + 1, n):
        mj = 5 * j + 5
        if mj > end:
            return None
        for f_, op, v in conds:
            x = facts.get(f_)
            if x is None:
                continue
            x = x[j]
            if x == x and ((x > v) if op == ">" else (x < v)):
                return mj - (m0 + 1)
    return None


def risk_pts(style: str, s0: float, d: int, atr: float, bar_lo: float | None = None, bar_hi: float | None = None,
             fc_lo: float | None = None, fc_hi: float | None = None, lv: dict | None = None) -> float:
    """R: distance from entry to the initial stop, in points of the underlying. lv (2.44): {"fc" | "or15" | "or30" |
    "swing": (low, high)} for stops at the other side of a level pair."""
    st = spec_of(style)
    kind, k = st["stop"][0], (st["stop"][1] if len(st["stop"]) > 1 else None)
    if kind in LEVEL_STOPS:                                  # 2.43 / 2.44: a level, no ATR needed
        lo, hi = (lv or {}).get(kind) or ((fc_lo, fc_hi) if kind == "fc" else (None, None))
        lvl = lo if d > 0 else hi
        if lvl is None or not math.isfinite(lvl) or not math.isfinite(s0):
            return math.nan
        r = (s0 - lvl) * d
        return float(r) if r > 0 else math.nan
    if kind == "pct":
        return float(s0 * k / 100) if math.isfinite(s0) and s0 > 0 else math.nan
    if not atr or not math.isfinite(atr) or atr <= 0:
        return math.nan
    if kind == "atr":
        return k * atr
    lvl = (bar_lo - 0.1 * atr) if d > 0 else (bar_hi + 0.1 * atr)
    r = (s0 - lvl) * d if lvl is not None and math.isfinite(lvl) else atr
    return float(min(2 * atr, max(0.5 * atr, r)))


def first_exit(style: str, spot, opt, s0: float, entry: float, d: int, R: float, mom_k: int | None = None,
               sig_k: int | None = None):
    """Backtest: index of the first minute an exit fires in spot/opt (minutes after entry), and why. (None, 'time')
    if nothing fires. Same rule as LivePlan.check, minute by minute. mom_k: where a momentum exit fires (momentum_k)."""
    st = spec_of(style)
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
    sell = bool(st.get("sell"))
    if sell:                                                 # 2.44: a sold option hurts when it rises
        reasons = [("stop", move <= stop), ("premium stop", np.nan_to_num(opt, nan=-1e18) >= entry * (1 + PREM_STOP / 100))]
    else:
        reasons = [("stop", move <= stop), ("premium stop", np.nan_to_num(opt, nan=1e18) <= entry * (1 - PREM_STOP / 100))]
    if st.get("opt_stop_pct"):                              # 2.47: a stop on the option's own price
        x = st["opt_stop_pct"] / 100
        reasons.append(("option stop", (np.nan_to_num(opt, nan=-1e18) >= entry * (1 + x)) if sell
                        else (np.nan_to_num(opt, nan=1e18) <= entry * (1 - x))))
    if st["target_r"]:
        reasons.append(("target", move >= st["target_r"] * R))
    if st.get("target_pts"):
        reasons.append(("target", move >= st["target_pts"]))
    if st.get("target_pct"):
        reasons.append(("target", move >= s0 * st["target_pct"] / 100))
    for key in ("opt_target_pts", "opt_target_pct"):
        if st.get(key):
            gain = st[key] if key == "opt_target_pts" else entry * st[key] / 100
            hit = (np.nan_to_num(opt, nan=1e18) <= entry - gain) if sell else (np.nan_to_num(opt, nan=-1e18) >= entry + gain)
            reasons.append(("target", hit))
    first, why = None, "time"
    for name, hit in reasons:
        hit = hit & ~np.isnan(move) if name not in ("premium stop", "option stop") else hit
        if hit.any():
            k = int(np.argmax(hit))
            if first is None or k < first:
                first, why = k, name
    if mom_k is not None and 0 <= mom_k < len(move) and (first is None or mom_k < first):
        first, why = mom_k, "momentum"
    if sig_k is not None and 0 <= sig_k < len(move) and (first is None or sig_k < first):
        first, why = sig_k, "signal exit"
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
        st = spec_of(self.style)
        stop = -self.R
        if st["be_at"] and self.best >= st["be_at"] * self.R:
            stop = 0.0
        if st["trail_atr"]:
            stop = max(stop, self.best - st["trail_atr"] * self.R / st["stop"][1])
        return stop

    def check(self, spot: float | None, opt: float | None, now_ts: float) -> str | None:
        st = spec_of(self.style)
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
        st = spec_of(self.style)
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
    st = spec_of(style)
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
