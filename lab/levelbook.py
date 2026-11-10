"""2.54 line book: how often each kind of chart line held or broke on your own 1-minute history, by situation.

Mandeep, 11 Nov 2026: "check whether we are marking and analysing good support, resistance ... make some smart system
that can draw lines on the chart by itself analysing each and everything that matters".

Gun (this file, shared with the live line finder live/linesmith.py): the lines of a day from the previous days and the
day's own bars, the first touch of each line, the outcome (hold / break within 60 minutes), the situation (how fast price
came to the line, with or against the day's move, time of day), and the book of hold rates per line type and situation,
with random lines in the same situation as the baseline and an older (train) / newer (holdout) split.
Bullets: rules/lines.json (touch distances, buckets, thresholds, line names, round-number steps).

First read (Nifty + Bank Nifty, Jan 2022 - Oct 2026): plain lines hold about as often as random lines (~50%); how price
arrives decides: slow approaches hold ~56-58%, fast ones break ~56-59%, and yesterday's low / 20-day low / opening range
low touched on a down day break 58-63% of the time. Market data only; nothing here trades.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "rules" / "lines.json"
DEFAULT_BOOK = ROOT / "rules" / "level_book.json"
IST = 19800
SITUATIONS = ("all", "speed", "day", "speed|day")
_C: dict = {"mtime": None, "cfg": None, "error": None}
_B: dict = {}
_LOCK = threading.Lock()
_RUN = {"running": False, "error": None, "at": None}


def cfg() -> dict:
    """rules/lines.json, re-read when it changes; a broken file keeps the last good copy (and says so)."""
    try:
        mt = RULES.stat().st_mtime
        if _C["mtime"] != mt:
            c = json.loads(RULES.read_text())
            if not isinstance(c.get("touch"), dict) or not isinstance(c.get("types"), dict):
                raise ValueError("touch / types missing")
            _C.update(mtime=mt, cfg=c, error=None)
    except Exception as e:
        _C["error"] = f"rules/lines.json: {e}"
        if _C["cfg"] is None:
            _C["cfg"] = {"touch": {"index": {"away_pct": 0.15, "touch_pct": 0.03, "move_pct": 0.25, "brk_pct": 0.10}},
                         "types": {}, "window_min": 60, "first_minute": 15, "speed_lookback_min": 15, "day_move_pct": 0.3,
                         "split_date": "2025-01-01", "random_lines_per_day": 12, "random_band_pct": 1.5, "min_n": 60,
                         "matters_pts": 4.0, "matters_each_half_pts": 2.0, "holds_pct": 55, "breaks_pct": 45}
    return _C["cfg"]


def params(sym: str, is_index: bool = True) -> dict:
    c = cfg()
    t = c["touch"]
    if not is_index:
        return t.get("stock") or t["index"]
    return t.get("index_big", t["index"]) if sym in (c.get("big_index") or []) else t["index"]


def round_steps(sym: str, price: float) -> list:
    st = (cfg().get("round_steps") or {}).get(sym)
    if st:
        return [float(x) for x in st]
    p = float(price)                                  # stocks: scale by price
    s = 1000 if p > 20000 else 100 if p > 5000 else 50 if p > 1000 else 10 if p > 200 else 5
    return [float(s), float(s * 5)]


def day_lines(sym: str, o: float, prev: list, rng=None) -> list:
    """Lines known before the day starts: (type, price). prev = previous finished days [(h, l, c)], oldest first."""
    c = cfg()
    out = []
    if prev:
        h, l, cl = prev[-1]
        p = (h + l + cl) / 3
        bc = (h + l) / 2
        tc = 2 * p - bc
        out += [("pdh", h), ("pdl", l), ("pdc", cl), ("pivot", p), ("tc", max(tc, bc)), ("bc", min(tc, bc)),
                ("r1", 2 * p - l), ("s1", 2 * p - h), ("r2", p + (h - l)), ("s2", p - (h - l))]
        if len(prev) >= 5:
            out += [("wk_h", max(x[0] for x in prev[-5:])), ("wk_l", min(x[1] for x in prev[-5:]))]
        if len(prev) >= 20:
            out += [("d20_h", max(x[0] for x in prev[-20:])), ("d20_l", min(x[1] for x in prev[-20:]))]
    if o:
        out.append(("open", o))
        band = float(c.get("random_band_pct") or 1.5) / 100
        small, big = (round_steps(sym, o) + [None, None])[:2]
        for typ, step in (("round_s", small), ("round_b", big)):
            if step:
                for r in np.arange(np.floor(o * (1 - band) / step) * step, o * (1 + band), step):
                    if r > 0 and not (typ == "round_s" and big and abs(r / big - round(r / big)) < 1e-9):
                        out.append((typ, float(r)))
        if rng is not None:
            for _ in range(int(c.get("random_lines_per_day") or 12)):
                out.append(("random", o * (1 + rng.uniform(-band, band))))
    return [(t, float(x)) for t, x in out if x and np.isfinite(x) and x > 0]


def swings5(h1, l1, k: int = 2) -> list:
    """5-minute fractal swing highs / lows from 1-minute bars: (type, price, minute it was confirmed)."""
    n5 = len(h1) // 5
    if n5 <= 2 * k + 1:
        return []
    h5 = np.asarray(h1[:n5 * 5], float).reshape(n5, 5).max(1)
    l5 = np.asarray(l1[:n5 * 5], float).reshape(n5, 5).min(1)
    out = []
    for i in range(k, n5 - k):
        if h5[i] == h5[i - k:i + k + 1].max():
            out.append(("sw_h", float(h5[i]), (i + k + 1) * 5))
        if l5[i] == l5[i - k:i + k + 1].min():
            out.append(("sw_l", float(l5[i]), (i + k + 1) * 5))
    return out


def opening_range(h1, l1, minutes: int = 15):
    if len(h1) < minutes:
        return None
    return float(np.max(h1[:minutes])), float(np.min(l1[:minutes]))


def speed_toward(cl, t: int, side: int, lookback: int) -> float:
    """% the price moved toward the line over the last `lookback` minutes (side +1: line below, so falling = toward)."""
    j = max(0, t - lookback)
    return float((cl[t] / cl[j] - 1) * 100 * -side)


def day_side(price: float, day_open: float, side: int, thr: float) -> str:
    """'with' = a pullback to the line in the day's direction (up day, support below); 'against' = the day is moving
    toward the line (up day, resistance above / down day, support below); 'flat' = the day moved less than thr %."""
    dm = (price / day_open - 1) * 100
    if abs(dm) < thr:
        return "flat"
    return "with" if np.sign(dm) == side else "against"


def first_touch(L: float, start: int, hi, lo, cl, p: dict, window: int):
    """The first clean touch of line L from minute `start`: (minute, side, result) or None.
    side +1 = price was above (support), -1 = below (resistance). result 'hold' / 'break' / None (undecided)."""
    side = 0
    for t in range(start, len(cl) - 1):
        dist = (cl[t] / L - 1) * 100
        if side == 0:
            if abs(dist) >= p["away_pct"]:
                side = 1 if dist > 0 else -1
            continue
        touched = (lo[t] <= L * (1 + p["touch_pct"] / 100)) if side > 0 else (hi[t] >= L * (1 - p["touch_pct"] / 100))
        if not touched:
            if (side > 0 and dist < 0) or (side < 0 and dist > 0):
                side = 1 if dist > 0 else -1
            continue
        return t, side, outcome(L, t, side, hi, lo, cl, p, window)
    return None


def outcome(L: float, t: int, side: int, hi, lo, cl, p: dict, window: int):
    broke = False
    for u in range(t, min(len(cl), t + window + 1)):
        if not broke and (L - cl[u]) / L * 100 * side >= p["brk_pct"]:
            broke = True
        if broke:
            if ((L - lo[u]) if side > 0 else (hi[u] - L)) / L * 100 >= p["move_pct"]:
                return "break"
        elif (max(hi[u] - L, 0) if side > 0 else max(L - lo[u], 0)) / L * 100 >= p["move_pct"]:
            return "hold"
    return None


def day_events(sym: str, day, o1, h1, l1, c1, prev: list, rng=None, is_index: bool = True) -> list:
    """All first touches of a day's lines (historical days: the same lines the live chart draws, minus OI / gamma)."""
    c = cfg()
    p = params(sym, is_index)
    hi, lo, cl = (np.asarray(x, float) for x in (h1, l1, c1))
    o = float(o1[0])
    first = int(c.get("first_minute") or 15)
    lines = [(t, x, first) for t, x in day_lines(sym, o, prev, rng)]
    orr = opening_range(hi, lo, 15)
    if orr:
        lines += [("or_h", orr[0], 15), ("or_l", orr[1], 15)]
    lines += [(t, x, max(first, m)) for t, x, m in swings5(hi, lo)]
    ev = []
    lb, thr, win = int(c.get("speed_lookback_min") or 15), float(c.get("day_move_pct") or 0.3), int(c.get("window_min") or 60)
    for typ, L, start in lines:
        r = first_touch(L, start, hi, lo, cl, p, win)
        if r is None:
            continue
        t, side, res = r
        ev.append({"sym": sym, "date": str(day), "type": typ, "side": side, "tod": t,
                   "speed_r": speed_toward(cl, t, side, lb) / p["move_pct"], "day": day_side(cl[t], o, side, thr), "res": res})
    return ev


