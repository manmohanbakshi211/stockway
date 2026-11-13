"""Display-only detectors on the live day (ADR-0012). The live loop hands over each closed minute's bars (every
instrument it watches) and the last quotes; the detectors keep their own small state per underlying and per option
and write alerts to the alert book. They never touch the engine, the accounts or the broker.

Gun rule: each detector runs in isolation; one that raises is switched off for the rest of the day and logged, and
the others carry on. Thresholds are the bullet detectors.json."""
from __future__ import annotations

import json
import math
import statistics
from collections import deque
from pathlib import Path

from ..core import log
from .book import SPEC, AlertBook

_log = log.get("detect")
YEAR_MIN = 252 * 375


def _f(x, d=2):
    return "–" if x is None else f"{x:,.{d}f}"


class Under:
    """One underlying's day so far (minute bars, opening range, VWAP, 5-minute candles, its option chain)."""

    def __init__(self, sym: str, is_index: bool):
        self.sym, self.is_index = sym, is_index
        self.t: list[int] = []
        self.o: list[float] = []
        self.h: list[float] = []
        self.l: list[float] = []
        self.c: list[float] = []
        self.v: list[float] = []
        self.pv = self.vv = 0.0
        self.vwap: list[float | None] = []
        self.c5: list[tuple] = []                       # closed 5-minute candles (t, o, h, l, c, vol)
        self.iv: deque = deque(maxlen=60)               # (t, ATM IV)
        self.oi: deque = deque(maxlen=60)               # (t, call OI, put OI, spot)
        self.pcr_open: float | None = None
        self.walls: dict[str, float] = {}
        self.fired: set = set()                         # once-a-day events (pattern pairs)

    def add(self, t, o, h, l, c, v):
        self.t.append(t)
        self.o.append(o)
        self.h.append(h)
        self.l.append(l)
        self.c.append(c)
        self.v.append(v or 0.0)
        if v:
            self.pv += c * v
            self.vv += v
        self.vwap.append(self.pv / self.vv if self.vv else None)

    def pace(self) -> float | None:
        """Last 5 minutes' volume vs the median 5-minute volume so far today (after the first 15 minutes)."""
        if self.is_index or len(self.v) < 30:
            return None
        blocks = [sum(self.v[i:i + 5]) for i in range(15, len(self.v) - 5, 5)]
        med = statistics.median(blocks) if blocks else 0
        return round(sum(self.v[-5:]) / med, 2) if med else None


