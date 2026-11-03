"""Mover sweep (gun): which stocks are moving decently right now, so the test bench can paper-test them.

The thresholds are bullets in rules/bench_movers.json. Moves are measured in the stock's own daily ATR (average daily
range), so a 2% move in a quiet stock and in a wild one are not treated the same. Pure functions: no data calls.
"""
from __future__ import annotations

import json
from pathlib import Path

FILE = Path(__file__).resolve().parents[1] / "rules" / "bench_movers.json"
DEFAULTS = {"every_min": 15, "start": "09:30", "end": "14:30", "top_n": 10, "min_move_atr": 0.6, "min_ret15_atr": 0.25,
            "min_rel_vol": 0, "indices": True, "both_ways": True, "in_play_max": 25}
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


def pick(metrics: dict, daily: dict, c: dict | None = None) -> list:
    """[{"sym", "direction", "move_atr", "ret15_atr", "score", "is_index", "why"}], strongest first.

    metrics: the engine's per-symbol metrics (chg_pct, ret_15m, rel_vol, is_index); daily: per-symbol daily facts with
    htf.atr_pct (yesterday's 14-day average range as % of price)."""
    c = c or cfg()
    stocks, idx = [], []
    for sym, m in (metrics or {}).items():
        atr = ((daily.get(sym) or {}).get("htf") or {}).get("atr_pct")
        if not atr or atr <= 0:
            continue
        chg, r15 = m.get("chg_pct"), m.get("ret_15m")
        if chg is None and r15 is None:
            continue
        mv, f15 = abs(chg or 0) / atr, abs(r15 or 0) / atr
        if c.get("min_rel_vol") and (m.get("rel_vol") or 0) < float(c["min_rel_vol"]):
            continue
        fresh = f15 >= float(c["min_ret15_atr"])
        if m.get("is_index"):
            if c.get("indices") and fresh:
                idx.append({"sym": sym, "direction": "up" if (r15 or 0) > 0 else "down", "move_atr": round(mv, 2),
                            "ret15_atr": round(f15, 2), "score": round(mv + 2 * f15, 2), "is_index": True,
                            "why": f"last 15 min {r15:+.2f}% ({f15:.2f} ATR)"})
            continue
        if mv < float(c["min_move_atr"]) and not fresh:
            continue
        d = (r15 if fresh else chg) or 0
        if d == 0:
            continue
        stocks.append({"sym": sym, "direction": "up" if d > 0 else "down", "move_atr": round(mv, 2), "ret15_atr": round(f15, 2),
                       "score": round(mv + 2 * f15, 2), "is_index": False,
                       "why": (f"last 15 min {r15:+.2f}% ({f15:.2f} ATR)" if fresh else f"today {chg:+.2f}% ({mv:.2f} ATR)")})
    stocks.sort(key=lambda x: -x["score"])
    idx.sort(key=lambda x: -x["score"])
    return idx + stocks[:int(c["top_n"])]
