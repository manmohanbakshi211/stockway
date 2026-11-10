"""2.54 smart chart lines: the line finder. Draws the lines that matter by itself and says what each one usually does.

Mandeep, 11 Nov 2026: "make some smart system that can draw lines on the chart by itself analysing each and everything
that matters". The test behind it (lab/levelbook.py, 4.8 years of Nifty + Bank Nifty 1-minute): a plain line holds
about as often as a random line; how price comes to it (fast / slow, with or against the day's move) decides. So every
line here carries its record in the situation it is in now, against random lines in the same situation.

Gun: the lines of the day (previous days, today's opening range and swings, OI walls, max pain, gamma flip), diagonal
trendlines from 5-minute swings, merging near lines into one zone, the situation now (speed toward the line, the day's
move), the evidence from the line book, a short label and what it means for a buyer. Bullets: rules/lines.json.
Display and shadow grading only: no alert, paper trade or Lab result changes. Market data only.
"""
from __future__ import annotations

import datetime as dt

import numpy as np

from lab import levelbook as LB

IST_SHIFT = 19800
COLORS = {"likely holds": "#2F6FDE", "likely breaks": "#E07B00", "coin flip": "#8A95A1", "not tested yet": "#B04ACF"}
STYLE = {"likely holds": 0, "likely breaks": 2, "coin flip": 1, "not tested yet": 3}     # solid / dashed / dotted / sparse
SHORT = {"pdh": "PDH", "pdl": "PDL", "pdc": "PDC", "pivot": "Pivot", "tc": "CPR top", "bc": "CPR bottom", "r1": "R1",
         "s1": "S1", "r2": "R2", "s2": "S2", "wk_h": "5-day high", "wk_l": "5-day low", "d20_h": "20-day high",
         "d20_l": "20-day low", "open": "Open", "round_s": "Round", "round_b": "Big round", "or_h": "OR high",
         "or_l": "OR low", "sw_h": "Swing high", "sw_l": "Swing low", "oi_call": "Call OI wall", "oi_put": "Put OI wall",
         "max_pain": "Max pain", "gamma_flip": "Gamma flip", "tl_up": "Trendline ↗", "tl_dn": "Trendline ↘"}


def ist_day(ts: float) -> dt.date:
    return dt.datetime.fromtimestamp(ts + IST_SHIFT, dt.timezone.utc).date()