class Detectors:
    def __init__(self, universe, day_t0: int, book: AlertBook, spec: dict | None = None,
                 prev_close: dict[str, float] | None = None):
        self.u, self.t0, self.book = universe, int(day_t0), book
        self.spec = spec or json.loads(SPEC.read_text())
        self.cfg = self.spec["detectors"]
        self.prev_close = prev_close or {}
        self.und: dict[str, Under] = {}
        self.opt: dict[str, deque] = {}                 # option key -> deque of (t, c, vol, oi)
        self.ivs: dict[str, float] = {}                 # option key -> last IV
        self.failed: dict[str, str] = {}
        self.counts = {"minutes": 0, "alerts": 0}
        self.rules = {"sharp_move": self._sharp, "orb": self._orb, "day_extreme": self._extreme, "vwap_cross": self._vwap,
                      "volume_surge": self._surge, "unusual_options": self._unusual, "oi_buildup": self._oi_buildup,
                      "oi_wall": self._walls, "pcr_shift": self._pcr, "iv_spike": self._iv, "candles": self._candles,
                      "patterns": self._patterns}

    # -------------------------------------------------------------- input
    def on_minute(self, t: int, rows: list[dict], last: dict[str, dict] | None = None) -> int:
        """t = the closed minute's start (epoch s); rows = that minute's bars {key, t, o, h, l, c, vol, oi}."""
        last = last or {}
        m = (t - self.t0) // 60
        got = set()
        for r in rows:
            if r.get("t") != t:
                continue
            k = r["key"]
            if k in self.u.meta:
                self.opt.setdefault(k, deque(maxlen=120)).append((t, r["c"], r.get("vol") or 0.0, r.get("oi")))
                iv = (last.get(k) or {}).get("iv")
                if iv:
                    self.ivs[k] = float(iv)
            elif k in self.u.by_key:
                s = self.u.by_key[k]
                un = self.und.get(s)
                if un is None:
                    un = self.und[s] = Under(s, bool(self.u.under.get(s, {}).get("is_index")))
                un.add(t, r["o"], r["h"], r["l"], r["c"], r.get("vol"))
                got.add(s)
        for k, q in last.items():                        # IV also arrives with quotes that did not trade this minute
            if k in self.u.meta:
                if q.get("iv"):
                    self.ivs[k] = float(q["iv"])
            elif k in self.u.by_key and q.get("cp") and self.u.by_key[k] not in self.prev_close:
                self.prev_close[self.u.by_key[k]] = float(q["cp"])   # the broker's previous close
        n = 0
        five = (m + 1) % 5 == 0
        for s in got:
            un = self.und[s]
            if five and len(un.t) >= 5:
                w = slice(-5, None)
                un.c5.append((un.t[-5], un.o[-5], max(un.h[w]), min(un.l[w]), un.c[-1], sum(un.v[w])))
            self._chain(un, t, m)
            for name, fn in self.rules.items():
                c = self.cfg.get(name, {})
                if not c.get("on", True) or name in self.failed or m < c.get("after_min", 0):
                    continue
                try:
                    for a in fn(un, c, m, t, five) or []:
                        a.setdefault("price", un.c[-1])
                        if self.book.add({"sym": s, **a}, t + 60):
                            n += 1
                except Exception as e:
                    self.failed[name] = f"{type(e).__name__}: {e}"[:200]
                    _log.warning("detector_failed", detector=name, error=self.failed[name])
        self.counts["minutes"] += 1
        self.counts["alerts"] += n
        return n

    def _chain(self, un: Under, t: int, m: int) -> None:
        """ATM IV, call / put OI and the biggest-OI strikes from the watched strikes of this underlying."""
        ch = self.u.chain.get(un.sym)
        if not ch:
            return
        spot = un.c[-1]
        call_oi = put_oi = 0.0
        by = {"CE": {}, "PE": {}}
        for (k, side), key in ch["keys"].items():
            q = self.opt.get(key)
            if q and q[-1][3]:
                by[side][k] = q[-1][3]
        call_oi, put_oi = sum(by["CE"].values()), sum(by["PE"].values())
        if call_oi and put_oi:
            un.oi.append((t, call_oi, put_oi, spot))
            if un.pcr_open is None and m >= 5:
                un.pcr_open = put_oi / call_oi
        near = sorted(ch["strikes"], key=lambda k: abs(k - spot))[:1]
        ivs = [self.ivs[ch["keys"][(k, s)]] for k in near for s in ("CE", "PE") if ch["keys"].get((k, s)) in self.ivs]
        if ivs:
            un.iv.append((t, sum(ivs) / len(ivs)))
        for side, name in (("CE", "resistance"), ("PE", "support")):
            if len(by[side]) >= 3:
                top = sorted(by[side].items(), key=lambda x: -x[1])
                un.walls[name] = top[0][0]
                un.walls[name + "_lead"] = top[0][1] / top[1][1] if top[1][1] else 0
                un.walls.setdefault(name + "_open", top[0][0])

    # -------------------------------------------------------------- detectors
    def _kind(self, un: Under, c, key):
        v = c[key]
        return v["index" if un.is_index else "stock"] if isinstance(v, dict) else v

    def _sharp(self, un, c, m, t, five):
        if len(un.c) < 6:
            return []
        r5 = (un.c[-1] / un.c[-6] - 1) * 100
        iv = un.iv[-1][1] if un.iv else None
        if iv:
            sig = iv / 100 * math.sqrt(5 / YEAR_MIN) * 100
        elif len(un.c) >= 21:
            rets = [(b / a - 1) * 100 for a, b in zip(un.c[-61:-1], un.c[-60:])]
            sig = statistics.pstdev(rets) * math.sqrt(5)
        else:
            return []
        if not sig or abs(r5) < self._kind(un, c, "min_pct"):
            return []
        z = r5 / sig
        if abs(z) < c["z"]:
            return []
        d = "up" if r5 > 0 else "down"
        return [{"kind": "sharp_move", "direction": d, "severity": 3 if abs(z) >= c["z_top"] else 2,
                 "title": f"{un.sym} {'spiking' if d == 'up' else 'dropping'} {r5:+.2f}% in 5 min",
                 "detail": f"{abs(z):.1f}x the usual 5-minute move{' (option-implied)' if iv else ''}. Now {_f(un.c[-1])}.",
                 "data": {"z": round(z, 2), "ret_5m": round(r5, 3)}}]

    def _orb(self, un, c, m, t, five):
        if len(un.c) < 16:
            return []
        orh, orl = max(un.h[:15]), min(un.l[:15])
        mg = self._kind(un, c, "margin_pct") / 100
        pace = un.pace()
        if not un.is_index and (pace or 0) < c["min_pace"]:
            return []
        p, now = un.c[-2], un.c[-1]
        out = []
        if p <= orh * (1 + mg) < now:
            out.append({"kind": "orb", "direction": "up", "severity": 2, "title": f"{un.sym} broke above the opening range {_f(orh)}",
                        "detail": "First 15 minutes' high taken out" + (f", volume pace {pace}x." if pace else "."),
                        "data": {"level": orh}})
        if p >= orl * (1 - mg) > now:
            out.append({"kind": "orb", "direction": "down", "severity": 2, "title": f"{un.sym} broke below the opening range {_f(orl)}",
                        "detail": "First 15 minutes' low taken out" + (f", volume pace {pace}x." if pace else "."),
                        "data": {"level": orl}})
        return out

    def _extreme(self, un, c, m, t, five):
        if len(un.c) < 31 or (not un.is_index and (un.pace() or 0) < c["min_pace"]):
            return []
        ph, pl = max(un.h[:-1]), min(un.l[:-1])
        pc = self.prev_close.get(un.sym)
        chg = f" {(un.c[-1] / pc - 1) * 100:+.2f}% on the day." if pc else ""
        if un.c[-1] > ph:
            return [{"kind": "day_extreme", "direction": "up", "severity": 2 if un.is_index else 1,
                     "title": f"{un.sym} at a new day high {_f(un.c[-1])}", "detail": "Above every earlier minute of the day." + chg}]
        if un.c[-1] < pl:
            return [{"kind": "day_extreme", "direction": "down", "severity": 2 if un.is_index else 1,
                     "title": f"{un.sym} at a new day low {_f(un.c[-1])}", "detail": "Below every earlier minute of the day." + chg}]
        return []

    def _vwap(self, un, c, m, t, five):
        if un.is_index or len(un.vwap) < 2 or None in un.vwap[-2:] or (un.pace() or 0) < c["min_pace"]:
            return []
        p, now, pv, vw = un.c[-2], un.c[-1], un.vwap[-2], un.vwap[-1]
        if p <= pv and now > vw:
            return [{"kind": "vwap_cross", "direction": "up", "severity": 1, "title": f"{un.sym} crossed above VWAP {_f(vw)}",
                     "detail": f"Buyers took back the day's average price, volume pace {un.pace()}x."}]
        if p >= pv and now < vw:
            return [{"kind": "vwap_cross", "direction": "down", "severity": 1, "title": f"{un.sym} slipped below VWAP {_f(vw)}",
                     "detail": f"Sellers pushed below the day's average price, volume pace {un.pace()}x."}]
        return []

    def _surge(self, un, c, m, t, five):
        pace, pc = un.pace(), self.prev_close.get(un.sym)
        if not pace or not pc or pace < c["pace"]:
            return []
        ch = (un.c[-1] / pc - 1) * 100
        if abs(ch) < c["move_pct"]:
            return []
        d = "up" if ch > 0 else "down"
        return [{"kind": "volume_surge", "direction": d, "severity": 3 if pace >= 2 * c["pace"] else 2,
                 "title": f"{un.sym} on {pace:.1f}x its volume pace, {ch:+.2f}% on the day",
                 "detail": "Heavy participation behind the move.", "data": {"pace": pace, "chg_pct": round(ch, 2)}}]

    def _unusual(self, un, c, m, t, five):
        ch = self.u.chain.get(un.sym)
        if not ch:
            return []
        lot = (self.u.under.get(un.sym) or {}).get("lot") or 1
        out = []
        for (k, side), key in ch["keys"].items():
            q = self.opt.get(key)
            if not q or len(q) < 15 or q[-1][0] != t:
                continue
            vals = [x[1] * x[2] for x in q]
            v5 = sum(vals[-5:])
            pace = sum(vals[:-5]) / max(len(vals) - 5, 1) * 5
            if v5 < self._kind(un, c, "min_value_rs") or not pace or v5 / pace < c["x_pace"]:
                continue
            p0, p1 = q[-6][1] if len(q) > 5 else q[0][1], q[-1][1]
            oi0, oi1 = (q[-6][3] if len(q) > 5 else q[0][3]), q[-1][3]
            prem = (p1 / p0 - 1) * 100 if p0 else 0.0
            oi = (oi1 / oi0 - 1) * 100 if oi0 and oi1 else None
            buying = prem >= 0
            what = "buying" if buying else "writing"
            d = ("up" if side == "CE" else "down") if buying else ("down" if side == "CE" else "up")
            xp = v5 / pace
            lots = sum(row[2] for row in list(q)[-5:]) / lot
            top = v5 >= self._kind(un, c, "top_value_rs") and xp >= c["x_pace_top"]
            out.append({"kind": "unusual_options", "direction": d, "severity": 3 if top else 2,
                        "title": f"Heavy {what} in {un.sym} {k:g} {side}",
                        "detail": f"{lots:,.0f} lots (₹{v5 / 1e7:,.2f} Cr) in 5 min, {xp:.1f}x its pace. "
                                  f"Premium {prem:+.1f}%" + (f", OI {oi:+.1f}%." if oi is not None else "."),
                        "data": {"strike": k, "side": side, "value_5m": round(v5), "x_pace": round(xp, 1)}})
        return out[:3]

    def _oi_buildup(self, un, c, m, t, five):
        w = c["window_min"]
        if len(un.oi) <= w:
            return []
        (t0, ce0, pe0, s0), (t1, ce1, pe1, s1) = un.oi[-w - 1], un.oi[-1]
        px = (s1 / s0 - 1) * 100
        dce, dpe = (ce1 / ce0 - 1) * 100, (pe1 / pe0 - 1) * 100
        if abs(px) < self._kind(un, c, "px_pct"):
            return []
        if px > 0 and dpe >= c["oi_pct"] and dpe > dce:
            lab, d = "put writing on a rise (bullish build-up)", "up"
        elif px < 0 and dce >= c["oi_pct"] and dce > dpe:
            lab, d = "call writing on a fall (bearish build-up)", "down"
        elif px > 0 and dce <= -c["oi_pct"]:
            lab, d = "call writers covering on a rise (short covering)", "up"
        elif px < 0 and dpe <= -c["oi_pct"]:
            lab, d = "put writers leaving on a fall (long unwinding of support)", "down"
        else:
            return []
        return [{"kind": "oi_buildup", "direction": d, "severity": 2, "title": f"{un.sym}: {lab}",
                 "detail": f"Price {px:+.2f}%, call OI {dce:+.1f}%, put OI {dpe:+.1f}% in {w} min (watched strikes).",
                 "data": {"px_pct": round(px, 2), "call_oi_pct": round(dce, 1), "put_oi_pct": round(dpe, 1)}}]

    def _walls(self, un, c, m, t, five):
        if not un.is_index:
            return []
        out = []
        for side, label, opened in (("resistance", "Resistance (biggest call OI)", "resistance_open"),
                                    ("support", "Support (biggest put OI)", "support_open")):
            a, b = un.walls.get(side), un.walls.get(side + "_seen", un.walls.get(opened))
            if a is None or b is None or a == b or un.walls.get(side + "_lead", 0) < c["lead"]:
                continue
            un.walls[side + "_seen"] = a
            moved = "up" if a > b else "down"
            out.append({"kind": "oi_wall", "direction": moved, "severity": 2,
                        "title": f"{un.sym} {side} moved {moved} to {a:g}", "detail": f"{label} shifted from {b:g} to {a:g}.",
                        "data": {"side": side, "old": b, "new": a}})
        return out

    def _pcr(self, un, c, m, t, five):
        if not un.is_index or un.pcr_open is None or len(un.oi) < 2:
            return []
        now = un.oi[-1][2] / un.oi[-1][1]
        before = un.oi[-2][2] / un.oi[-2][1]
        dl, pdl = now - un.pcr_open, before - un.pcr_open
        if abs(dl) >= c["shift"] > abs(pdl):
            d = "up" if dl > 0 else "down"
            return [{"kind": "pcr_shift", "direction": d, "severity": 2, "title": f"{un.sym} PCR {d} to {now:.2f}",
                     "detail": f"From {un.pcr_open:.2f} early in the day: "
                               + ("put writers adding (supportive)." if dl > 0 else "call writers adding (capping)."),
                     "data": {"pcr": round(now, 3), "pcr_open": round(un.pcr_open, 3)}}]
        return []

    def _iv(self, un, c, m, t, five):
        w = c["window_min"]
        if len(un.iv) <= w:
            return []
        chg, before = un.iv[-1][1] - un.iv[-w - 1][1], un.iv[-2][1] - un.iv[max(-w - 2, -len(un.iv))][1]
        if chg >= c["pts"] > before:
            return [{"kind": "iv_spike", "direction": "neutral", "severity": 2,
                     "title": f"{un.sym} option IV jumped +{chg:.1f} pts in {w} min",
                     "detail": f"At-the-money IV now {un.iv[-1][1]:.1f}%. Options got expensive: a move is expected, or fear is rising.",
                     "data": {"iv": round(un.iv[-1][1], 2), "chg": round(chg, 2)}}]
        return []

    def _candles(self, un, c, m, t, five):
        if not five or len(un.c5) < 4:
            return []
        if not un.is_index and (un.pace() or 0) < c["stocks_min_pace"]:
            return []
        (_, o1, h1, l1, c1, _), (_, o2, h2, l2, c2, _) = un.c5[-2], un.c5[-1]
        avg = statistics.mean(abs(x[4] - x[1]) for x in un.c5[-11:-1]) or 1e-9
        body2, rng2 = abs(c2 - o2), (h2 - l2) or 1e-9
        out = []
        sev = 2 if un.is_index else 1
        if body2 >= c["min_body_vs_avg"] * avg:
            if c1 < o1 and c2 > o2 and c2 >= o1 and o2 <= c1:
                out.append(("up", "bullish engulfing", "A green 5-minute candle swallowed the red one before it."))
            if c1 > o1 and c2 < o2 and c2 <= o1 and o2 >= c1:
                out.append(("down", "bearish engulfing", "A red 5-minute candle swallowed the green one before it."))
        low_wick, up_wick = min(o2, c2) - l2, h2 - max(o2, c2)
        day_l, day_h = min(un.l), max(un.h)
        if low_wick >= 2 * max(body2, rng2 * 0.1) and up_wick <= body2 and l2 <= day_l * 1.0005:
            out.append(("up", "hammer at the day low", "Sellers pushed down and buyers took it back within 5 minutes."))
        if up_wick >= 2 * max(body2, rng2 * 0.1) and low_wick <= body2 and h2 >= day_h * 0.9995:
            out.append(("down", "shooting star at the day high", "Buyers pushed up and sellers took it back within 5 minutes."))
        return [{"kind": "candles", "direction": d, "severity": sev, "title": f"{un.sym} 5-min {name}", "detail": why}
                for d, name, why in out]

    def _patterns(self, un, c, m, t, five):
        """Double top / bottom on 5-minute candles: two swing highs (lows) within tol% at least min_gap candles apart,
        then a close through the low (high) between them."""
        if not five or len(un.c5) < 10:
            return []
        hs, ls_ = [x[2] for x in un.c5], [x[3] for x in un.c5]
        tol = self._kind(un, c, "tol_pct") / 100
        last = un.c5[-1][4]
        out = []
        for kind, vals, better in (("top", hs, lambda a, b: a > b), ("bottom", ls_, lambda a, b: a < b)):
            sw = [i for i in range(2, len(vals) - 2)
                  if all(better(vals[i], vals[j]) or vals[i] == vals[j] for j in (i - 2, i - 1, i + 1, i + 2))]
            if len(sw) < 2:
                continue
            a, b = sw[-2], sw[-1]
            if b - a < c["min_gap_candles"] or abs(vals[a] - vals[b]) > tol * vals[a] or (kind, a, b) in un.fired:
                continue
            between = ls_[a:b + 1] if kind == "top" else hs[a:b + 1]
            neck = min(between) if kind == "top" else max(between)
            if (kind == "top" and last < neck) or (kind == "bottom" and last > neck):
                un.fired.add((kind, a, b))
                d = "down" if kind == "top" else "up"
                out.append({"kind": "patterns", "direction": d, "severity": 2,
                            "title": f"{un.sym} double {kind} confirmed below {_f(neck)}" if kind == "top"
                            else f"{un.sym} double {kind} confirmed above {_f(neck)}",
                            "detail": f"Two 5-minute {'highs' if kind == 'top' else 'lows'} near {_f(vals[a])} and {_f(vals[b])}, "
                                      f"then a close through {_f(neck)}.", "data": {"neck": neck}})
        return out
