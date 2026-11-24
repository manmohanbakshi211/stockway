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

from .store import reading                # 2.99: reads off the writer lock

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
        self._packs = (0.0, {})              # 2.90: strategy id -> (strategy file, name), re-read hourly
        self._mind_s = 0.0                   # 2.90: MindGame time spent in this pass (capped)
        self.warmed = None                   # 3.07: the day the earlier bars were read again after a (re)start
        self.warm_info: dict = {}

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

    # ------------------------------------------------------------------ 2.90: what only the live engine knows
    def _pack_map(self) -> dict:
        import time as _t
        at, mp = self._packs
        if _t.time() - at < 3600 and mp:
            return mp
        mp = {}
        try:
            for v in self.e.charts._pack_rules():
                mp[v["_variant"]] = (str(v.get("_pack") or ""), str(v.get("_strategy") or v.get("_name") or ""))
        except Exception:
            pass
        self._packs = (_t.time(), mp)
        return mp

    @staticmethod
    def _vote(w: int, a: int):
        if not w and not a:
            return None
        return 1 if w > a else -1 if a > w else 0

    def ext(self, sym, now, cand: dict | None = None) -> dict:
        """Votes from outside Trend Rider for both directions, for its confidence: Ghanshyam's points and the other
        named strategies (library, book, strategy files, fusions) that fired on this symbol in the last 15 minutes
        (the engine's confluence record, filled by the same scan just before), the candle forecast (the forming 5-min
        candle's forecast, else the next-candle chance), and MindGame's buyer / seller lean for the directions in
        cand = {d: True} (about 80 ms a symbol, so only where the setup passes; time-capped per pass)."""
        out = {1: {}, -1: {}}
        ts = now.timestamp()
        win = float(getattr(self.e, "CONF_WINDOW_S", 900))
        q = [x for x in ((self.e.__dict__.get("_conf") or {}).get(sym) or []) if ts - win <= x[0] <= ts + 1]
        mp = self._pack_map() if q else {}
        g = {1: set(), -1: set()}
        o = {1: set(), -1: set()}
        names = {}
        for _t, dd, who in q:
            pk, nm = mp.get(who, ("", ""))
            (g if pk.startswith("ghanshyam") else o)[1 if dd > 0 else -1].add(who)
            names[who] = nm
        for d in (1, -1):
            gw, ga = len(g[d]), len(g[-d])
            ow, oa = len(o[d]), len(o[-d])
            out[d]["ghanshyam"] = self._vote(gw, ga)
            if gw or ga:
                out[d]["ghanshyam_why"] = (f"{gw} with" + (f" ({', '.join(sorted({names[x] for x in g[d] if names.get(x)})[:3])})" if gw else "")
                                           + f", {ga} against")
            out[d]["strategies"] = self._vote(ow, oa)
            if ow or oa:
                out[d]["strategies_why"] = f"{ow} with, {oa} against (last 15 min)"
            try:
                ff = self.e.forecast_facts(sym, "up" if d > 0 else "down", now) if hasattr(self.e, "forecast_facts") else {}
                fc = next((x for x in (_f(ff.get(k)) for k in ("fc5_al", "nc5_al", "fc15_al", "nc15_al")) if x is not None), None)
                if fc is not None:
                    out[d]["forecast"] = 1 if fc > 0.2 else -1 if fc < -0.2 else 0
            except Exception:
                pass
        cand = cand or {}
        mind = getattr(self.e, "mind", None)
        if mind is not None and any(cand.values()) and self._mind_s < 1.5:
            import time as _t
            t1 = _t.perf_counter()
            try:
                from .mindgame import combine
                reads, _ = mind.small(sym, ("5m",), ts)
                bias = (combine(reads) or {}).get("bias")
                if bias is not None:
                    for d in (1, -1):
                        if cand.get(d):
                            out[d]["mindgame"] = 1 if bias * d >= 15 else -1 if bias * d <= -15 else 0
                            out[d]["mindgame_why"] = f"lean {bias:+d}"
            except Exception:
                pass
            self._mind_s += _t.perf_counter() - t1
        return out

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
        if self.warmed != day:                             # 3.07: a late start or a restart: read the day's earlier bars
            if not self._warm(now, day, slot, c_):
                self.ms = round((_t.perf_counter() - t_start) * 1000)
                return 0                                   # today's earlier candles still filling: no signal on half a day
        tod = float(slot * 5)
        try:
            nifty = self._closed("NIFTY", day, slot)
        except Exception:
            nifty = None
        cap = self._capital()
        self._mind_s = 0.0
        n = 0
        for sym, m in list(self.e.metrics.items()):
            try:
                n += self._one(sym, m, now, day, slot, tod, nifty, cap, c_, emit)
            except Exception as ex:
                if self.e.charts._rate(f"rider_err:{sym}", 900):
                    self.e.log(f"Trend Rider {sym} skipped: {ex}")
        self.ms = round((_t.perf_counter() - t_start) * 1000)
        return n

    # ------------------------------------------------------------------ 3.07: warm-up after a late start or a restart
    def _complete(self, day, slot) -> bool:
        """Today's 5-minute bars of Nifty are there up to this bar (the gap fill has brought the earlier ones back)."""
        try:
            b = self._closed("NIFTY", day, slot)
        except Exception:
            return True                                    # can't tell (bad data): never wait on an error
        return bool(b) and len(b["c"]) >= slot - 1

    def _warm(self, now, day, slot, c_) -> bool:
        """3.07 (Mandeep, 24 Nov: Pro saw nothing from 09:37 to 10:10 on 23 Nov after two restarts; in 6 replayed days
        74% of Pro's signals came between 09:15 and 10:15). The first bar after a (re)start in the day:
          * waits (no signal) until the day's earlier candles are back (live/gapfill.py: today's recording, then
            Upstox's intraday candles), at most warm_wait_bars bars;
          * reads the last warm_slots closed bars again for every symbol: the series the EMA videos and the sub-charts
            use are back (no signal, no trade, no option looked at: those are now, not then);
          * takes back today's Trend Rider signals from the record, so the two-a-day limit and the cool-down still hold
            and a restart never buys the same setup twice.
        True = go on with this bar."""
        first = int(c_.get("first_slot", 3))
        if slot <= first + 1 or not getattr(self.e, "metrics", None):
            self.warmed = day
            return True
        tries = self.warm_info.get("tries", 0) if self.warm_info.get("day") == day else 0
        self.warm_info = {"day": day, "tries": tries + 1}
        if not self._complete(day, slot) and tries < int(c_.get("warm_wait_bars", 3)):
            if self.e.charts._rate("rider_warm_wait", 600):
                self.e.log("Trend Rider: waiting for today's earlier candles before judging (after a late start or a restart)")
            return False
        import time as _t
        t0 = _t.perf_counter()
        n_rows = 0
        lo = max(first, slot - int(c_.get("warm_slots", 12)))
        cap = self._capital()
        for s in range(lo, slot):
            try:
                nifty = self._closed("NIFTY", day, s)
            except Exception:
                nifty = None
            for sym, m in list(self.e.metrics.items()):
                try:
                    b = self._closed(sym, day, s)
                    if not b or len(b["c"]) < 3:
                        continue
                    t_last = float(b["t"][-1])
                    hist = [x.get("ema_now") or [] for x in (self.series.get(sym) or []) if x["t"] < t_last]
                    r = R.readings(b, self._ctx(sym, m), c_, nifty if sym != "NIFTY" else None, None, None, cap, None,
                                   {1: None, -1: None}, hist)
                    self._keep_row(sym, b, r, None, 0)
                    n_rows += 1
                except Exception:
                    continue
        fired = self._fired_today(day)
        for (sym, d_), ts in fired.items():
            self.fired[(sym, d_)] = sorted(set(self.fired.get((sym, d_), [])) | set(ts))
        self.warmed = day
        self.warm_info.update(rows=n_rows, bars=slot - lo, signals_back=sum(len(v) for v in fired.values()),
                              ms=round((_t.perf_counter() - t0) * 1000))
        if n_rows:
            self.e.log(f"Trend Rider warmed up: {slot - lo} earlier 5-minute bars read again ({n_rows} rows), "
                       f"{self.warm_info['signals_back']} signal(s) of today taken back")
        return True

    def _fired_today(self, day) -> dict:
        """{(sym, day): [ts]} of today's Trend Rider signals already recorded (empty without a store)."""
        st = getattr(self.e, "store", None)
        if st is None:
            return {}
        src = getattr(self.e, "source", None)
        out: dict = {}
        try:
            with reading(st) as db:
                rows = db.execute("SELECT sym, ts FROM trial_alerts WHERE kind=? AND day=? AND source=?",
                                  (self.KIND, day.isoformat(), src)).fetchall()
            for sym, ts in rows:
                out.setdefault((sym, day), []).append(float(ts))
        except Exception:
            return {}
        return out

    def _keep_row(self, sym, b, r, flow, go) -> dict:
        """One row of today's series for a closed bar (the chart's sub-charts and the EMA videos read these)."""
        ev = r.get("ema") or {}
        a_ = r.get("atr") or 0
        c_last = float(b["c"][-1])
        bo = r.get("breakout")
        row = {"t": float(b["t"][-1]), "tds": r["trend_day"].get("score"), "vwap_dist": r["vwap"].get("dist_atr"),
               "bq": bo["quality"] if bo else None, "bo_dir": bo["dir"] if bo else 0,
               "chase_up": r["up"]["chase"], "chase_dn": r["down"]["chase"],
               "ag_up": r["up"]["agree"]["families"], "ag_dn": r["down"]["agree"]["families"],
               "mvd_up": r["up"]["mvd"], "mvd_dn": r["down"]["mvd"],
               "trad_up": r["up"]["trad"].get("score"), "trad_dn": r["down"]["trad"].get("score"),
               "flow": flow, "rs": r.get("rs"), "go": go,
               # 2.89: the EMA videos (5 EMA alert, 10 EMA pullback), on trial
               "e5_dist": round((c_last - ev["ema_fast"]) / a_, 3) if ev.get("ema_fast") is not None and a_ else None,
               "e10_dist": round((c_last - ev["ema_slow"]) / a_, 3) if ev.get("ema_slow") is not None and a_ else None,
               "ema_now": list(ev.get("now") or []),
               "ema_fired": [[dd, x["alert"]] for dd in (1, -1) for x in ((ev.get("fired") or {}).get(dd) or [])],
               "ema_up": bool((ev.get("vote") or {}).get(1)), "ema_dn": bool((ev.get("vote") or {}).get(-1)),
               "conf_up": ((r.get("conf") or {}).get(1) or {}).get("score"),
               "conf_dn": ((r.get("conf") or {}).get(-1) or {}).get("score")}
        ser = self.series.setdefault(sym, [])
        if ser and ser[-1]["t"] == row["t"]:
            ser[-1] = row
        elif not ser or ser[-1]["t"] < row["t"]:
            ser.append(row)
        else:                                              # an earlier bar read again (warm-up): in its place
            ser[:] = sorted([x for x in ser if x["t"] != row["t"]] + [row], key=lambda x: x["t"])
        return row

    def _one(self, sym, m, now, day, slot, tod, nifty, cap, c_, emit) -> int:
        b = self._closed(sym, day, slot)
        if not b or len(b["c"]) < 3:
            return 0
        ctx = self._ctx(sym, m)
        up, dn = self._opt(sym, "up", day), self._opt(sym, "down", day)
        flow = self._flow(sym)
        thrust = {1: self._thrust(sym, up, 1), -1: self._thrust(sym, dn, -1)}
        t_last = float(b["t"][-1])
        hist = [x.get("ema_now") or [] for x in (self.series.get(sym) or []) if x["t"] < t_last]
        r = R.readings(b, ctx, c_, nifty if sym != "NIFTY" else None, up, dn, cap, flow, thrust, hist)
        bo = r.get("breakout")
        cc = c_.get("confidence") or {}
        ex = None
        if cc.get("on", True):                             # 2.90: every factor at once, both directions
            ex = self.ext(sym, now)
            r["conf"] = {d: R.confidence(r, d, c_, ex[d]) for d in (1, -1)}
        c_pre = {**c_, "confidence": {**cc, "use_in_decision": False}} if cc.get("use_in_decision") else c_
        dec = R.decide(b, r, tod, c_pre, (up if bo and bo["dir"] > 0 else dn) if bo else None, opts={1: up, -1: dn})
        if ex is not None and dec.get("go"):               # the setup passes: add MindGame's lean, then score again
            d0 = dec["dir"]
            ex[d0].update(self.ext(sym, now, {d0: True})[d0])
            r["conf"][d0] = R.confidence(r, d0, c_, ex[d0])
            if cc.get("use_in_decision"):                  # only when switched on: the confidence gate decides
                dec = R.decide(b, r, tod, c_, (up if bo and bo["dir"] > 0 else dn) if bo else None, opts={1: up, -1: dn})
        ev = r.get("ema") or {}
        self.read[sym] = {"t": float(b["t"][-1]), "tod": tod, "readings": r, "decision": dec,
                          "options": {"up": (up or {}).get("label"), "down": (dn or {}).get("label")}}
        self._keep_row(sym, b, r, flow, dec["dir"] if dec.get("go") else 0)
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
        cf = (r.get("conf") or {}).get(d) or {}
        try:                                               # 3.01: Pro's reading (live/pro.py), kept on the signal
            from . import pro as _pro
            prj = _pro.judge(self.e, sym, d, now, {k: cf.get(k) for k in ("score", "label", "with", "against", "no_view")})
        except Exception:
            prj = {}
        xv = {}
        try:                                               # 3.08: the Vote Lab (live/provotes.py): recorded, weight 0
            from . import provotes as _pv
            dly = (getattr(self.e, "daily", None) or {}).get(sym) or {}
            th = (getattr(self.e.charts, "__dict__", {}).get("_tfh") or {}).get((sym, day))
            xv = _pv.compute(b, d, dec.get("level"), r.get("atr"), dly.get("prev"),
                             (th[1][-2:] if th and th[1] else None))          # earlier days only when already held
            if prj:
                prj.setdefault("votes", {}).update(_pv.as_pro_votes(xv))
        except Exception:
            xv = {}
        W = R.FACTOR_WORDS
        conf_txt = (f" Confidence {cf['score']:.0f}/100 ({cf['label']}): saath {', '.join(W.get(k, k) for k in cf['with']) or '-'}"
                    f"; khilaaf {', '.join(W.get(k, k) for k in cf['against']) or '-'}." if cf.get("score") is not None else "")
        emit({"sym": sym, "kind": self.KIND, "direction": "up" if d > 0 else "down", "severity": 2,
              "title": f"{sym} Trend Rider: BUY {'call' if d > 0 else 'put'} (on trial)",
              "detail": ("Kyun: " + "; ".join(dec["why"]) + "."
                         + (f" Stop {stop:,.2f} (5-min close pe, wick pe nahi)." if stop is not None else "")
                         + conf_txt
                         + " On trial: recorded and graded only."),
              "price": e_,
              "data": {"trial_key": self.KIND,
                       "rider": {"trigger": dec.get("trigger"), "level": dec.get("level"), "stop": stop, "atr": r.get("atr"),
                                 "risk_pct": dec.get("risk_pct"), "tds": r["trend_day"].get("score"),
                                 "bq": bo["quality"] if bo else None, "chase": side["chase"],
                                 "agree": side["agree"]["agree"], "mvd": side["mvd"], "trad": side["trad"].get("score"),
                                 "flow": flow, "rs": r.get("rs"), "thrust": side.get("thrust"),
                                 "ema_vote": bool((ev.get("vote") or {}).get(d)),
                                 "ema_fired": [x["alert"] for x in ((ev.get("fired") or {}).get(d) or [])],
                                 "ema10": ev.get("ema_slow"),
                                 "confidence": {k: cf.get(k) for k in ("score", "label", "with", "against", "no_view", "why")}},
                       **({"pro": prj} if prj else {}),
                       **({"votes2": xv} if xv else {}),
                       **({"setup_levels": {"entry": round(e_, 2), "stop": stop, "target": round(e_ + d * 2 * risk, 2),
                                            "rr": 2.0}} if risk else {})}})
        return 1

    # ------------------------------------------------------------------ 2.90: is a high confidence worth more?
    @staticmethod
    def by_confidence(store, source: str, days: int = 60) -> dict:
        """The graded Trend Rider trial alerts split by the confidence they carried (weak / ok / strong at the rules'
        min and strong): how many, how many reached the target, average R at the exit. The record that decides
        whether confidence may ever gate entries (rules/buy_setup.json confidence.use_in_decision)."""
        import json as _json
        c = R.cfg().get("confidence") or {}
        lo, hi = float(c.get("min", 60)), float(c.get("strong", 70))
        cut = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        with reading(store) as db_:
            rows = db_.execute("SELECT data, result, r_exit FROM trial_alerts WHERE source=? AND kind='trend_rider' "
                                    "AND day >= ? AND result IS NOT NULL AND result != 'ungraded'", (source, cut)).fetchall()
        out = {k: {"n": 0, "hit": 0, "r": 0.0} for k in ("weak", "ok", "strong", "none")}
        for data, res, rx in rows:
            try:
                sc = (((_json.loads(data or "{}").get("rider") or {}).get("confidence")) or {}).get("score")
            except Exception:
                sc = None
            k = "none" if sc is None else "strong" if sc >= hi else "ok" if sc >= lo else "weak"
            out[k]["n"] += 1
            out[k]["hit"] += int(res == "hit")
            out[k]["r"] += float(rx or 0)
        for v in out.values():
            v["hit_share"] = round(v["hit"] / v["n"], 3) if v["n"] else None
            v["avg_r"] = round(v["r"] / v["n"], 3) if v["n"] else None
            v.pop("r")
        return {"min": lo, "strong": hi, "days": days, "buckets": out}

    # ------------------------------------------------------------------ for the screens
    def view(self, sym) -> dict:
        return {"read": self.read.get(sym), "series": list(self.series.get(sym) or []), "config": R.cfg()}
