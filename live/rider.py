"""Trend Rider v1, live (2.84): at every closed 5-minute bar, the setup's indicators for every index and F&O stock.

The maths and the decision are lab/rider.py (shared with the Lab test); the numbers are rules/buy_setup.json. Here:
which bars, which option, which flow, and what is kept:
  * readings: the latest values per symbol (the chart's side card reads these, whatever the chart shows),
  * series: one row per closed bar per symbol for today (the chart's sub-charts draw these),
  * a trial alert (registry "trend_rider", shadow) when the setup passes: recorded, graded and paper-tested on the
    test bench only. Nothing you see or trade changes while it is on trial.

It runs inside the 5-minute scan (2.81: its own thread on the live market); a symbol's bad data or a bug here is
logged and skipped, never stopping the scan.
"""
from __future__ import annotations

import collections
import datetime as dt
import math

import numpy as np

from lab import rider as R


def _f(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


class Rider:
    KIND = "trend_rider"

    def __init__(self, engine):
        self.e = engine
        self.read: dict = {}                 # sym -> latest readings + decision
        self.series: dict = {}               # sym -> [row per closed bar] for today
        self.fired: dict = {}                # (sym, day) -> [ts of signals]
        self.flow_snap: dict = {}            # key -> deque of (cvd, vol) at each bar
        self.day = None
        self.ms = 0.0                        # time of the last pass (for the System page)

    # ------------------------------------------------------------------ inputs
    def _closed(self, sym, day, slot):
        b = self.e.charts.five_min(sym, day)
        if not b or len(b["c"]) < 3:
            return None
        return {k: v[:slot] for k, v in b.items()} if len(b["c"]) > slot else b

    def _opt(self, sym, direction, today):
        try:
            s = self.e._suggest(sym, direction)
        except Exception:
            s = None
        if not s:
            return None
        i = self.e.state.get(s["key"]) if self.e.state is not None else None
        try:
            exp = dt.date.fromisoformat(s["expiry"])
            dte = float(np.busday_count(today, exp)) + 1.0
        except Exception:
            dte = None
        return {"key": s["key"], "label": s.get("label"), "ltp": s.get("ltp"), "ask": s.get("ask"),
                "bid": getattr(i, "bid", None) if i else None, "delta": s.get("delta"),
                "theta": getattr(i, "theta", None) if i else None, "lot": s.get("lot"), "dte": dte, "iv": s.get("iv")}

    def _flow(self, sym):
        """-1..+1 (+ = buyers): half the bid/ask quantity imbalance, half the order-flow (CVD) change over the last
        3 bars against the volume traded in them, on the futures when there are futures, else on the symbol."""
        u = self.e.uni.underlyings.get(sym) if self.e.uni else None
        if not u:
            return None
        key = u.fut_key or u.key
        i = self.e.state.get(key) if key and self.e.state is not None else None
        if not i:
            return None
        parts = []
        tb, ts = _f(i.tbq), _f(i.tsq)
        if tb is not None and ts is not None and tb + ts > 0:
            parts.append((tb - ts) / (tb + ts))
        q = self.flow_snap.setdefault(key, collections.deque(maxlen=8))
        q.append((_f(i.cvd) or 0.0, _f(i.vol) or 0.0))
        if len(q) >= 4:
            dc, dv = q[-1][0] - q[-4][0], q[-1][1] - q[-4][1]
            if dv > 0:
                parts.append(max(-1.0, min(1.0, dc / dv)))
        return round(float(np.mean(parts)), 3) if parts else None

    def _thrust(self, sym, opt, d):
        """The option's own move over the last ~15 minutes against what delta says it should have moved: above 1 =
        the option is outrunning the underlying (buyers paying up), under 1 = it lags (IV falling, decay)."""
        if not opt or self.e.state is None:
            return None
        u = self.e.uni.underlyings.get(sym) if self.e.uni else None
        oi = self.e.state.get(opt["key"])
        ui = self.e.state.get(u.key) if u and u.key else None
        if not oi or not ui or len(oi.hist) < 16 or len(ui.hist) < 16:
            return None
        o0, o1 = _f(oi.hist[-16][1]), _f(oi.hist[-1][1])
        s0, s1 = _f(ui.hist[-16][1]), _f(ui.hist[-1][1])
        delta = abs(_f(opt.get("delta")) or 0.5)
        if not all((o0, o1, s0, s1)) or o0 <= 0:
            return None
        expect = delta * d * (s1 - s0) / o0
        if expect <= 0.002:
            return None
        return round((o1 / o0 - 1) / expect, 3)

    def _ctx(self, sym, m):
        ctx = self.e.charts.context(sym, m)
        d = (self.e.daily or {}).get(sym) or {}
        for k in ("datr", "adr"):
            v = d.get(k) or (d.get("prev") or {}).get(k)
            if v:
                ctx[k] = v
        ctx["event_day"] = bool(m.get("event") or m.get("results"))
        ctx["ban"] = bool(m.get("ban"))
        return ctx

    def _capital(self):
        p = getattr(self.e, "paper", None)
        try:
            return float(p.cfg.get("capital") or 0) or None
        except Exception:
            return None

    # ------------------------------------------------------------------ every closed bar
    def on_bar(self, now, slot, emit) -> int:
        c_ = R.cfg()
        if not c_.get("on", True):
            return 0
        import time as _t
        t_start = _t.perf_counter()
        day = now.date()
        if self.day != day:
            self.day, self.series, self.read, self.fired, self.flow_snap = day, {}, {}, {}, {}
        tod = float(slot * 5)
        try:
            nifty = self._closed("NIFTY", day, slot)
        except Exception:
            nifty = None
        cap = self._capital()
        n = 0
        for sym, m in list(self.e.metrics.items()):
            try:
                n += self._one(sym, m, now, day, slot, tod, nifty, cap, c_, emit)
            except Exception as ex:
                if self.e.charts._rate(f"rider_err:{sym}", 900):
                    self.e.log(f"Trend Rider {sym} skipped: {ex}")
        self.ms = round((_t.perf_counter() - t_start) * 1000)
        return n

    def _one(self, sym, m, now, day, slot, tod, nifty, cap, c_, emit) -> int:
        b = self._closed(sym, day, slot)
        if not b or len(b["c"]) < 3:
            return 0
        ctx = self._ctx(sym, m)
        up, dn = self._opt(sym, "up", day), self._opt(sym, "down", day)
        flow = self._flow(sym)
        thrust = {1: self._thrust(sym, up, 1), -1: self._thrust(sym, dn, -1)}
        r = R.readings(b, ctx, c_, nifty if sym != "NIFTY" else None, up, dn, cap, flow, thrust)
        bo = r.get("breakout")
        dec = R.decide(b, r, tod, c_, (up if bo and bo["dir"] > 0 else dn) if bo else None)
        self.read[sym] = {"t": float(b["t"][-1]), "tod": tod, "readings": r, "decision": dec,
                          "options": {"up": (up or {}).get("label"), "down": (dn or {}).get("label")}}
        row = {"t": float(b["t"][-1]), "tds": r["trend_day"].get("score"), "vwap_dist": r["vwap"].get("dist_atr"),
               "bq": bo["quality"] if bo else None, "bo_dir": bo["dir"] if bo else 0,
               "chase_up": r["up"]["chase"], "chase_dn": r["down"]["chase"],
               "ag_up": r["up"]["agree"]["families"], "ag_dn": r["down"]["agree"]["families"],
               "mvd_up": r["up"]["mvd"], "mvd_dn": r["down"]["mvd"],
               "trad_up": r["up"]["trad"].get("score"), "trad_dn": r["down"]["trad"].get("score"),
               "flow": flow, "rs": r.get("rs"), "go": dec["dir"] if dec.get("go") else 0}
        ser = self.series.setdefault(sym, [])
        if ser and ser[-1]["t"] == row["t"]:
            ser[-1] = row
        else:
            ser.append(row)
        if not dec.get("go"):
            return 0
        ts = now.timestamp()
        shots = self.fired.setdefault((sym, day), [])
        if len(shots) >= int(c_.get("max_signals_per_symbol_day", 2)):
            return 0
        if shots and ts - shots[-1] < float(c_.get("cooldown_min", 30)) * 60:
            return 0
        shots.append(ts)
        d = dec["dir"]
        e_ = _f(m.get("spot")) or float(b["c"][-1])
        stop = dec.get("stop")
        risk = abs(e_ - stop) if stop is not None else None
        side = r["up" if d > 0 else "down"]
        emit({"sym": sym, "kind": self.KIND, "direction": "up" if d > 0 else "down", "severity": 2,
              "title": f"{sym} Trend Rider: BUY {'call' if d > 0 else 'put'} (on trial)",
              "detail": ("Kyun: " + "; ".join(dec["why"]) + "."
                         + (f" Stop {stop:,.2f} (5-min close pe, wick pe nahi)." if stop is not None else "")
                         + " On trial: recorded and graded only."),
              "price": e_,
              "data": {"trial_key": self.KIND,
                       "rider": {"trigger": dec.get("trigger"), "level": dec.get("level"), "stop": stop, "atr": r.get("atr"),
                                 "risk_pct": dec.get("risk_pct"), "tds": r["trend_day"].get("score"),
                                 "bq": bo["quality"] if bo else None, "chase": side["chase"],
                                 "agree": side["agree"]["agree"], "mvd": side["mvd"], "trad": side["trad"].get("score"),
                                 "flow": flow, "rs": r.get("rs"), "thrust": side.get("thrust")},
                       **({"setup_levels": {"entry": round(e_, 2), "stop": stop, "target": round(e_ + d * 2 * risk, 2),
                                            "rr": 2.0}} if risk else {})}})
        return 1

    # ------------------------------------------------------------------ for the screens
    def view(self, sym) -> dict:
        return {"read": self.read.get(sym), "series": list(self.series.get(sym) or []), "config": R.cfg()}