def history_events(data_dir, sym: str, log=print) -> list:
    import pandas as pd
    f = Path(data_dir) / "history" / sym / "minutes.parquet"
    if not f.exists():
        return []
    m = pd.read_parquet(f, columns=["ts", "open", "high", "low", "close"])
    t = pd.to_datetime(m["ts"] + IST, unit="s")
    m["date"] = t.dt.date
    m["mins"] = t.dt.hour * 60 + t.dt.minute - 555
    m = m[(m["mins"] >= 0) & (m["mins"] <= 375)]
    rng = np.random.default_rng(zlib.crc32(sym.encode()))   # same random lines every rebuild
    days = [(d, g) for d, g in m.groupby("date") if len(g) >= 200]
    prev, ev = [], []
    for d, g in days:
        o1, h1, l1, c1 = (g[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        if len(prev) >= 20:
            ev += day_events(sym, d, o1, h1, l1, c1, prev, rng)
        prev.append((float(h1.max()), float(l1.min()), float(c1[-1])))
    log(f"Line book: {sym} {len(days):,} days, {len(ev):,} line touches")
    return ev


def _speed_cuts(ev: list) -> list:
    xs = np.array([e["speed_r"] for e in ev if e["type"] != "random" and np.isfinite(e["speed_r"])])
    return [round(float(np.quantile(xs, 1 / 3)), 3), round(float(np.quantile(xs, 2 / 3)), 3)] if len(xs) > 30 else [0.3, 0.8]


def speed_bucket(speed_r: float, cuts) -> str:
    return "slow" if speed_r <= cuts[0] else "fast" if speed_r >= cuts[1] else "mid"


def cell_keys(sb: str, db: str) -> list:
    """Finest first: speed|day, speed, day, all."""
    return [f"{sb}|{db}", f"speed:{sb}", f"day:{db}", "all"]


def summarise(ev: list, split: str, cuts) -> dict:
    """{type: {cell: {n, hold, n_tr, hold_tr, n_ho, hold_ho, touches}}} with hold in % of decided touches."""
    cells: dict = {}
    for e in ev:
        sb = speed_bucket(e["speed_r"], cuts)
        half = "tr" if e["date"] < split else "ho"
        for k in cell_keys(sb, e["day"]):
            c = cells.setdefault(e["type"], {}).setdefault(k, {"touches": 0, "n": 0, "h": 0, "n_tr": 0, "h_tr": 0, "n_ho": 0, "h_ho": 0})
            c["touches"] += 1
            if e["res"] is None:
                continue
            hold = e["res"] == "hold"
            c["n"] += 1
            c["h"] += hold
            c[f"n_{half}"] += 1
            c[f"h_{half}"] += hold
    out = {}
    for typ, cs in cells.items():
        out[typ] = {}
        for k, c in cs.items():
            pct = lambda h, n: round(h / n * 100, 1) if n else None
            out[typ][k] = {"touches": c["touches"], "n": c["n"], "hold": pct(c["h"], c["n"]), "n_tr": c["n_tr"],
                           "hold_tr": pct(c["h_tr"], c["n_tr"]), "n_ho": c["n_ho"], "hold_ho": pct(c["h_ho"], c["n_ho"])}
    return out


def build(data_dir, syms=("NIFTY", "BANKNIFTY"), log=print, save: bool = True) -> dict:
    """Rebuild the book from data/history/<sym>/minutes.parquet. Saved to data/lab/level_book.json (atomic)."""
    c = cfg()
    ev = []
    used = {}
    for s in syms:
        e = history_events(data_dir, s, log)
        if e:
            used[s] = {"days": len({x["date"] for x in e}), "from": min(x["date"] for x in e), "to": max(x["date"] for x in e)}
            ev += e
    if not ev:
        raise RuntimeError("no 1-minute index history on this Mac yet (data/history)")
    cuts = _speed_cuts(ev)
    split = str(c.get("split_date") or "2025-01-01")
    book = {"version": 1, "made": dt.datetime.now().isoformat(timespec="seconds"), "syms": used, "split": split,
            "speed_cuts": cuts, "params": {k: c.get(k) for k in ("touch", "window_min", "day_move_pct", "speed_lookback_min")},
            "touches": len(ev), "cells": summarise(ev, split, cuts)}
    if save:
        d = Path(data_dir) / "lab"
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "level_book.json.tmp"
        tmp.write_text(json.dumps(book))
        os.replace(tmp, d / "level_book.json")
    return book


def load(data_dir=None) -> dict | None:
    """Your own book (data/lab/level_book.json) if there is one, else the book shipped with the app."""
    for f in ([Path(data_dir) / "lab" / "level_book.json"] if data_dir else []) + [DEFAULT_BOOK]:
        try:
            mt = f.stat().st_mtime
        except OSError:
            continue
        b = _B.get(str(f))
        if b and b[0] == mt:
            return b[1]
        try:
            book = json.loads(f.read_text())
            if isinstance(book.get("cells"), dict):
                book["_file"] = "yours" if f != DEFAULT_BOOK else "shipped"
                _B[str(f)] = (mt, book)
                return book
        except Exception:
            continue
    return None


def rebuild_async(data_dir, log=print) -> bool:
    """Rebuild in a background thread (one at a time). False if one is already running."""
    with _LOCK:
        if _RUN["running"]:
            return False
        _RUN.update(running=True, error=None)

    def go():
        try:
            build(data_dir, log=log)
            _RUN["at"] = time.time()
        except Exception as e:
            _RUN["error"] = str(e)[:200]
            log(f"Line book rebuild failed: {e}")
        finally:
            _RUN["running"] = False
    threading.Thread(target=go, name="line-book", daemon=True).start()
    return True


def status(data_dir=None) -> dict:
    b = load(data_dir)
    return {"running": _RUN["running"], "error": _RUN["error"], "file": (b or {}).get("_file"), "made": (b or {}).get("made"),
            "syms": (b or {}).get("syms"), "touches": (b or {}).get("touches")}


SPEED_WORDS = {"slow": "when price came slowly", "mid": "when price came at a normal pace", "fast": "when price came fast"}
DAY_WORDS = {"with": "on a pullback in the day's move", "against": "with the day's move pushing into it", "flat": "on a flat day"}


def situation_words(cell: str, sb: str, day: str) -> str:
    if cell.count("|"):
        return f"{SPEED_WORDS[sb]}, {DAY_WORDS.get(day, '')}"
    if cell.startswith("speed:"):
        return SPEED_WORDS[sb]
    if cell.startswith("day:"):
        return DAY_WORDS.get(day, "")
    return "in any situation"


def evidence(book: dict | None, typ: str, speed_r: float | None, day: str | None) -> dict | None:
    """How this kind of line did in this situation: hold % (decided touches), random lines in the same situation,
    the older / newer split, and a verdict. None without a book or without enough touches."""
    if not book or speed_r is None or not np.isfinite(speed_r):
        return None
    c = cfg()
    cells = book.get("cells") or {}
    own, rnd = cells.get(typ) or {}, cells.get("random") or {}
    sb = speed_bucket(speed_r, book.get("speed_cuts") or [0.3, 0.8])
    min_n = int(c.get("min_n") or 60)
    for k in cell_keys(sb, day or "flat"):
        a, r = own.get(k), rnd.get(k)
        if a and r and a["n"] >= min_n and r["n"] >= min_n // 2:
            break
    else:
        a = r = None
    if not a:
        return {"type": typ, "tested": bool(own), "situation": situation_words("x|y", sb, day or "flat"),
                "verdict": "not tested yet", "words": "not enough history for this kind of line yet: graded live from today"}
    hold, rh = a["hold"], r["hold"]
    edge = round(hold - rh, 1)
    each = float(c.get("matters_each_half_pts") or 2.0)
    stable = all(x is not None for x in (a["hold_tr"], a["hold_ho"], r["hold_tr"], r["hold_ho"])) and \
        np.sign(a["hold_tr"] - r["hold_tr"]) == np.sign(a["hold_ho"] - r["hold_ho"]) == np.sign(edge) and \
        min(abs(a["hold_tr"] - r["hold_tr"]), abs(a["hold_ho"] - r["hold_ho"])) >= each
    matters = stable and abs(edge) >= float(c.get("matters_pts") or 4.0)
    verdict = "likely holds" if hold >= float(c.get("holds_pct") or 55) else "likely breaks" if hold <= float(c.get("breaks_pct") or 45) else "coin flip"
    situation = situation_words(k, sb, day)
    pct, what = (hold, "held") if hold >= 50 else (round(100 - hold, 1), "broke")
    rp = rh if what == "held" else round(100 - rh, 1)
    words = (f"{what} {pct:g}% of {a['n']:,} times {situation}; any random line here {what} {rp:g}%: "
             + (("this line holds clearly more often than a random line" if edge > 0 else
                 "this line breaks clearly more often than a random line") + " (older and newer years agree)" if matters
                else "the line itself adds little, the situation decides"))
    return {"type": typ, "tested": True, "cell": k, "situation": situation, "n": a["n"], "hold": hold, "random_hold": rh,
            "edge": edge, "matters": bool(matters), "verdict": verdict, "hold_tr": a["hold_tr"], "hold_ho": a["hold_ho"],
            "words": words}
