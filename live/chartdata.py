"""Chart data (candles, indicators, levels, markers) and the live setup scanner."""
from __future__ import annotations

import datetime as dt
import math
import time
import urllib.parse

import numpy as np

from . import setups, ta
from .clock import IST

IST_SHIFT = 19800          # the chart library shows UTC; shift so the axis reads IST
SHORT = {"ema_pullback": "EMA", "vwap_reclaim": "VWAP", "cpr_breakout": "CPR", "pdh_break": "PDH/L", "wall_fade": "Wall"}


def _num(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


class ChartData:
    def __init__(self, engine):
        self.e = engine
        self.hist = {}            # (sym, date) -> [[t, o, h, l, c, v], ...] previous days
        self.today_fill = {}      # sym -> earlier minutes of today fetched from the broker
        self.last_slot = None
        self.active = {}          # sym -> list of live setup signals

    # ------------------------------------------------------------ bars
    def _session_start(self, day):
        return dt.datetime.combine(day, dt.time(9, 15), IST).timestamp()

    def today_bars(self, sym):
        b = self.e.state.bars.get(sym) if self.e.state else None
        rows = [list(x) for x in b.bars] if b else []
        extra = self.today_fill.get(sym) or []
        if extra:
            have = {r[0] for r in rows}
            rows = sorted([r for r in extra if r[0] not in have] + rows, key=lambda r: r[0])
        return rows

    def history(self, sym):
        """Previous sessions' 1-minute bars (live mode only; the simulator has just today)."""
        if self.e.source != "upstox" or not self.e.rest or not self.e.uni:
            return []
        day = self.e.clock.now().date()
        k = (sym, day)
        if k in self.hist:
            return self.hist[k]
        u = self.e.uni.underlyings.get(sym)
        rows = []
        try:
            key = urllib.parse.quote(u.key, safe="")
            to, frm = (day - dt.timedelta(days=1)).isoformat(), (day - dt.timedelta(days=6)).isoformat()
            data = self.e.rest.get(f"/v3/historical-candle/{key}/minutes/1/{to}/{frm}") or {}
            for c in data.get("candles", []):
                t = dt.datetime.fromisoformat(c[0]).timestamp()
                rows.append([t, c[1], c[2], c[3], c[4], c[5] or 0])
            rows.sort(key=lambda r: r[0])
            if sym not in self.today_fill:
                data = self.e.rest.get(f"/v3/historical-candle/intraday/{key}/minutes/1") or {}
                self.today_fill[sym] = sorted(([dt.datetime.fromisoformat(c[0]).timestamp(), c[1], c[2], c[3], c[4], c[5] or 0]
                                               for c in data.get("candles", [])), key=lambda r: r[0])
        except PermissionError:
            raise
        except Exception as ex:
            self.e.log(f"Chart history for {sym} failed: {ex}")
        self.hist[k] = rows
        return rows

    # ------------------------------------------------------------ context for setups
    def context(self, sym, m=None) -> dict:
        m = m or self.e.metrics.get(sym) or {}
        d = self.e.daily.get(sym) or {}
        prev = d.get("prev") or {}
        o = m.get("opt") or {}
        g = self.e.gex.get(sym) or {}
        pdh, pdl, pdc = prev.get("h"), prev.get("l"), prev.get("c") or m.get("cp")
        return {"is_index": bool(m.get("is_index")), "pdh": pdh, "pdl": pdl, "pdc": pdc, "cpr": ta.cpr(pdh, pdl, pdc),
                "or_high": m.get("or_high"), "or_low": m.get("or_low"), "resistance": o.get("resistance"),
                "support": o.get("support"), "gamma": g.get("regime"), "flip": g.get("flip"),
                "call_wall": g.get("call_wall"), "put_wall": g.get("put_wall"), "max_pain": o.get("max_pain")}

    def five_min(self, sym, day):
        rows = self.today_bars(sym)
        if len(rows) < 2:
            return None
        a = np.asarray(rows, dtype=float)
        t0 = self._session_start(day)
        a = a[a[:, 0] >= t0]
        if len(a) < 2:
            return None
        t, o, h, l, c, v = ta.resample(a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5], 5, t0)
        return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}

    # ------------------------------------------------------------ live setup scanner (every closed 5-min bar)
    def scan(self, now, emit):
        t0 = self._session_start(now.date())
        slot = int((now.timestamp() - t0) // 300)
        if slot < 3 or slot == self.last_slot or now.timestamp() < t0:
            return
        self.last_slot = slot
        for sym, m in list(self.e.metrics.items()):
            b = self.five_min(sym, now.date())
            if not b or len(b["c"]) < 4:
                continue
            closed = {k: v[:slot] for k, v in b.items()} if len(b["c"]) > slot else b
            if len(closed["c"]) < 4:
                continue
            ind = setups.indicators(closed)
            ctx = self.context(sym, m)
            v = closed["v"]
            ctx["rel_vol"] = float(v[-1] / v[:-1].mean()) if len(v) > 1 and v[:-1].mean() > 0 else None
            for s in setups.evaluate(closed, ind, ctx):
                d = 1 if s["direction"] == "up" else -1
                s["ts"] = now.timestamp()
                self.active.setdefault(sym, []).append(s)
                e = s["entry"]
                hist = {"line": None, "grade": "unknown", "p": None}
                edge = getattr(self.e, "edge", None)
                if edge is not None and sym in ("NIFTY", "BANKNIFTY"):
                    try:
                        edge.load()
                        hist = edge.verdict(sym, s["setup"], s["direction"], self._edge_row(sym, m, closed, ind, ctx, s, slot))
                    except Exception:
                        pass
                detail = f"Stop {s['stop']:,.1f}, target {s['target']:,.1f} (reward:risk {s['rr']}). " + "; ".join(s["why"]) + "."
                if hist["line"]:
                    detail += " " + hist["line"]
                emit({"sym": sym, "kind": "setup_" + s["setup"], "direction": s["direction"],
                      "severity": 1 if hist["grade"] == "losing" else 2,
                      "title": f"{sym} {setups.NAMES[s['setup']]} {'long' if d > 0 else 'short'} at {e:,.1f}",
                      "detail": detail,
                      "price": m.get("spot"),
                      "data": {"setup": s["setup"], "history": hist, "setup_levels": {
                          "stop": round(s["stop"], 2), "target": round(s["target"], 2), "rr": float(s["rr"]),
                          "stop_pct": round(abs(e - s["stop"]) / e * 100, 2), "target_pct": round(abs(s["target"] - e) / e * 100, 2)}}})

    def _scenario(self, sym, day, ctx, tf, last_bar_time):
        """Three projected paths (up / sideways / down) and the 80% range, as chart series in the future."""
        sc = getattr(self.e, "scenarios", None)
        if sc is None or sym not in ("NIFTY", "BANKNIFTY"):
            return None
        today = self.today_bars(sym)
        if not today:
            return {"ready": False, "why": "Starts 15 minutes after the open."}
        try:
            vix = self.e._vix() if self.e.state else None
            f = sc.live(sym, today, day, ctx.get("pdc"), vix and vix.get("ltp"))
        except Exception as ex:
            return {"ready": False, "why": f"Scenario engine error: {ex}"}
        if not f:
            st = sc.status()["symbols"].get(sym) or {}
            why = ("Needs 1-minute history: log in to Upstox; it downloads by itself (a few minutes)." if not st.get("have")
                   else "Starts 15 minutes after the open.")
            return {"ready": False, "why": why}
        t0, t = f["t0"], f["t"]
        def series(vals):
            pts = [{"time": int(t0 + t * 60 + IST_SHIFT) // (60 * tf) * (60 * tf), "value": f["spot"]}]
            for j, v in enumerate(vals):
                minute = t + 1 + j
                if minute % tf == 0 or j == len(vals) - 1:
                    tm = int(t0 + minute * 60 + IST_SHIFT)
                    if tm > pts[-1]["time"]:
                        pts.append({"time": tm, "value": v})
            return pts
        out = {k: v for k, v in f.items() if k not in ("paths", "band")}
        out["ready"] = True
        out["paths"] = {k: {**{x: y for x, y in v.items() if x != "path"}, "line": series(v["path"])} for k, v in f["paths"].items()}
        out["band"] = {"lo": series(f["band"]["lo"]), "hi": series(f["band"]["hi"])}
        return out

    def _edge_row(self, sym, m, b, ind, ctx, s, slot) -> dict:
        """The facts the signal filter was trained on, as far as they are known live (missing ones stay empty)."""
        d = 1 if s["direction"] == "up" else -1
        c = float(b["c"][-1])
        o = m.get("opt") or {}
        vix = self.e._vix() if self.e.state else None
        o0 = float(b["o"][0])
        cp = ctx.get("cpr") or {}
        atr = float(ind["atr"][-1]) if len(ind["atr"]) else math.nan
        wall = ctx.get("resistance") if d > 0 else ctx.get("support")
        r = float(ind["rsi"][-1])
        return {"dir": d, "minute": 5 * slot - 1, "rr": s["rr"], "dte": o.get("days_to_expiry"), "iv": o.get("atm_iv"),
                "vix": vix and vix.get("ltp"), "vix_chg": vix and vix.get("chg_pct"),
                "gap_pct": (o0 / ctx["pdc"] - 1) * 100 if ctx.get("pdc") else math.nan,
                "move_pct": d * (c / o0 - 1) * 100, "dist_vwap_pct": d * (c / float(ind["vw"][-1]) - 1) * 100,
                "rsi": r if d > 0 else 100 - r, "ema_spread_pct": d * (float(ind["e9"][-1]) / float(ind["e21"][-1]) - 1) * 100,
                "st_with": int(ind["sd"][-1] == d), "cpr_width": cp.get("width_pct"),
                "risk_atr": abs(c - s["stop"]) / atr if atr and atr == atr else math.nan,
                "wall_room": ((wall - c) if d > 0 else (c - wall)) / c * 100 if wall else math.nan}

    # ------------------------------------------------------------ chart payload
    def payload(self, sym, tf=5, days=3) -> dict | None:
        e = self.e
        m = e.metrics.get(sym)
        if not m:
            return None
        day = e.clock.now().date()
        today = self.today_bars(sym)
        rows = (self.history(sym) if days > 1 or not today else []) + today
        if not rows:
            return None
        if not today and days == 1:              # market closed / before the open: show the last session
            last = max(dt.datetime.fromtimestamp(r[0], IST).date() for r in rows)
            rows = [r for r in rows if dt.datetime.fromtimestamp(r[0], IST).date() == last]
        a = np.asarray(sorted(rows, key=lambda r: r[0]), dtype=float)
        # keep the last `days` sessions
        dates = sorted({dt.datetime.fromtimestamp(x, IST).date() for x in a[:, 0]})[-days:]
        keep = np.array([dt.datetime.fromtimestamp(x, IST).date() in dates for x in a[:, 0]])
        a = a[keep]
        segs = []
        for d in dates:
            t0 = self._session_start(d)
            s = a[(a[:, 0] >= t0) & (a[:, 0] < t0 + 6.5 * 3600)]
            if len(s):
                segs.append(ta.resample(s[:, 0], s[:, 1], s[:, 2], s[:, 3], s[:, 4], s[:, 5], tf, t0))
        if not segs:
            return None
        t, o, h, l, c, v = (np.concatenate([sg[k] for sg in segs]) for k in range(6))
        vw = np.concatenate([ta.vwap_bands(sg[2], sg[3], sg[4], sg[5])[0] for sg in segs])
        bands = [np.concatenate([ta.vwap_bands(sg[2], sg[3], sg[4], sg[5])[j] for sg in segs]) for j in range(1, 5)]
        st, sd = ta.supertrend(h, l, c, 10, 3.0)
        r = ta.rsi(c, 14)
        mline, msig, mhist = ta.macd(c)
        T = (t + IST_SHIFT).astype(int)

        def line(arr):
            return [{"time": int(tt), "value": round(float(x), 2)} for tt, x in zip(T, arr) if not math.isnan(x)]
        ctx = self.context(sym, m)
        g = e.gex.get(sym) or {}
        o_ = m.get("opt") or {}
        # expected move for today from the ATM straddle (from the day's open)
        em = None
        if m.get("day_o") and o_.get("daily_move_pct"):
            em = {"up": m["day_o"] * (1 + o_["daily_move_pct"] / 100), "down": m["day_o"] * (1 - o_["daily_move_pct"] / 100)}
        lv = []
        def add(name, price, color, style=2, group="levels"):
            if _num(price):
                lv.append({"name": name, "price": round(float(price), 2), "color": color, "style": style, "group": group})
        cp = ctx.get("cpr") or {}
        add("PDH", ctx.get("pdh"), "#1E8A5A", 0, "prev")
        add("PDL", ctx.get("pdl"), "#C23B32", 0, "prev")
        add("PDC", ctx.get("pdc"), "#5E6B78", 2, "prev")
        for k, col in (("TC", "#3D2FA8"), ("P", "#3D2FA8"), ("BC", "#3D2FA8"), ("R1", "#9A6A12"), ("S1", "#9A6A12"), ("R2", "#9A6A12"), ("S2", "#9A6A12")):
            add(k if k not in ("P",) else "Pivot", cp.get(k), col, 1 if k in ("TC", "BC", "P") else 3, "cpr")
        add("OR high", m.get("or_high"), "#16212C", 3, "or")
        add("OR low", m.get("or_low"), "#16212C", 3, "or")
        add("Call OI wall", o_.get("resistance"), "#C23B32", 0, "oi")
        add("Put OI wall", o_.get("support"), "#1E8A5A", 0, "oi")
        add("Max pain", o_.get("max_pain"), "#9A6A12", 2, "oi")
        add("Gamma flip", g.get("flip"), "#E07B00", 1, "gamma")
        add("Gamma call wall", g.get("call_wall"), "#B04ACF", 2, "gamma")
        add("Gamma put wall", g.get("put_wall"), "#B04ACF", 2, "gamma")
        if em:
            add("Expected high", em["up"], "#5E6B78", 3, "em")
            add("Expected low", em["down"], "#5E6B78", 3, "em")
        # markers: alerts, setups, paper and desk trades
        mk = []
        t_first = float(t[0]) if len(t) else 0
        def tt(ts):
            return int((ts // (60 * tf)) * 60 * tf + IST_SHIFT)
        for al in e.alerts_mem:
            if al["sym"] != sym or al["ts"] < t_first:
                continue
            setup = al["kind"].startswith("setup_")
            mk.append({"time": tt(al["ts"]), "position": "belowBar" if al["direction"] == "up" else "aboveBar" if al["direction"] == "down" else "inBar",
                       "color": "#1E8A5A" if al["direction"] == "up" else "#C23B32" if al["direction"] == "down" else "#9A6A12",
                       "shape": "arrowUp" if al["direction"] == "up" else "arrowDown" if al["direction"] == "down" else "circle",
                       "text": (SHORT.get(al["kind"][6:], "") if setup else "") if al["severity"] >= 2 else "",
                       "id": al["id"], "title": al["title"], "kind": al["kind"]})
        trades = []
        if e.paper:
            for tr in e.store.trades(source=e.source, limit=300):
                if tr["sym"] == sym and tr["entry_ts"] >= t_first:
                    trades.append({"time": tt(tr["entry_ts"]), "label": "Buy", "title": f"Paper buy {tr['label']}", "kind": "paper"})
                    if tr.get("exit_ts"):
                        trades.append({"time": tt(tr["exit_ts"]), "label": f"₹{tr['net']:,.0f}", "title": f"Paper exit ({tr['reason']}) ₹{tr['net']:,.0f}", "kind": "paper"})
        try:
            for tr in e.desk.trades(source=e.source):
                if tr["sym"] == sym and tr["entry_ts"] >= t_first:
                    trades.append({"time": tt(tr["entry_ts"]), "label": "Desk", "title": f"Strategy desk: {tr['family']}", "kind": "desk"})
                    if tr.get("exit_ts"):
                        trades.append({"time": tt(tr["exit_ts"]), "label": f"₹{(tr['net'] or 0):,.0f}", "title": f"Desk exit ₹{(tr['net'] or 0):,.0f}", "kind": "desk"})
        except Exception:
            pass
        for x in trades:
            mk.append({"time": x["time"], "position": "aboveBar", "color": "#3D2FA8", "shape": "square", "text": x["label"][:12],
                       "title": x["title"], "kind": x["kind"]})
        mk.sort(key=lambda x: x["time"])
        seen = set()                         # one label per bar and side, the rest stay as plain arrows
        for m_ in mk:
            k_ = (m_["time"], m_["position"])
            if k_ in seen:
                m_["text"] = ""
            seen.add(k_)
        # chart read and active setups (today, still unresolved)
        b5 = self.five_min(sym, day)
        read, live_setups = [], []
        if b5 is not None and len(b5["c"]) >= 3:
            ind = setups.indicators(b5)
            read = setups.chart_read(b5, ind, {**ctx, "flip": g.get("flip")})
            px = m["spot"]
            for s in self.active.get(sym, [])[-8:]:
                d = 1 if s["direction"] == "up" else -1
                hit_t = (px - s["target"]) * d >= 0
                hit_s = (px - s["stop"]) * d <= 0
                live_setups.append({**s, "name": setups.NAMES[s["setup"]], "state": "target hit" if hit_t else "stopped" if hit_s else "active",
                                    "r_now": round((px - s["entry"]) * d / abs(s["entry"] - s["stop"]), 2)})
        scen = self._scenario(sym, day, ctx, tf, int(T[-1]) if len(T) else None)
        return {"sym": sym, "name": m.get("name"), "tf": tf, "spot": m.get("spot"), "chg_pct": m.get("chg_pct"), "scenario": scen,
                "candles": [{"time": int(a_), "open": round(float(b_), 2), "high": round(float(c_), 2), "low": round(float(d_), 2), "close": round(float(e_), 2)}
                            for a_, b_, c_, d_, e_ in zip(T, o, h, l, c)],
                "volume": [{"time": int(a_), "value": float(x), "color": "rgba(30,138,90,.45)" if cc >= oo else "rgba(194,59,50,.45)"}
                           for a_, x, cc, oo in zip(T, v, c, o)],
                "has_volume": bool(np.nansum(v) > 0),
                "series": {"vwap": line(vw), "u1": line(bands[0]), "d1": line(bands[1]), "u2": line(bands[2]), "d2": line(bands[3]),
                           "ema9": line(ta.ema(c, 9)), "ema21": line(ta.ema(c, 21)), "ema50": line(ta.ema(c, 50)),
                           "st_up": line(np.where(sd > 0, st, np.nan)), "st_dn": line(np.where(sd < 0, st, np.nan)),
                           "rsi": line(r), "macd": line(mline), "macd_sig": line(msig),
                           "macd_hist": [{"time": int(a_), "value": round(float(x), 2), "color": "rgba(30,138,90,.6)" if x >= 0 else "rgba(194,59,50,.6)"}
                                         for a_, x in zip(T, mhist) if not math.isnan(x)]},
                "levels": lv, "markers": mk, "read": read, "setups": live_setups, "ctx": {k: v for k, v in ctx.items() if k != "cpr"},
                "cpr": ctx.get("cpr"), "regime": e.regimes.get(sym), "updated": time.time()}
