"""Early Breakout, 09:20-09:30 (3.08, Stockway Pro v2, on trial): a strong move right after the first 5-minute candle.

Mandeep (25 Nov 2026): "v2 mein ye bhi add karo ki 9:20 par bhi breakout bane or ek strong signal bane toh trade lele
kyu ki kyi baar strong move 9:20 se 9:30 mein hi mil jaate hai 5 min ke candle ke baad."

Trend Rider needs three closed 5-minute bars (its first signal is at 09:30). This looks at 09:20-09:30 only:
  1. breakout: a 1-minute close (tf "1m"), or the 09:25 / 09:30 5-minute close (tf "5m"), beyond the first 5-minute
     candle's high (calls) / low (puts), that is also through a key level (yesterday's high / low / close, pivot,
     R1 / S1, the CPR, last week's high / low, a round number) lying between the first candle's far end and the close;
  2. strong: score = share of the checks that agree, min_score (85) to buy, and every one of "musts" (rules) must agree:
     the first candle strong (body, close near its end, range not tiny), gap and first candle the same way, the right
     side of VWAP, the index with it (or the stock index_lead_x times the index's move), order flow with it, a
     tradable option (spread, premium), no chase;
  3. stop: the middle of the first candle (stop "mid") or its far end ("far"), whichever the rules say, and at most
     max_premium_risk_pct of the premium by the option's delta.
The signal is a trial alert (kind early_breakout, recorded and graded); the Pro v2 · Early paper account buys strong
ones, 1 lot, with the chart exit (live/books.py offer_early). One signal per symbol, side and trigger a day.
Gun: no symbol, date or threshold of its own (rules/pro_v2.json "early"); a bad symbol never stops the others.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np

from .clock import IST

KIND = "early_breakout"


def _f(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _round_levels(spot):
    step = 500 if spot > 20000 else 100 if spot > 5000 else 50 if spot > 1000 else 10 if spot > 200 else 5
    base = int(spot // step) * step
    return [base - step, base, base + step, base + 2 * step]


def levels(prev: dict | None, d_ohlc: list | None, spot: float, today: str) -> list:
    """[(name, price)]: the key levels the breakout must go through."""
    out = []
    pv = prev or {}
    h, l, c = _f(pv.get("h")), _f(pv.get("l")), _f(pv.get("c"))
    if h and l and c:
        p = (h + l + c) / 3
        bc = (h + l) / 2
        tc = 2 * p - bc
        out += [("Yesterday's high", h), ("Yesterday's low", l), ("Yesterday's close", c), ("Pivot", p),
                ("R1", 2 * p - l), ("S1", 2 * p - h), ("CPR top", max(tc, bc)), ("CPR bottom", min(tc, bc))]
    rows = [r for r in (d_ohlc or []) if str(r[0])[:10] < today]
    if len(rows) >= 5:
        out += [("Last week's high", max(float(x[2]) for x in rows[-5:])), ("Last week's low", min(float(x[3]) for x in rows[-5:]))]
    out += [("Round number", float(x)) for x in _round_levels(spot)]
    return out


def judge(rows: np.ndarray, d: int, prev: dict | None, lv: list, c: dict, idx_move: float | None = None,
          flow: float | None = None, opt: dict | None = None, is_index: bool = False, datr: float | None = None,
          tf: str = "1m") -> dict | None:
    """rows: today's closed 1-minute bars [t, o, h, l, c, v] from 09:15. Returns the breakout reading for direction d
    on the last bar, or None when it did not break out through a key level."""
    if rows is None or len(rows) < 6:
        return None
    t, o, h, l, cl, v = (rows[:, i] for i in range(6))
    first = slice(0, 5)                                  # 09:15-09:19: the first 5-minute candle
    o1, h1, l1, c1 = float(o[0]), float(h[first].max()), float(l[first].min()), float(cl[4])
    rng = h1 - l1
    if rng <= 0:
        return None
    brk = h1 if d > 0 else l1
    C = float(cl[-1])
    if tf == "1m":
        prev_c = float(cl[-2])
        if not (d * (C - brk) > 0 and d * (prev_c - brk) <= 0):
            return None                                  # a fresh break on this minute only
    elif d * (C - brk) <= 0:
        return None
    names = set(c.get("level_names") or [])
    far = l1 if d > 0 else h1
    through = [(nm, px) for nm, px in lv if (not names or nm in names) and d * (px - far) > 0 and d * (C - px) >= 0]
    if not through:
        return None
    lvl_name, lvl = max(through, key=lambda x: d * x[1])     # the furthest level crossed
    checks = {}
    body = (c1 - o1) * d / rng
    zone = ((h1 - c1) if d > 0 else (c1 - l1)) / rng
    big = rng >= float(c.get("first_min_range_datr", 0.12)) * datr if datr else True
    checks["first_candle"] = body >= float(c.get("first_min_body", 0.5)) and zone <= float(c.get("first_close_zone", 0.3)) and big
    pc = _f((prev or {}).get("c"))
    gap = (o1 - pc) if pc else 0.0
    if pc and abs(gap) / pc >= 0.001:
        checks["gap_same_way"] = gap * d > 0 and (c1 - o1) * d > 0
    tp = (h + l + cl) / 3
    w = v if v.sum() > 0 else np.ones(len(tp))
    vw = float((tp * w).sum() / w.sum())
    checks["vwap_side"] = d * (C - vw) > 0
    if idx_move is not None and not is_index:
        own = (C / o1 - 1) * 100
        checks["index_with"] = idx_move * d > 0 or own * d >= float(c.get("index_lead_x", 2.0)) * abs(idx_move)
    elif idx_move is not None:
        checks["index_with"] = idx_move * d > 0
    if flow is not None:
        checks["flow_with"] = flow * d > 0
    if opt:
        ask, bid, ltp = _f(opt.get("ask")), _f(opt.get("bid")), _f(opt.get("ltp"))
        sp = (ask - bid) / ((ask + bid) / 2) * 100 if ask and bid and ask > bid > 0 else None
        checks["option_ok"] = (sp is None or sp <= float(c.get("max_spread_pct", 4.0))) and (ltp or 0) >= float(c.get("min_premium", 3.0))
    checks["no_chase"] = d * (C - brk) <= float(c.get("chase_max_first", 0.6)) * rng
    n_ok = sum(1 for x in checks.values() if x)
    score = round(100.0 * n_ok / len(checks), 1) if checks else 0.0
    musts = [k for k in (c.get("musts") or ()) if k in checks]
    missing = [k for k in musts if not checks[k]]
    stop = (l1 + h1) / 2 if str(c.get("stop", "mid")) == "mid" else far
    atr = max(rng, (0.1 * datr) if datr else 0.0)
    return {"dir": d, "tf": tf, "level": round(brk, 2), "through": lvl_name, "through_px": round(lvl, 2),
            "stop": round(stop, 2), "atr": round(atr, 4), "first": [round(o1, 2), round(h1, 2), round(l1, 2), round(c1, 2)],
            "score": score, "label": "strong" if score >= float(c.get("min_score", 85)) and not missing else "weak",
            **({"missing": missing} if missing else {}),
            "checks": {k: bool(x) for k, x in checks.items()}, "vwap": round(vw, 2), "close": round(C, 2)}


class Early:
    def __init__(self, engine):
        self.e = engine
        self.day = None
        self.fired: set = set()            # (sym, dir, tf)
        self.done_min = None               # the last minute looked at

    def cfg(self) -> dict:
        try:
            from . import pro2
            return pro2.cfg().get("early") or {}
        except Exception:
            return {}

    def on_minute(self, now: dt.datetime, emit) -> int:
        """Called by the loop; works once per closed minute between the window's start and end."""
        c = self.cfg()
        if not c.get("on", True):
            return 0
        hm = now.strftime("%H:%M")
        if not (str(c.get("from", "09:20")) <= hm <= str(c.get("to", "09:30"))):
            return 0
        day = now.date()
        if self.day != day:
            self.day, self.fired, self.done_min = day, set(), None
        mk = int(now.timestamp() // 60)
        if self.done_min == mk:
            return 0
        self.done_min = mk
        t0 = dt.datetime.combine(day, dt.time(9, 15), IST).timestamp()
        tfs = ["1m"] + (["5m"] if hm in ("09:25", "09:30") else [])
        idx = {}
        for s in ("NIFTY", "BANKNIFTY"):
            rw = self._rows(s, t0, now)
            if rw is not None and len(rw) >= 1:
                idx[s] = (float(rw[-1, 4]) / float(rw[0, 1]) - 1) * 100
        n = 0
        for sym, m in list(getattr(self.e, "metrics", {}).items()):
            try:
                n += self._one(sym, m, now, day, t0, tfs, idx, c, emit)
            except Exception as ex:
                rate = getattr(getattr(self.e, "charts", None), "_rate", None)
                if rate and rate(f"early_err:{sym}", 900):
                    self.e.log(f"Early breakout {sym} skipped: {ex}")
        return n

    def _rows(self, sym, t0, now):
        rows = self.e.charts.today_bars(sym)
        if not rows:
            return None
        a = np.asarray(rows, dtype=float)
        a = a[(a[:, 0] >= t0) & (a[:, 0] + 60 <= now.timestamp() + 1)]
        return a if len(a) else None

    def _one(self, sym, m, now, day, t0, tfs, idx, c, emit) -> int:
        rows = self._rows(sym, t0, now)
        if rows is None or len(rows) < 6:
            return 0
        dly = (getattr(self.e, "daily", None) or {}).get(sym) or {}
        spot = float(rows[-1, 4])
        lv = levels(dly.get("prev"), dly.get("d_ohlc"), spot, day.isoformat())
        is_index = bool(m.get("is_index"))
        im = idx.get("BANKNIFTY" if sym == "NIFTY" else "NIFTY") if is_index else idx.get("NIFTY")
        datr = _f(dly.get("datr") or (dly.get("prev") or {}).get("datr"))
        n = 0
        for tf in tfs:
            rw = rows
            if tf == "5m":                                   # the last closed 5-minute bar's close
                k = int((now.timestamp() - t0) // 300)
                rw = rows[rows[:, 0] < t0 + k * 300]
                if len(rw) < 10:
                    continue
            for d in (1, -1):
                if (sym, d, tf) in self.fired:
                    continue
                if not judge(rw, d, dly.get("prev"), lv, c, im, None, None, is_index, datr, tf):
                    continue                                 # no breakout through a level: no option / flow lookups
                opt = None
                try:
                    opt = self.e._suggest(sym, "up" if d > 0 else "down")
                except Exception:
                    opt = None
                flow = None
                try:
                    flow = self.e.rider._flow(sym)
                except Exception:
                    flow = None
                j = judge(rw, d, dly.get("prev"), lv, c, im, flow, opt, is_index, datr, tf)
                if not j:
                    continue
                self.fired.add((sym, d, tf))
                if (opt or {}).get("delta") and (opt or {}).get("ltp"):
                    risk = abs(spot - j["stop"]) * abs(float(opt["delta"])) / float(opt["ltp"]) * 100
                    j["risk_pct"] = round(risk, 1)
                    if risk > float(c.get("max_premium_risk_pct", 30.0)):
                        j["label"], j["why_weak"] = "weak", f"stop is {risk:.0f}% of the premium"
                R = abs(spot - j["stop"])
                ok = ", ".join(k.replace("_", " ") for k, x in j["checks"].items() if x)
                no = ", ".join(k.replace("_", " ") for k, x in j["checks"].items() if not x)
                emit({"sym": sym, "kind": KIND, "direction": "up" if d > 0 else "down", "severity": 2,
                      "title": f"{sym} Early breakout ({tf}): BUY {'call' if d > 0 else 'put'} (on trial)",
                      "detail": (f"Kyun: pehli 5-min candle ka {'high' if d > 0 else 'low'} {j['level']:,.2f} toota aur "
                                 f"{j['through']} {j['through_px']:,.2f} ke paar. Score {j['score']:.0f} ({j['label']}): "
                                 f"saath {ok or '-'}; khilaaf {no or '-'}. Stop {j['stop']:,.2f}. On trial."),
                      "price": spot,
                      "data": {"trial_key": f"early_{tf}", "early": j,
                               "setup_levels": {"entry": round(spot, 2), "stop": j["stop"],
                                                "target": round(spot + d * 2 * R, 2), "rr": 2.0}}})
                n += 1
        return n
