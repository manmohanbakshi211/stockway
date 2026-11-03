"""Multi-timeframe charts: 1D / 75m / 1H / 15m / 5m candles, the markings that suit each timeframe, higher-timeframe
levels drawn on lower charts, confluence zones, a trend strip, and strategy overlays (apply / remove).

Data: Dhan (daily candles and 15-minute / 5-minute intraday, kept on disk in data/charts, refreshed at most every 30
minutes) plus today's live 1-minute bars from the scanner. 1H and 75m are built from 15-minute bars aligned to 09:15
(75m = five candles a day, the Indian standard). Without Dhan it falls back to the scanner's own history.

Markings are computed here (the same maths as the scanner and the Lab), not drawn by AI, so what the chart shows is
what gets tested. Each marking is a "layer"; a layer that fails is skipped and the chart still draws.
Market data only.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import time
from pathlib import Path

import numpy as np

from . import patterns as PT
from . import setups, ta
from .clock import IST

IST_SHIFT = 19800
INDEX_IDS = {"NIFTY": 13, "BANKNIFTY": 25, "FINNIFTY": 27, "MIDCPNIFTY": 442}
TFS = {
    "1D": {"label": "Daily", "base": "D", "days": 400},
    "75m": {"label": "75 min", "base": 15, "mins": 75, "days": 60},
    "1H": {"label": "1 hour", "base": 15, "mins": 60, "days": 60},
    "15m": {"label": "15 min", "base": 15, "mins": 15, "days": 20},
    "5m": {"label": "5 min", "base": 5, "mins": 5, "days": 5},
}
ORDER = ["1D", "75m", "1H", "15m", "5m"]
REFRESH_S = 1800
TF_COLORS = {"1D": "#3D2FA8", "75m": "#9A6A12", "1H": "#B04ACF", "15m": "#0F7C8C", "5m": "#5E6B78"}


def _f(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else round(x, 2)


def _session_start(day: dt.date) -> float:
    return dt.datetime.combine(day, dt.time(9, 15), IST).timestamp()


def resample(rows: list, minutes: int) -> list:
    """[t, o, h, l, c, v] bars (any base that divides `minutes`) -> `minutes` bars aligned to each day's 09:15."""
    out, cur, key = [], None, None
    for r in rows:
        day = dt.datetime.fromtimestamp(r[0], IST).date()
        t0 = _session_start(day)
        k = (day, int((r[0] - t0) // (60 * minutes)))
        if k != key:
            if cur:
                out.append(cur)
            key, cur = k, [t0 + k[1] * 60 * minutes, r[1], r[2], r[3], r[4], r[5] or 0]
        else:
            cur[2], cur[3], cur[4], cur[5] = max(cur[2], r[2]), min(cur[3], r[3]), r[4], cur[5] + (r[5] or 0)
    if cur:
        out.append(cur)
    return out


def daily_from(rows: list) -> list:
    out = {}
    for r in rows:
        d = dt.datetime.fromtimestamp(r[0], IST).date()
        if d not in out:
            out[d] = [_session_start(d), r[1], r[2], r[3], r[4], r[5] or 0]
        else:
            o = out[d]
            o[2], o[3], o[4], o[5] = max(o[2], r[2]), min(o[3], r[3]), r[4], o[5] + (r[5] or 0)
    return [out[d] for d in sorted(out)]


class MTF:
    def __init__(self, engine):
        self.e = engine
        self.dir = Path(engine.data_dir) / "charts"
        self.mem: dict = {}
        self.errors: dict = {}

    # ------------------------------------------------------------ data
    def _dhan(self):
        from lab import dhan_token
        lab = self.e.cfg().get("lab") or {}
        tok = dhan_token.latest()
        if not (lab.get("dhan_client_id") and tok) or dhan_token.is_dead(tok):
            return None
        from lab.dhan import Dhan
        return Dhan(lab["dhan_client_id"], tok, per_sec=2.0)

    def _sec(self, sym):
        if sym in INDEX_IDS:
            return INDEX_IDS[sym], "IDX_I", "INDEX"
        from lab.dhan_hist import stock_ids
        ids = stock_ids(Path(self.e.data_dir), [sym], fetch=False)
        return (ids[sym]["id"], "NSE_EQ", "EQUITY") if sym in ids else None

    def _rows(self, res) -> list:
        ts = res.get("timestamp") or []
        o, h, l, c = (res.get(k) or [] for k in ("open", "high", "low", "close"))
        v = res.get("volume") or [0] * len(ts)
        return [[float(ts[i]), float(o[i]), float(h[i]), float(l[i]), float(c[i]), float(v[i] or 0)]
                for i in range(min(len(ts), len(o), len(h), len(l), len(c))) if o[i] and c[i]]

    def _fetch(self, sym, base, days) -> list | None:
        d = self._dhan()
        sec = self._sec(sym) if d else None
        if not sec:
            return None
        sid, seg, inst = sec
        end = dt.date.today() + dt.timedelta(days=1)
        if base == "D":
            res = d.daily(sym, (end - dt.timedelta(days=days)).isoformat(), end.isoformat(), security_id=sid, segment=seg, instrument=inst)
            rows = self._rows(res)
            return [[_session_start(dt.datetime.fromtimestamp(r[0], IST).date())] + r[1:] for r in rows]
        start = end - dt.timedelta(days=int(days * 1.5) + 3)
        out = []
        while start < end:                               # Dhan serves at most 90 days per intraday call
            stop = min(end, start + dt.timedelta(days=89))
            res = d.post("/charts/intraday", {"securityId": str(sid), "exchangeSegment": seg, "instrument": inst,
                                              "interval": str(base), "oi": False, "fromDate": start.isoformat(), "toDate": stop.isoformat()})
            out += self._rows(res.get("data", res) if isinstance(res, dict) else {})
            start = stop
        return sorted({r[0]: r for r in out}.values(), key=lambda r: r[0])

    def base_rows(self, sym, base, days) -> list:
        """Cached Dhan bars for (sym, base); refreshed at most every 30 min. Falls back to the scanner's history."""
        k = (sym, base)
        hit = self.mem.get(k)
        if hit and time.time() - hit[0] < REFRESH_S:
            return hit[1]
        f = self.dir / f"{sym}_{base}.json"
        rows = None
        try:
            rows = self._fetch(sym, base, days)
            self.errors.pop(k, None)
        except PermissionError as ex:
            self.errors[k] = str(ex)[:120]
        except Exception as ex:
            self.errors[k] = str(ex)[:120]
        if rows:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps({"at": time.time(), "rows": rows}))
            tmp.replace(f)
        elif f.exists():
            try:
                rows = json.loads(f.read_text())["rows"]
            except Exception:
                rows = None
        if not rows:                                     # no Dhan: the scanner's own 1-minute history
            one = []
            try:
                one = (self.e.charts.history(sym) or []) or self.e.charts.offline_rows(sym)
            except Exception:
                one = []
            rows = daily_from(one) if base == "D" else resample(sorted(one, key=lambda r: r[0]), base) if one else []
        self.mem[k] = (time.time(), rows)
        return rows

    def bars(self, sym, tf) -> list:
        spec = TFS[tf]
        base = spec["base"]
        rows = list(self.base_rows(sym, base, spec["days"]))
        live = []
        try:
            live = self.e.charts.today_bars(sym) if self.e.metrics.get(sym) else []
        except Exception:
            live = []
        if live:                                         # today's live minutes replace today's cached bars
            day0 = _session_start(dt.datetime.fromtimestamp(live[-1][0], IST).date())
            live = [r for r in live if day0 <= r[0] < day0 + 6.5 * 3600]
            rows = [r for r in rows if r[0] < day0] + (daily_from(live) if base == "D" else resample(live, base))
        if base != "D" and spec["mins"] != base:
            rows = resample(rows, spec["mins"])
        cut = time.time() - spec["days"] * 86400 * (1.45 if base != "D" else 1.0)
        return [r for r in rows if r[0] >= cut]

    # ------------------------------------------------------------ markings
    def _levels_daily(self, b) -> list:
        lv = []
        if len(b) < 3:
            return lv
        today = dt.datetime.now(IST).date()
        done = [r for r in b if dt.datetime.fromtimestamp(r[0], IST).date() < today] or b[:-1]
        if done:
            y = done[-1]
            lv += [("PDH", y[2], "prev"), ("PDL", y[3], "prev"), ("PDC", y[4], "prev")]
        def period(key):
            groups = {}
            for r in b:
                d = dt.datetime.fromtimestamp(r[0], IST).date()
                groups.setdefault(key(d), []).append(r)
            ks = sorted(groups)
            return groups[ks[-2]] if len(ks) >= 2 else None
        w = period(lambda d: d.isocalendar()[:2])
        if w:
            lv += [("PWH", max(r[2] for r in w), "week"), ("PWL", min(r[3] for r in w), "week")]
        m = period(lambda d: (d.year, d.month))
        if m:
            lv += [("PMH", max(r[2] for r in m), "month"), ("PML", min(r[3] for r in m), "month")]
        return lv

    def _swing_levels(self, b, k=1.3, n=2) -> tuple:
        if len(b) < 12:
            return [], [], None
        sw = PT.swings_for_chart({"h": [r[2] for r in b], "l": [r[3] for r in b], "c": [r[4] for r in b]}, k)
        hs = [s for s in sw if s["type"] == "H"][-n:]
        ls = [s for s in sw if s["type"] == "L"][-n:]
        lv = [(f"Swing high", s["price"], "swing") for s in hs] + [(f"Swing low", s["price"], "swing") for s in ls]
        return lv, sw, PT.structure(sw)

    def panel(self, sym, tf, m: dict | None) -> dict:
        b = self.bars(sym, tf)
        out = {"tf": tf, "label": TFS[tf]["label"], "candles": [], "series": {}, "levels": [], "markers": [], "trend": None}
        if not b:
            out["empty"] = (self.errors.get((sym, TFS[tf]["base"])) or "No bars yet")
            return out
        a = np.asarray(b, dtype=float)
        T = (a[:, 0] + IST_SHIFT).astype(int)
        o, h, l, c, v = a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5]
        out["candles"] = [{"time": int(t), "open": _f(x1), "high": _f(x2), "low": _f(x3), "close": _f(x4)} for t, x1, x2, x3, x4 in zip(T, o, h, l, c)]

        def line(arr):
            return [{"time": int(t), "value": _f(x)} for t, x in zip(T, arr) if _f(x) is not None]
        layers = {}

        def layer(name, fn):
            try:
                layers[name] = fn()
            except Exception as ex:
                out.setdefault("failed", []).append(f"{name}: {str(ex)[:80]}")
        e20, e50 = ta.ema(c, 20), ta.ema(c, 50)
        st, sd = ta.supertrend(h, l, c, 10, 3.0)
        lv = []
        if tf == "1D":
            layer("ema", lambda: out["series"].update(ema20=line(e20), ema50=line(e50), ema200=line(ta.ema(c, 200))))
            layer("daily levels", lambda: lv.extend(self._levels_daily(b)))
        if tf in ("75m", "1H", "1D"):
            if tf != "1D":
                layer("ema", lambda: out["series"].update(ema20=line(e20), ema50=line(e50)))
                layer("supertrend", lambda: out["series"].update(st_up=line(np.where(sd > 0, st, np.nan)), st_dn=line(np.where(sd < 0, st, np.nan))))

            def sw():
                s_lv, s, struct = self._swing_levels(b, 1.5 if tf == "1D" else 1.3)
                lv.extend(s_lv)
                out["structure"] = struct
                out["markers"] += [{"time": int(T[x["i"]]), "position": "aboveBar" if x["type"] == "H" else "belowBar",
                                    "color": "#5E6B78", "shape": "circle", "text": x["label"] or ""} for x in s[-8:] if x["i"] < len(T)]
            layer("swings", sw)
        if tf in ("15m", "5m"):
            def vw():
                day0 = _session_start(dt.datetime.fromtimestamp(a[-1, 0], IST).date())
                sl = a[:, 0] >= day0
                if sl.sum() >= 1:
                    bands = ta.vwap_bands(h[sl], l[sl], c[sl], v[sl])
                    T2 = T[sl]
                    out["series"]["vwap"] = [{"time": int(t), "value": _f(x)} for t, x in zip(T2, bands[0]) if _f(x) is not None]
                    if tf == "5m":
                        out["series"]["u1"] = [{"time": int(t), "value": _f(x)} for t, x in zip(T2, bands[1]) if _f(x) is not None]
                        out["series"]["d1"] = [{"time": int(t), "value": _f(x)} for t, x in zip(T2, bands[2]) if _f(x) is not None]
                    nor = 2 if tf == "15m" else 3              # 30-min range on 15m, 15-min range on 5m
                    oh, ol = h[sl][:nor], l[sl][:nor]
                    if len(oh) >= nor:
                        lv.extend([(f"OR{nor * (15 if tf == '15m' else 5)} high", float(oh.max()), "or"),
                                   (f"OR{nor * (15 if tf == '15m' else 5)} low", float(ol.min()), "or")])
            layer("vwap & opening range", vw)
            if tf == "5m":
                layer("ema", lambda: out["series"].update(ema9=line(ta.ema(c, 9)), ema21=line(ta.ema(c, 21))))

                def oi():
                    o_ = (m or {}).get("opt") or {}
                    lv.extend([(n, p, "oi") for n, p in (("Call OI wall", o_.get("resistance")), ("Put OI wall", o_.get("support")),
                                                          ("Max pain", o_.get("max_pain"))) if _f(p)])
                layer("OI walls", oi)
            else:
                layer("ema", lambda: out["series"].update(ema20=line(e20)))

                def cpr():
                    days = sorted({dt.datetime.fromtimestamp(x, IST).date() for x in a[:, 0]})
                    if len(days) >= 2:
                        p = a[np.array([dt.datetime.fromtimestamp(x, IST).date() == days[-2] for x in a[:, 0]])]
                        cp = ta.cpr(p[:, 2].max(), p[:, 3].min(), p[:, 4][-1])
                        lv.extend([("CPR top", cp["TC"], "cpr"), ("Pivot", cp["P"], "cpr"), ("CPR bottom", cp["BC"], "cpr"),
                                   ("R1", cp["R1"], "cpr"), ("S1", cp["S1"], "cpr")])
                layer("CPR", cpr)

                def pats():
                    bb = {"o": o, "h": h, "l": l, "c": c, "v": v}
                    for p in PT.detect(bb, [], recent=6)[:4]:
                        i = min(len(T) - 1, int(p.get("i1", len(T) - 1)))
                        out["markers"].append({"time": int(T[i]), "position": "aboveBar" if p["direction"] == "down" else "belowBar",
                                               "color": "#C23B32" if p["direction"] == "down" else "#1E8A5A",
                                               "shape": "arrowDown" if p["direction"] == "down" else "arrowUp",
                                               "text": PT.NAMES.get(p["pattern"], p["pattern"])[:18]})
                layer("patterns", pats)
        out["levels"] = [{"name": n, "price": _f(p), "group": g, "tf": tf} for n, p, g in lv if _f(p) is not None]
        # trend: price vs EMA20, EMA20 vs EMA50, Supertrend
        try:
            s = 0
            if not math.isnan(e20[-1]):
                s += 1 if c[-1] > e20[-1] else -1
            if not (math.isnan(e20[-1]) or math.isnan(e50[-1])):
                s += 1 if e20[-1] > e50[-1] else -1
            if sd[-1]:
                s += 1 if sd[-1] > 0 else -1
            out["trend"] = "up" if s >= 2 else "down" if s <= -2 else "sideways"
            out["trend_score"] = s
        except Exception:
            out["trend"] = None
        out["last"] = _f(c[-1])
        if out.get("failed"):
            out["failed"] = out["failed"][:5]
        return out

    def view(self, sym, tfs=None) -> dict:
        tfs = [t for t in (tfs or ORDER) if t in TFS]
        m = self.e.metrics.get(sym)
        panels = {tf: self.panel(sym, tf, m) for tf in tfs}
        # higher-timeframe levels on each lower chart
        for i, tf in enumerate(ORDER):
            if tf not in panels:
                continue
            htf = []
            for up in ORDER[:i]:
                if up in panels:
                    htf += [{**x, "htf": True, "name": f"{up}·{x['name']}"} for x in panels[up]["levels"]
                            if x["group"] in ("prev", "week", "month", "swing")]
            panels[tf]["htf_levels"] = htf
        spot = (m or {}).get("spot") or next((p["last"] for p in reversed(list(panels.values())) if p.get("last")), None)
        conf = self.confluence(panels, spot, sym)
        trend = [{"tf": tf, "trend": panels[tf].get("trend"), "structure": panels[tf].get("structure")} for tf in tfs]
        ups = sum(t["trend"] == "up" for t in trend)
        dns = sum(t["trend"] == "down" for t in trend)
        n = sum(t["trend"] is not None for t in trend)
        summary = (f"{ups} of {n} timeframes up" if ups > dns else f"{dns} of {n} timeframes down" if dns > ups else "mixed") if n else None
        return {"sym": sym, "spot": _f(spot), "panels": panels, "confluence": conf, "trend": trend, "summary": summary,
                "aligned": (ups == n or dns == n) and n >= 3}

    @staticmethod
    def confluence(panels: dict, spot, sym) -> list:
        """Price zones where levels from 2+ timeframes meet (the strongest places on the chart)."""
        pts = [(x["price"], tf, x["name"]) for tf, p in panels.items() for x in p.get("levels", []) if x.get("price")]
        if not pts or not spot:
            return []
        tol = spot * (0.0012 if sym in INDEX_IDS else 0.0025)
        pts.sort()
        zones, cur = [], [pts[0]]
        for p in pts[1:]:
            if p[0] - cur[-1][0] <= tol:
                cur.append(p)
            else:
                zones.append(cur)
                cur = [p]
        zones.append(cur)
        out = []
        for z in zones:
            tfs = sorted({x[1] for x in z}, key=ORDER.index)
            if len(tfs) >= 2:
                lo, hi = min(x[0] for x in z), max(x[0] for x in z)
                out.append({"lo": _f(lo), "hi": _f(hi), "mid": _f((lo + hi) / 2), "tfs": tfs, "strength": len(tfs),
                            "names": [f"{x[1]}·{x[2]}" for x in z][:6], "dist_pct": round(((lo + hi) / 2 / spot - 1) * 100, 2)})
        out.sort(key=lambda z: (-z["strength"], abs(z["dist_pct"])))
        return out[:6]

    # ------------------------------------------------------------ strategy overlays
    def overlays(self, sym) -> list:
        from . import registry
        out = [{"id": f"setup:{k}", "name": f"Setup: {v}", "type": "setup"} for k, v in registry.names("setup").items() if k != "wall_fade"]
        out += [{"id": f"candle:{k}", "name": f"Candle{' (on trial)' if registry.is_shadow('candle_' + k) else ''}: {v}", "type": "candle"}
                for k, v in registry.names("candle").items()]
        try:
            from lab import factory
            for r in self.e.factory_rules():
                if r.get("ml") or not factory.sym_ok(r["syms"], sym) or r.get("dir", 0) == 0:
                    continue
                out.append({"id": f"lab:{r['id']}", "name": f"Lab rule {r['id']}: {r['rule'][:70]}", "type": "lab"})
        except Exception:
            pass
        return out

    def overlay(self, sym, oid: str, days: int = 10) -> dict:
        """Where a strategy would have fired on the last `days` sessions of 5-minute bars, with stop / target and how
        each one ended (target first, stop first, or still open at 15:15)."""
        rows = resample(sorted(self.base_rows(sym, 5, max(days, 5) + 5), key=lambda r: r[0]), 5)
        try:
            live = self.e.charts.today_bars(sym) if self.e.metrics.get(sym) else []
        except Exception:
            live = []
        if live:
            day0 = _session_start(dt.datetime.fromtimestamp(live[-1][0], IST).date())
            rows = [r for r in rows if r[0] < day0] + resample([r for r in live if r[0] >= day0], 5)
        by_day = {}
        for r in rows:
            by_day.setdefault(dt.datetime.fromtimestamp(r[0], IST).date(), []).append(r)
        dates = sorted(by_day)
        sigs = []
        kind, key = oid.split(":", 1)
        rule = None
        if kind == "lab":
            rule = next((r for r in self.e.factory_rules() if r["id"] == key), None)
            if not rule:
                return {"id": oid, "signals": [], "error": "Rule not live any more"}
        for di, d in enumerate(dates[-days:]):
            bars = by_day[d]
            prev = by_day.get(dates[dates.index(d) - 1]) if dates.index(d) > 0 else None
            if len(bars) < 4:
                continue
            try:
                sigs += self._overlay_day(sym, kind, key, rule, bars, prev)
            except Exception as ex:
                return {"id": oid, "signals": sigs, "error": str(ex)[:160]}
        done = [s for s in sigs if s["result"] in ("target", "stop", "time")]
        wins = int(sum(1 for s in done if s["r"] > 0))
        stats = {"n": len(sigs), "wins": wins, "losses": len(done) - wins,
                 "avg_r": round(float(sum(s["r"] for s in done)) / len(done), 2) if done else None,
                 "days": len(dates[-days:])}
        note = ("On the underlying's 5-minute chart, stop and target first touched within the day; options, costs "
                "and slippage are not included here (the Strategy Lab tests those).")
        if rule:                       # facts the chart can't rebuild for past days: say so instead of drawing too few
            missing = sorted({c[0] for c in rule.get("conds", []) if c[0] in ("vix", "dte", "iv", "iv_chg3", "prem_pct",
                                                                                 "d_trend", "d_vs_ema20", "d_pos20")})
            if missing:
                note += (f" This rule also uses {', '.join(missing)}, which the chart does not have for past days, "
                         "so it shows fewer signals here than the Lab and the live scanner see.")
        return {"id": oid, "signals": sigs, "stats": stats, "note": note}

    def _overlay_day(self, sym, kind, key, rule, bars, prev) -> list:
        from . import registry
        a = np.asarray(bars, dtype=float)
        t, o, h, l, c, v = (a[:, k] for k in range(6))
        ctx = {"is_index": sym in INDEX_IDS}
        if prev:
            ph, pl, pc = max(r[2] for r in prev), min(r[3] for r in prev), prev[-1][4]
            ctx.update(pdh=ph, pdl=pl, pdc=pc, cpr=ta.cpr(ph, pl, pc))
        out, last_fire = [], {}
        cool = (registry.get(f"{kind}_{key}").get("cooldown") or 45) * 60
        if kind == "candle":
            from . import candles as CDL
            spec = registry.candle_spec()
            if key not in spec:
                return out
            lv = [x for x in (ctx.get("pdh"), ctx.get("pdl"), ctx.get("pdc")) if x]
            n = len(c)
            orh = np.where(np.arange(n) >= 3, h[:3].max(), np.nan) if n > 3 else np.full(n, np.nan)
            orl = np.where(np.arange(n) >= 3, l[:3].min(), np.nan) if n > 3 else np.full(n, np.nan)
            for x in CDL.trade_ideas(o, h, l, c, lv + [orh, orl], {key: spec[key]}):
                i = x["i"]
                if i < 3 or i >= n - 1 or t[i] - last_fire.get(x["d"], -1e18) < cool:
                    continue
                last_fire[x["d"]] = t[i]
                why = registry.names("candle").get(key, key) + (f", {x['lvl_atr']:.1f} ATR from a key level" if x["lvl_atr"] is not None else "")
                out.append(self._resolve(t, h, l, c, i, x["d"], x["entry"], x["stop"], x["target"], why))
            return out
        if kind == "lab":
            from lab import exits as X
            from lab import factory
            fctx = {"prev_close": ctx.get("pdc"), "prev_high": ctx.get("pdh"), "prev_low": ctx.get("pdl"),
                    "gap": (o[0] / ctx["pdc"] - 1) * 100 if ctx.get("pdc") else None, "vix": None, "dte": None,
                    "dow": dt.datetime.fromtimestamp(t[0], IST).weekday()}
            f = factory.bar_features(o, h, l, c, fctx, [np.nan] * len(c), [np.nan] * len(c))
            fired = 0
            for i in range(3, len(c) - 1):
                if fired >= rule.get("max_day", 1) or not factory.signal_now(rule, f, sym, i):
                    continue
                fired += 1
                d = rule["dir"]
                style = rule["exit"] if rule["exit"] in X.STYLES else "atr2r"
                R = X.risk_pts(style, c[i], d, X.atr14(h[:i + 1], l[:i + 1], c[:i + 1])[-1], l[i], h[i])
                if not np.isfinite(R) or R <= 0:
                    continue
                tgt_r = X.STYLES[style]["target_r"] or 2.0
                out.append(self._resolve(t, h, l, c, i, d, c[i], c[i] - d * R, c[i] + d * tgt_r * R,
                                         f"Lab {key} ({'chart exit ' + style if rule['exit'] in X.STYLES else 'shown with a 1 ATR stop, 2R target'})"))
            return out
        for i in range(3, len(c) - 1):
            pre = {"o": o[:i + 1], "h": h[:i + 1], "l": l[:i + 1], "c": c[:i + 1], "v": v[:i + 1]}
            c2 = dict(ctx)
            if i >= 3:
                c2["or_high"], c2["or_low"] = float(h[:3].max()), float(l[:3].min())
            c2["rel_vol"] = float(v[i] / v[:i].mean()) if v[:i].mean() > 0 else None
            for s in setups.evaluate(pre, setups.indicators(pre), c2):
                if s["setup"] != key:
                    continue
                k = (s["setup"], s["direction"])
                if t[i] - last_fire.get(k, -1e18) < cool:
                    continue
                last_fire[k] = t[i]
                d = 1 if s["direction"] == "up" else -1
                out.append(self._resolve(t, h, l, c, i, d, s["entry"], s["stop"], s["target"], "; ".join(s.get("why", []))[:120]))
        return out

    @staticmethod
    def _resolve(t, h, l, c, i, d, entry, stop, target, why) -> dict:
        risk = abs(entry - stop) or 1e-9
        res, exit_px, j_end = "open", c[-1], len(c) - 1
        for j in range(i + 1, len(c)):
            hit_s = (l[j] <= stop) if d > 0 else (h[j] >= stop)
            hit_t = (h[j] >= target) if d > 0 else (l[j] <= target)
            if hit_s:                                    # both in one bar: assume the stop came first (honest)
                res, exit_px, j_end = "stop", stop, j
                break
            if hit_t:
                res, exit_px, j_end = "target", target, j
                break
        if res == "open" and dt.datetime.fromtimestamp(t[-1], IST).time() >= dt.time(15, 10):
            res = "time"
        return {"time": int(t[i] + IST_SHIFT), "exit_time": int(t[j_end] + IST_SHIFT), "dir": "up" if d > 0 else "down",
                "entry": _f(entry), "stop": _f(stop), "target": _f(target), "exit": _f(exit_px), "result": res,
                "r": round(float((exit_px - entry) * d / risk), 2), "why": why}