def resample5(rows: list):
    """1-minute rows [ts, o, h, l, c, v] (any number of sessions) -> 5-minute arrays (t, h, l, c), per session."""
    if not rows:
        return (np.array([]),) * 4
    a = np.asarray(sorted(rows, key=lambda r: r[0]), float)
    k = ((a[:, 0] + IST_SHIFT - 555 * 60) // 300).astype(np.int64)        # 5-minute slot counted from 09:15 IST
    t, h, l, c = [], [], [], []
    start = 0
    for i in range(1, len(a) + 1):
        if i == len(a) or k[i] != k[start]:
            seg = a[start:i]
            t.append(seg[0, 0])
            h.append(seg[:, 2].max())
            l.append(seg[:, 3].min())
            c.append(seg[-1, 4])
            start = i
    return np.array(t), np.array(h), np.array(l), np.array(c)


def _atr(h, l, c, n=14) -> float:
    if len(c) < 2:
        return float("nan")
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    return float(np.mean(tr[-n:]))


def trendlines(rows: list, spot: float) -> list:
    """Diagonal lines through 5-minute swing points of the last few sessions: a falling line through two lower swing
    highs (resistance) and a rising line through two higher swing lows (support), never broken by a 5-minute close
    since the first point. Each: {type, t1, p1, t2, p2, price_now, touches, slope_per_hour}."""
    c = LB.cfg().get("trendline") or {}
    k, min_t, tol = int(c.get("swing_k", 2)), int(c.get("min_touches", 2)), float(c.get("touch_atr", 0.35))
    t, h, l, cl = resample5(rows)
    n = len(cl)
    if n < 4 * k + 6 or not spot:
        return []
    atr = _atr(h, l, cl)
    if not np.isfinite(atr) or atr <= 0:
        return []
    hs = [i for i in range(k, n - k) if h[i] == h[i - k:i + k + 1].max()]
    ls = [i for i in range(k, n - k) if l[i] == l[i - k:i + k + 1].min()]
    out = []
    for typ, pts, arr, sgn in (("tl_dn", hs, h, -1), ("tl_up", ls, l, 1)):
        best = None
        for j in range(len(pts) - 1, 0, -1):                 # newest anchor first
            i2 = pts[j]
            for i1 in reversed(pts[:j]):
                if (arr[i2] - arr[i1]) * sgn <= 0:           # falling line needs a lower high, rising a higher low
                    continue
                if i2 - i1 < 3:
                    continue
                slope = (arr[i2] - arr[i1]) / (i2 - i1)
                line = arr[i1] + slope * (np.arange(n) - i1)
                seg = slice(i1, n)
                # broken: a 5-minute close beyond the line (above a falling one, below a rising one)
                if np.any((cl[seg] - line[seg]) * -sgn > 0.15 * atr):
                    continue
                touches = sum(1 for i in pts if i >= i1 and abs(arr[i] - line[i]) <= tol * atr)
                now = float(line[-1])
                if touches >= min_t and abs(now / spot - 1) * 100 <= float(LB.cfg().get("near_pct") or 1.6):
                    cand = (touches, i2, i1, slope, now)
                    if best is None or cand[:2] > best[:2]:
                        best = cand
            if best is not None and best[1] == i2:
                break
        if best:
            touches, i2, i1, slope, now = best
            out.append({"type": typ, "t1": int(t[i1] + IST_SHIFT), "p1": round(float(arr[i1]), 2),
                        "t2": int(t[-1] + IST_SHIFT), "p2": round(now, 2), "price_now": round(now, 2), "touches": int(touches),
                        "slope_per_hour": round(float(slope) * 12, 2)})
    return out[: int(c.get("max_lines", 2)) * 2]


def compute(sym: str, today: list, prev_days: list, spot: float | None, extra: dict | None = None,
            multi_rows: list | None = None, book: dict | None = None, is_index: bool = True) -> dict:
    """The smart lines for one symbol now.
    today: today's 1-minute rows [ts, o, h, l, c, v]; prev_days: finished days [(h, l, c)], oldest first;
    extra: live-only lines {'oi_call', 'oi_put', 'max_pain', 'gamma_flip'}; multi_rows: 1-minute rows of the last few
    sessions (for trendlines). Returns {lines, trendlines, situation, book}."""
    c = LB.cfg()
    p = LB.params(sym, is_index)
    rows = sorted(today or [], key=lambda r: r[0])
    if not rows or not spot:
        return {"lines": [], "trendlines": [], "note": "no bars for today yet"}
    o1, h1, l1, c1 = (np.array([r[k] for r in rows], float) for k in (1, 2, 3, 4))
    day_open = float(o1[0])
    first = int(c.get("first_minute") or 15)
    cand = [(t, x, first) for t, x in LB.day_lines(sym, day_open, prev_days)]
    orr = LB.opening_range(h1, l1, 15)
    if orr:
        cand += [("or_h", orr[0], 15), ("or_l", orr[1], 15)]
    sw = LB.swings5(h1, l1)
    last_sw = {}
    for t, x, m in sw:                                       # the latest two swing highs / lows of the day
        last_sw.setdefault(t, []).append((t, x, m))
    for t in ("sw_h", "sw_l"):
        cand += [(a, b, max(first, m)) for a, b, m in last_sw.get(t, [])[-2:]]
    for t, x in (extra or {}).items():
        if x and np.isfinite(float(x)):
            cand.append((t, float(x), first))
    near = float(c.get("near_pct") or 1.6)
    cand = [(t, x, m) for t, x, m in cand if abs(x / spot - 1) * 100 <= near]
    book = book if book is not None else LB.load()
    lb, thr, win = int(c.get("speed_lookback_min") or 15), float(c.get("day_move_pct") or 0.3), int(c.get("window_min") or 60)
    tnow = len(c1) - 1
    out = []
    for typ, L, start in cand:
        side = 1 if L < spot else -1
        speed_r = LB.speed_toward(c1, tnow, side, lb) / p["move_pct"] if tnow >= 1 else 0.0
        day = LB.day_side(spot, day_open, side, thr)
        ev = LB.evidence(book, typ, speed_r, day)
        touched = LB.first_touch(L, start, h1, l1, c1, p, win) if typ not in ("oi_call", "oi_put", "max_pain", "gamma_flip") else None
        out.append({"type": typ, "name": (LB.cfg().get("types") or {}).get(typ, typ), "short": SHORT.get(typ, typ),
                    "price": round(L, 2), "side": "support" if side > 0 else "resistance",
                    "dist_pct": round((L / spot - 1) * 100, 2), "speed_r": round(speed_r, 2), "day": day,
                    "ev": ev, "touched": ({"minute": int(touched[0]), "time": _hhmm(rows[touched[0]][0]), "side": int(touched[1]),
                                           "result": touched[2]}
                                          if touched else None)})
    lines = _merge(out, spot, float(c.get("merge_pct") or 0.04))
    keep = int(c.get("max_lines_each_side") or 4)
    chosen = []
    for side in ("support", "resistance"):           # the nearest lines, plus up to 2 farther ones that beat random lines
        xs = sorted((x for x in lines if x["side"] == side), key=lambda x: abs(x["price"] - spot))
        near_, rest = xs[:max(1, keep - 1)], xs[max(1, keep - 1):]
        chosen += near_ + [x for x in rest if x["matters"]][:2] + ([] if any(x["matters"] for x in rest) else rest[:1])
    chosen = sorted(chosen, key=lambda x: -x["price"])
    tls = []
    for tl in trendlines(multi_rows or rows, spot):
        side = 1 if tl["price_now"] < spot else -1
        speed_r = LB.speed_toward(c1, tnow, side, lb) / p["move_pct"] if tnow >= 1 else 0.0
        ev = LB.evidence(book, tl["type"], speed_r, LB.day_side(spot, day_open, side, thr))
        tl.update(name=(LB.cfg().get("types") or {}).get(tl["type"], tl["type"]), short=SHORT[tl["type"]], ev=ev,
                  side="support" if side > 0 else "resistance", verdict=(ev or {}).get("verdict") or "not tested yet")
        tl["label"] = f"{tl['short']} ({tl['touches']} touches) · {_verdict_short(tl['verdict'], ev)}"
        tl["color"] = COLORS.get(tl["verdict"], "#B04ACF")
        tls.append(tl)
    sp = LB.speed_toward(c1, tnow, 1, lb) / p["move_pct"] if tnow >= 1 else 0.0
    return {"lines": chosen, "trendlines": tls, "spot": spot,
            "situation": {"day_move_pct": round((spot / day_open - 1) * 100, 2), "falling_r": round(sp, 2),
                          "speed_words": _speed_words(abs(sp), (book or {}).get("speed_cuts") or [0.39, 0.7])},
            "book": {k: (book or {}).get(k) for k in ("_file", "made", "syms", "touches")}}


def _hhmm(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts + IST_SHIFT, dt.timezone.utc).strftime("%H:%M")


def _speed_words(r: float, cuts) -> str:
    return "slowly" if r <= cuts[0] else "fast" if r >= cuts[1] else "at a normal pace"


def _verdict_short(v: str, ev: dict | None) -> str:
    if not ev or v == "not tested yet":
        return "new: graded live"
    h = ev.get("hold")
    edge = ev.get("edge") or 0
    star = ""
    if ev.get("matters"):                                  # the line itself beats random lines the way the verdict points
        if v != "coin flip":
            agrees = (v == "likely holds" and edge > 0) or (v == "likely breaks" and edge < 0)
            star = " ★" if agrees else " (weaker than a random line)"
    return (f"holds {h:.0f}%" if v == "likely holds" else f"breaks {100 - h:.0f}%" if v == "likely breaks" else "50/50") + star


def _merge(lines: list, spot: float, merge_pct: float) -> list:
    """Lines within merge_pct % of each other become one zone: the best-evidenced line speaks for it, all names kept."""
    lines = sorted(lines, key=lambda x: x["price"])
    groups, cur = [], []
    for x in lines:
        if cur and (x["price"] / cur[-1]["price"] - 1) * 100 > merge_pct:
            groups.append(cur)
            cur = []
        cur.append(x)
    if cur:
        groups.append(cur)
    out = []
    for g in groups:
        def rank(x):
            ev = x.get("ev") or {}
            return (bool(ev.get("matters")), ev.get("n") or 0)
        lead = max(g, key=rank)
        ev = lead.get("ev") or {}
        v = ev.get("verdict") or "not tested yet"
        names = list(dict.fromkeys(x["short"] for x in g))
        z = {**lead, "names": names, "count": len(g), "low": min(x["price"] for x in g), "high": max(x["price"] for x in g),
             "verdict": v, "matters": bool(ev.get("matters")), "color": COLORS.get(v, "#8A95A1"), "style": STYLE.get(v, 1),
             "width": 2 if ev.get("matters") or len(g) >= 3 else 1}
        z["label"] = f"{' + '.join(names[:3])}{' +' + str(len(names) - 3) if len(names) > 3 else ''} · {_verdict_short(v, ev)}"
        z["action"] = _action(z)
        out.append(z)
    return out


def _action(z: dict) -> str:
    """What the line means for a buyer (words only; nothing is traded from this)."""
    v, sup = z["verdict"], z["side"] == "support"
    t = z.get("touched")
    done = ""
    if t:
        was = "support" if t.get("side", 0) > 0 else "resistance"
        if t.get("result") == "hold":
            done = f"Held as {was} at {t['time']}. "
        elif t.get("result") == "break":
            done = f"Broken at {t['time']}" + (f": old {was}, now {z['side']} (flip). " if was != z["side"] else ". ")
        else:
            done = f"Touched at {t['time']}, still deciding. "
    if v == "likely breaks":
        return done + ("If a 5-min candle closes below it: puts in play, stop back above it." if sup else
                       "If a 5-min candle closes above it: calls in play, stop back below it.")
    if v == "likely holds":
        return done + ("Bounce zone: calls only after a rejection candle here, stop just below." if sup else
                       "Rejection zone: puts only after a rejection candle here, stop just above.")
    if v == "coin flip":
        return done + "No edge from the line itself: wait for a 5-min close beyond it or a clear rejection."
    return done + "New kind of line: no history yet, graded live from today."


def lines_from_engine(e, sym: str, m: dict, today: list, multi_rows: list | None = None) -> dict:
    """The engine's view of a symbol -> compute(). Previous finished days from the daily candles, live-only lines from
    the option chain (OI walls, max pain) and gamma (flip)."""
    day = ist_day(today[-1][0]) if today else None
    d = (e.daily or {}).get(sym) or {}
    prev = [(float(x[2]), float(x[3]), float(x[4])) for x in (d.get("d_ohlc") or [])
            if day is None or str(x[0])[:10] < day.isoformat()]
    if not prev and multi_rows:                      # no daily candles (closed market / simulator): from the bars we have
        by = {}
        for r in multi_rows:
            dd = ist_day(r[0])
            if day is None or dd < day:
                x = by.setdefault(dd, [r[2], r[3], r[4]])
                x[0], x[1], x[2] = max(x[0], r[2]), min(x[1], r[3]), r[4]
        prev = [tuple(by[k]) for k in sorted(by)]
    o = (m or {}).get("opt") or {}
    g = (getattr(e, "gex", {}) or {}).get(sym) or {}
    extra = {"oi_call": o.get("resistance"), "oi_put": o.get("support"), "max_pain": o.get("max_pain"), "gamma_flip": g.get("flip")}
    spot = (m or {}).get("spot") or (today[-1][4] if today else None)
    book = LB.load(getattr(e, "data_dir", None))
    out = compute(sym, today, prev, spot, {k: v for k, v in extra.items() if v}, multi_rows, book,
                  is_index=bool((m or {}).get("is_index", True)))
    lg = getattr(e, "linegrade", None)
    if lg is not None:
        try:
            out["live"] = lg.summary(sym)
        except Exception:
            pass
    return out
