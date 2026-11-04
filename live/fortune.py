"""Fortune chart (gun): honest ranges for the candle forming now and the next few, from the symbol's own history.

For the timeframe on screen, every past candle is replayed: cut at the same point the forming candle has reached
(say 70% of its time) and compared with how it really ended. That gives
- where the forming candle's close lands (68% / 95% ranges) and how far its high / low still stretch,
- how often its shape so far (a hammer, an engulfing...) stayed that shape when it closed,
- how often the rest of the candle went up (about 50% = no direction edge: said plainly),
- a range cone for the next `horizon` candles (moves measured in the ATR of the time, so quiet and wild days both fit),
- a "lean" from the trend on this timeframe with its own historical hit rate (not proven, shown as such).
Every forecast served is checked when its candle closes, so the page can say how well the ranges really held.
Settings are bullets in rules/fortune.json. Information only: nothing here is a signal or touches the paper account.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import threading
import time
from pathlib import Path

import numpy as np

from .clock import CLOSE, IST, OPEN

FILE = Path(__file__).resolve().parents[1] / "rules" / "fortune.json"
DEFAULTS = {"horizon": 6, "history_days": {"3m": 6, "5m": 10, "15m": 25, "30m": 40, "1H": 60, "1D": 150, "1W": 1800, "1M": 3650},
            "min_samples": 30, "grade_from": 0.5, "ghost": True, "ghost_candles": 7, "refresh_s": 10,
            "show_cone": False}
INTRA = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1H": 60}
IST_SHIFT = 19800
_C: dict = {}


def cfg() -> dict:
    try:
        mt = FILE.stat().st_mtime
        if _C.get("mt") != mt:
            raw = json.loads(FILE.read_text())
            _C.update(mt=mt, cfg={**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}, error=None)
        return _C["cfg"]
    except Exception as e:
        _C.update(cfg=dict(DEFAULTS), error=str(e)[:160])
        return _C["cfg"]


def _day(t):
    return dt.datetime.fromtimestamp(t, IST).date()


def _open_ts(d: dt.date) -> float:
    return dt.datetime.combine(d, OPEN, IST).timestamp()


def groups(rows: list, tf: str) -> list:
    """Base bars [t, o, h, l, c, v] -> [(start, [bars...])] per candle of `tf` (sessions start 09:15 IST)."""
    out, key, cur = [], None, None
    for r in sorted(rows, key=lambda r: r[0]):
        d = _day(r[0])
        if tf in INTRA:
            k = (d, int((r[0] - _open_ts(d)) // (60 * INTRA[tf])))
            start = _open_ts(d) + k[1] * 60 * INTRA[tf]
        elif tf == "1D":
            k, start = d, _open_ts(d)
        elif tf == "1W":
            k = d.isocalendar()[:2]
            start = _open_ts(d - dt.timedelta(days=d.weekday()))
        else:
            k = (d.year, d.month)
            start = _open_ts(d.replace(day=1))
        if k != key:
            if cur:
                out.append(cur)
            key, cur = k, (start, [r])
        else:
            cur[1].append(r)
    if cur:
        out.append(cur)
    return out


def ohlc(start, rows) -> list:
    return [start, rows[0][1], max(r[2] for r in rows), min(r[3] for r in rows), rows[-1][4], sum((r[5] or 0) for r in rows)]


def atr_before(c: np.ndarray, n=14) -> np.ndarray:
    """ATR known before each candle (mean true range of the n candles before it)."""
    h, l, cl = c[:, 2], c[:, 3], c[:, 4]
    pc = np.r_[cl[0], cl[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    out = np.full(len(c), np.nan)
    for i in range(1, len(c)):
        w = tr[max(0, i - n):i]
        out[i] = float(w.mean()) if len(w) else np.nan
    return out


def _q(xs, ps=(2.5, 16, 50, 84, 97.5)):
    a = np.asarray(xs, float)
    return [float(np.percentile(a, p)) for p in ps] if len(a) else None


def _ema(x, n):
    a = 2 / (n + 1)
    out = np.empty(len(x))
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def fraction(tf: str, start: float, now: float) -> float:
    """How much of a candle's time has passed (0-1)."""
    if tf in INTRA:
        return max(0.0, min(1.0, (now - start) / (INTRA[tf] * 60)))
    d0, today = _day(start), _day(now)
    mins = max(0.0, min(375.0, (now - _open_ts(today)) / 60))
    if tf == "1D":
        return mins / 375
    import numpy as _np
    if tf == "1W":
        done = int(_np.busday_count(d0, today))
        return min(1.0, (done + mins / 375) / 5)
    end = (d0.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    total = max(1, int(_np.busday_count(d0, end)))
    return min(1.0, (int(_np.busday_count(d0, today)) + mins / 375) / total)


def next_times(tf: str, last_start: float, n: int) -> list:
    """Start times of the next n candles (market hours, weekdays; holidays skipped for daily candles)."""
    out, t = [], last_start
    try:
        from .health import is_trading_day
    except Exception:
        is_trading_day = lambda d: d.weekday() < 5
    for _ in range(n):
        if tf in INTRA:
            t += INTRA[tf] * 60
            d = _day(t)
            if t >= dt.datetime.combine(d, CLOSE, IST).timestamp() or d.weekday() >= 5:
                nd = d + dt.timedelta(days=1)
                while not is_trading_day(nd):
                    nd += dt.timedelta(days=1)
                t = _open_ts(nd)
        elif tf == "1D":
            nd = _day(t) + dt.timedelta(days=1)
            while not is_trading_day(nd):
                nd += dt.timedelta(days=1)
            t = _open_ts(nd)
        elif tf == "1W":
            t = _open_ts(_day(t) + dt.timedelta(days=7))
        else:
            d = _day(t)
            t = _open_ts((d.replace(day=28) + dt.timedelta(days=4)).replace(day=1))
        out.append(t)
    return out


def replay(grps: list, cands: np.ndarray, atr: np.ndarray, f: float, spec: dict | None = None) -> dict:
    """Cut every finished candle at fraction f and compare with its end: rest-of-candle move, high / low stretch and
    shape kept, all in ATRs of the time."""
    from . import candles as CDL
    rest, up_ext, dn_ext, hold = [], [], [], {}
    finals = {}
    if spec is not None and len(cands) >= 3:
        allp = CDL.all_patterns(cands[:, 1], cands[:, 2], cands[:, 3], cands[:, 4])
        finals = {name: allp[fact] for name, (_, fact, _) in CDL.PATTERNS.items() if fact in allp}
    for i, (start, rows) in enumerate(grps):
        if i < 15 or len(rows) < 2 or not (atr[i] > 0):
            continue
        k = max(1, min(len(rows) - 1, int(round(f * len(rows)))))
        p = ohlc(start, rows[:k])
        fin = cands[i]
        rest.append((fin[4] - p[4]) / atr[i])
        up_ext.append(max(0.0, fin[2] - p[2]) / atr[i])
        dn_ext.append(max(0.0, p[3] - fin[3]) / atr[i])
        if spec is not None:
            lo = max(0, i - 59)
            seg = np.vstack([cands[lo:i], np.asarray(p, float)[None, :]])
            pats, _ = CDL.last_bar_scan(seg[:, 1], seg[:, 2], seg[:, 3], seg[:, 4], [], {})
            for name, v in pats.items():
                h = hold.setdefault(name, [0, 0])
                h[0] += 1
                fv = finals.get(name)
                if fv is not None and fv[i] == v:
                    h[1] += 1
    return {"n": len(rest), "rest_q": _q(rest), "up_share": float(np.mean(np.asarray(rest) > 0)) if rest else None,
            "up_ext_med": float(np.median(up_ext)) if up_ext else None, "dn_ext_med": float(np.median(dn_ext)) if dn_ext else None,
            "hold": {k: {"n": v[0], "held": v[1], "pct": round(v[1] / v[0] * 100) if v[0] else None} for k, v in hold.items()}}


def cone(cands: np.ndarray, atr: np.ndarray, horizon: int) -> dict:
    """Quantiles of the move h candles ahead (h = 1..horizon), in ATRs, from every finished candle in the history."""
    c = cands[:, 4]
    out = {}
    for h in range(1, horizon + 1):
        xs = [(c[i + h] - c[i]) / atr[i] for i in range(15, len(c) - h) if atr[i] > 0]
        out[h] = {"n": len(xs), "q": _q(xs)}
    return out


def lean(cands: np.ndarray) -> dict:
    """Trend on this timeframe (EMA 20 vs 50) and how often the next candle went its way in this history."""
    c = cands[:, 4]
    if len(c) < 60:
        return {}
    e20, e50 = _ema(c, 20), _ema(c, 50)
    tr = np.where((c > e20) & (e20 > e50), 1, np.where((c < e20) & (e20 < e50), -1, 0))
    nxt = np.sign(np.r_[c[1:] - c[:-1], 0])
    m = (tr[:-1] != 0)
    agree = (nxt[:-1][m] == tr[:-1][m])
    return {"trend": int(tr[-1]), "with_trend_pct": round(float(agree.mean()) * 100) if m.sum() else None, "n": int(m.sum())}


class Fortune:
    def __init__(self, engine):
        self.e = engine
        self.cache: dict = {}
        self.pending: dict = {}                     # (sym, tf, start) -> forecast to grade when the candle closes
        self.lock = threading.Lock()
        try:
            self.grades = engine.store.get("fortune:grades", {}) or {}
        except Exception:
            self.grades = {}

    def _rows(self, mtf, sym, tf, days):
        """Base bars for a timeframe (1-min for intraday, 15-min for daily, daily for weekly / monthly) + live minutes."""
        from .mtf import daily_from, resample
        base = 1 if tf in INTRA else 15 if tf == "1D" else "D"
        rows, src = mtf.long_rows(sym, base, days)
        rows = list(rows or [])
        live = []
        try:
            live = self.e.charts.today_bars(sym) if self.e.metrics.get(sym) else []
        except Exception:
            live = []
        if live:
            d0 = _open_ts(_day(live[-1][0]))
            live = [r for r in live if d0 <= r[0] < d0 + 6.5 * 3600]
            if live:
                rows = [r for r in rows if r[0] < d0] + (daily_from(live) if base == "D" else resample(live, base) if base != 1 else
                                                         [list(r[:6]) for r in live])
                src = (src or "") + " + live"
        cut = self._now() - days * 86400
        return [r for r in rows if r[0] >= cut], src

    def compute(self, mtf, sym: str, tf: str) -> dict:
        c = cfg()
        days = int((c["history_days"] or {}).get(tf, 30)) if tf != "1m" else 3
        rows, src = self._rows(mtf, sym, tf, days)
        grps = groups(rows, tf)
        if len(grps) < 20:
            return {"sym": sym, "tf": tf, "enough": False, "note": f"Not enough {tf} history for {sym} yet ({len(grps)} candles).",
                    "candles": [], "source": src}
        now = self._now()
        cands = np.asarray([ohlc(s, r) for s, r in grps], float)
        last_start = grps[-1][0]
        f = fraction(tf, last_start, now)
        mkt_open = self._open_now(now)
        forming = bool(mkt_open and f < 1.0 and _day(last_start) <= _day(now) and
                       (tf not in INTRA or now < last_start + INTRA[tf] * 60))
        hist = cands[:-1] if forming else cands
        hgrps = grps[:-1] if forming else grps
        atr = atr_before(cands)
        atr_now = float(atr[-1]) if forming else float(np.nanmean(atr[-14:]))
        fb = min(0.9, max(0.1, round(f * 10) / 10)) if forming else None
        key = (sym, tf, fb, int(now // 1800))
        with self.lock:
            hit = self.cache.get(key)
        if hit is None:
            from . import registry
            rp = replay(hgrps, hist, atr[:len(hist)], fb, spec=registry.candle_spec()) if forming and tf != "1m" else None
            hit = {"replay": rp, "cone": cone(hist, atr[:len(hist)], int(c["horizon"])), "lean": lean(hist)}
            with self.lock:
                if len(self.cache) > 200:
                    self.cache.clear()
                self.cache[key] = hit
        out = {"sym": sym, "tf": tf, "enough": True, "source": src, "forming": forming, "fraction": round(f, 2) if forming else None,
               "atr": round(atr_now, 2), "last": float(cands[-1][4]), "history_candles": int(len(hist))}
        show = cands[-80:]
        out["candles"] = [{"time": int(r[0] + IST_SHIFT), "open": round(r[1], 2), "high": round(r[2], 2), "low": round(r[3], 2),
                           "close": round(r[4], 2)} for r in show]
        # the forming candle: where its close lands, how far high / low stretch, its shape so far and how often it held
        base = float(cands[-1][4])
        rp = hit.get("replay")
        if forming and rp and rp["n"] >= int(c["min_samples"]) and rp.get("rest_q"):
            q = rp["rest_q"]
            out["now"] = {"n": rp["n"], "fraction": out["fraction"], "close_68": [round(base + q[1] * atr_now, 2), round(base + q[3] * atr_now, 2)],
                          "close_95": [round(base + q[0] * atr_now, 2), round(base + q[4] * atr_now, 2)],
                          "close_mid": round(base + q[2] * atr_now, 2), "up_pct": round(rp["up_share"] * 100) if rp.get("up_share") is not None else None,
                          "high_to": round(float(cands[-1][2]) + (rp.get("up_ext_med") or 0) * atr_now, 2),
                          "low_to": round(float(cands[-1][3]) - (rp.get("dn_ext_med") or 0) * atr_now, 2),
                          "mins_left": round((last_start + INTRA[tf] * 60 - now) / 60, 1) if tf in INTRA else None}
            from . import candles as CDL, registry
            names = {"doji": "Doji", "inside_bar": "Inside bar", "nr7": "Narrowest of 7", **registry.names("candle")}
            seg = cands[-60:]
            pats, _ = CDL.last_bar_scan(seg[:, 1], seg[:, 2], seg[:, 3], seg[:, 4], [], {})
            out["now"]["shape"] = [{"pattern": k, "name": names.get(k, k), "d": v, **((rp.get("hold") or {}).get(k) or {"n": 0})}
                                   for k, v in pats.items()]
            self._remember(sym, tf, last_start, out["now"], f)
        elif forming:
            out["now"] = {"n": rp["n"] if rp else 0, "fraction": out["fraction"], "too_little": True}
        # the next candles: a range cone (combined with the forming candle's own range when one is forming)
        cn = hit.get("cone") or {}
        times = next_times(tf, last_start, int(c["horizon"]))
        rq = (rp or {}).get("rest_q") if forming and out.get("now", {}).get("close_68") else None
        pts = []
        for h, t in zip(range(1, int(c["horizon"]) + 1), times):
            qh = (cn.get(h) or {}).get("q")
            if not qh or (cn.get(h) or {}).get("n", 0) < int(c["min_samples"]):
                break
            comb = []
            for j in range(5):
                a, b = (rq[j] if rq else 0.0), qh[j]
                comb.append((math.copysign(math.sqrt(a * a + b * b), a + b)) if j != 2 else a + b)
            pts.append({"time": int(t + IST_SHIFT), **{k: round(base + v * atr_now, 2) for k, v in zip(("lo95", "lo68", "mid", "hi68", "hi95"), comb)}})
        if pts:
            pts.insert(0, {"time": int(last_start + IST_SHIFT), **{k: round(base, 2) for k in ("lo95", "lo68", "mid", "hi68", "hi95")}})
        out["cone"] = pts
        out["ghost"] = self._ghosts(c, cands, hist, atr, atr_now, forming, out.get("now") or {}, pts, (cn.get(1) or {}).get("q"))
        out["refresh_s"] = max(5, int(c.get("refresh_s") or 10))
        out["show_cone"] = bool(c.get("show_cone"))
        out["lean"] = hit.get("lean") or {}
        try:
            ev = self.e.events.risk(sym) if getattr(self.e, "events", None) is not None else None
            out["event"] = {"level": ev.get("level"), "line": ev.get("line")} if ev and ev.get("items") else None
        except Exception:
            out["event"] = None
        self.grade(sym, tf, grps, cands, now)
        out["check"] = self.grades.get(tf)
        early = getattr(getattr(self.e, "cdl", None), "early_stats", None)
        out["early"] = early.get(tf) if isinstance(early, dict) else None
        out["note"] = ("Ranges from this symbol's own history: every past candle cut at the same point and compared with how it "
                       "ended. A probability, not a promise, and never a signal. Buying at 70% of a candle is being paper-tested "
                       "on the test bench (Strategy board, version '" + tf + "@70%').")
        return out

    @staticmethod
    def _ghosts(c, cands, hist, atr, atr_now, forming, now_fc, pts, q1=None) -> list:
        """2.38: faded 'ghost' candles ahead of the real ones. The forming candle is finished at its likely close (high /
        low stretched by the typical extension). Each next candle is a range candle on the cone's middle path: its box is
        where that candle's close likely lands (68%, from one candle's moves in this history) and its wicks this
        timeframe's typical high / low beyond that (median, in ATRs). No direction is invented: when the history shows
        no edge the boxes sit around the middle. They redraw as the real candle forms."""
        if not c.get("ghost", True) or not pts:
            return []
        hh = hist[-400:]
        aa = atr[:len(hist)][-400:]
        ok = aa > 0
        if ok.sum() < 20:
            return []
        up_w = float(np.median((hh[ok, 2] - np.maximum(hh[ok, 1], hh[ok, 4])) / aa[ok]))
        dn_w = float(np.median((np.minimum(hh[ok, 1], hh[ok, 4]) - hh[ok, 3]) / aa[ok]))
        out = []
        prev = float(cands[-1][4])
        if forming and now_fc.get("close_mid") is not None:
            o, h, l = float(cands[-1][1]), float(cands[-1][2]), float(cands[-1][3])
            cl = float(now_fc["close_mid"])
            out.append({"time": pts[0]["time"], "open": round(o, 2), "close": round(cl, 2),
                        "high": round(max(h, now_fc.get("high_to") or h, o, cl), 2),
                        "low": round(min(l, now_fc.get("low_to") or l, o, cl), 2), "forming": True})
            prev = cl
        lo_q = (q1[1] - q1[2]) if q1 else -0.5            # one candle's 68% close range around its middle, in ATRs
        hi_q = (q1[3] - q1[2]) if q1 else 0.5
        for p in pts[1:int(c.get("ghost_candles") or len(pts))]:
            mid = float(p["mid"])
            lo_b, hi_b = mid + lo_q * atr_now, mid + hi_q * atr_now
            out.append({"time": p["time"], "open": round(lo_b, 2), "close": round(hi_b, 2), "mid": round(mid, 2),
                        "high": round(hi_b + up_w * atr_now, 2), "low": round(lo_b - dn_w * atr_now, 2), "range": True,
                        "from": round(prev, 2)})
            prev = mid
        return out

    def _now(self) -> float:
        """The scanner's clock while it runs (the simulator runs its own), else the wall clock."""
        clk = getattr(self.e, "clock", None)
        if clk is not None and getattr(self.e, "running", False):
            try:
                return float(clk.ts())
            except Exception:
                pass
        return time.time()

    def _open_now(self, now: float) -> bool:
        try:
            from .health import is_trading_day
            d = _day(now)
            return is_trading_day(d) and _open_ts(d) <= now <= dt.datetime.combine(d, CLOSE, IST).timestamp()
        except Exception:
            return False

    # ------------------------------------------------------------ live check of the ranges
    def _remember(self, sym, tf, start, now_fc, f):
        if f < float(cfg()["grade_from"]):
            return
        k = (sym, tf, start)
        with self.lock:
            if k not in self.pending:
                self.pending[k] = {"c68": now_fc["close_68"], "c95": now_fc["close_95"],
                                   "shape": {s["pattern"]: s["d"] for s in now_fc.get("shape") or []}, "f": f}

    def grade(self, sym, tf, grps, cands, now):
        """Forecasts whose candle has closed: did the close land in the 68% / 95% ranges, did the shape hold?"""
        from . import candles as CDL
        done = []
        with self.lock:
            items = [(k, v) for k, v in self.pending.items() if k[0] == sym and k[1] == tf]
        starts = {g[0]: i for i, g in enumerate(grps)}
        for k, v in items:
            i = starts.get(k[2])
            if i is None or i == len(grps) - 1 and (tf in INTRA and now < k[2] + INTRA[tf] * 60):
                continue
            if i == len(grps) - 1 and tf not in INTRA:
                continue
            close = float(cands[i][4])
            g = self.grades.setdefault(tf, {"n": 0, "in68": 0, "in95": 0, "shape_n": 0, "shape_held": 0})
            g["n"] += 1
            g["in68"] += int(v["c68"][0] <= close <= v["c68"][1])
            g["in95"] += int(v["c95"][0] <= close <= v["c95"][1])
            if v["shape"]:
                seg = cands[max(0, i - 59):i + 1]
                fin, _ = CDL.last_bar_scan(seg[:, 1], seg[:, 2], seg[:, 3], seg[:, 4], [], {})
                for p, d in v["shape"].items():
                    g["shape_n"] += 1
                    g["shape_held"] += int(fin.get(p) == d)
            g["in68_pct"] = round(g["in68"] / g["n"] * 100)
            g["in95_pct"] = round(g["in95"] / g["n"] * 100)
            g["shape_pct"] = round(g["shape_held"] / g["shape_n"] * 100) if g["shape_n"] else None
            done.append(k)
        if done:
            with self.lock:
                for k in done:
                    self.pending.pop(k, None)
            try:
                self.e.store.put("fortune:grades", self.grades)
            except Exception:
                pass
