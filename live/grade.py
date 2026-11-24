"""3.05: honest grade of every alert type (Mandeep, 24 Nov 2026: "okay sab karo").

The test bench (live/bench.py) paper-trades every directional alert, 1 lot, ask in / bid out, every charge. Here each
alert type (indices and stocks apart) is graded on the last `days` market days by what a trade usually does, not by
an average a few big winners (or one bad price) can pull up: the median rupees a trade, the win %, the average with
each trade's effect capped at +-cap_pct of the premium, for the main account's exit ("buy") and the trailing exit
("buy_trail", from 25 Nov). The 60-minute option move of the same alerts (outcomes) is shown as median and average.

On trial (shadow): the Scorecard page shows it; Focus, Pro, the accounts and Telegram do not use it. Numbers live in
rules/grade.json (bullet). Finished days are summarised once and kept on disk (data/bench_cache), so a restart does not
read every trade again. Reads only (its own read-only connections); never blocks the live engine.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import statistics
import threading
import time
from pathlib import Path

RULES = Path(__file__).resolve().parents[1] / "rules" / "grade.json"
DEFAULTS = {"days": 20, "styles": {"buy": "Main exit", "buy_trail": "Trailing"}, "cap_pct": 100, "min_n": 50,
            "edge": {"min_median": 0, "min_win_pct": 50}}
CACHE_V = 1
_C: dict = {}
_VIEW: dict = {}
_LOCK = threading.Lock()


def cfg() -> dict:
    try:
        mt = RULES.stat().st_mtime
        if _C.get("mt") != mt:
            _C.update(mt=mt, v={**DEFAULTS, **json.loads(RULES.read_text())}, error=None)
    except Exception as e:
        _C.update(mt=None, v=dict(DEFAULTS), error=str(e))
    return _C["v"]


def _cache_file(bdb, source: str, day: str) -> Path:
    import re as _re
    return Path(bdb.path).parent / "bench_cache" / f"{_re.sub(r'[^A-Za-z0-9_.-]', '_', source)}__{day}__grade.json.gz"


def _day(bdb, con, source: str, day: str, styles: tuple, finished: bool) -> dict:
    """{"kind|is_index|style": [[net rupees...], [net % of the premium...]]} of one day; finished days kept on disk."""
    from .bench import _day_stamp
    f = _cache_file(bdb, source, day)
    stamp = _day_stamp(con, source, day) if finished else None
    if finished:
        try:
            j = json.loads(gzip.decompress(f.read_bytes()))
            if j.get("v") == CACHE_V and j.get("stamp") == stamp and j.get("styles") == list(styles):
                return j["g"]
        except Exception:
            pass
    q = (f"SELECT kind, IFNULL(is_index, 0), style, net, cost_lot FROM bench_trades WHERE source=? AND day=? AND net IS NOT NULL "
         f"AND style IN ({','.join('?' * len(styles))})")
    g: dict = {}
    for kind, idx, style, net, cost in con.execute(q, (source, day) + tuple(styles)):
        x = g.setdefault(f"{kind}|{int(idx)}|{style}", [[], []])
        x[0].append(round(float(net)))
        x[1].append(round(float(net) / float(cost) * 100, 1) if cost else None)
    if finished:
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(gzip.compress(json.dumps({"v": CACHE_V, "stamp": stamp, "styles": list(styles), "g": g}).encode()))
        except Exception:
            pass
    return g


def summarise(nets: list, pcts: list, c: dict) -> dict:
    n = len(nets)
    if not n:
        return {"n": 0}
    cap = float(c.get("cap_pct") or 100)
    pc = [max(-cap, min(cap, p)) for p in pcts if p is not None]
    return {"n": n, "win_pct": round(100 * sum(x > 0 for x in nets) / n), "median": round(statistics.median(nets)),
            "avg": round(sum(nets) / n), "capped_pct": round(sum(pc) / len(pc), 1) if pc else None, "total": round(sum(nets))}


def verdict(s: dict, c: dict) -> str:
    e = c.get("edge") or {}
    if not s or s.get("n", 0) < int(c.get("min_n") or 50):
        return "too few"
    if s["median"] > float(e.get("min_median", 0)) and s["win_pct"] >= float(e.get("min_win_pct", 50)):
        return "edge"
    if s["median"] < 0:
        return "no edge"
    return "unclear"


def build(store, source: str, today: str, c: dict | None = None) -> dict:
    from .bench import db_for, days_with_trades
    from .store import reading
    c = c or cfg()
    styles = tuple(c.get("styles") or DEFAULTS["styles"])
    bdb = db_for(store)
    con = bdb.reader()
    try:
        first = (dt.date.fromisoformat(today) - dt.timedelta(days=int(c["days"]) * 2 + 10)).isoformat()
        days = days_with_trades(con, source, first, (dt.date.fromisoformat(today) + dt.timedelta(days=1)).isoformat())
        days = days[-int(c["days"]):]
        agg: dict = {}
        for d in days:
            for k, (nets, pcts) in _day(bdb, con, source, d, styles, finished=d < today).items():
                a = agg.setdefault(k, [[], []])
                a[0].extend(nets)
                a[1].extend(pcts)
    finally:
        con.close()
    o60: dict = {}
    if days:
        try:
            with reading(store) as db:
                for kind, idx, v in db.execute("SELECT kind, IFNULL(is_index, 0), o60 FROM outcomes WHERE source=? AND day>=? "
                                               "AND day<=? AND o60 IS NOT NULL", (source, days[0], days[-1])):
                    o60.setdefault(f"{kind}|{int(idx)}", []).append(float(v))
        except Exception:
            o60 = {}
    rows: dict = {}
    for k, (nets, pcts) in agg.items():
        kind, idx, style = k.split("|")
        r = rows.setdefault((kind, int(idx)), {"kind": kind, "is_index": bool(int(idx))})
        r[style] = summarise(nets, pcts, c)
    for (kind, idx), r in rows.items():
        v = o60.get(f"{kind}|{idx}") or []
        r["o60"] = {"n": len(v), "median": round(statistics.median(v), 1), "avg": round(sum(v) / len(v), 1)} if v else None
        r["verdict"] = verdict(r.get("buy") or {}, c)
        r["verdict_trail"] = verdict(r.get("buy_trail") or {}, c)
    out = sorted(rows.values(), key=lambda r: (r["verdict"] != "edge", -((r.get("buy") or {}).get("median") or -1e9)))
    graded = [r for r in out if r["verdict"] != "too few"]
    return {"source": source, "days": days, "from": days[0] if days else None, "to": days[-1] if days else None,
            "styles": c.get("styles"), "rows": out, "made": time.time(),
            "summary": {"types": len(graded), "edge": sum(r["verdict"] == "edge" for r in graded),
                        "edge_index": sum(r["verdict"] == "edge" and r["is_index"] for r in graded),
                        "edge_stock": sum(r["verdict"] == "edge" and not r["is_index"] for r in graded),
                        "trail_edge": sum(r["verdict_trail"] == "edge" for r in out)},
            "rules": {k: c.get(k) for k in ("days", "cap_pct", "min_n", "edge", "why")}}


def view(store, source: str, today: str, max_age: float = 900) -> dict:
    """The last grade, worked out again in the background when older than max_age (the page never waits for it)."""
    key = (source, today)
    with _LOCK:
        v = _VIEW.get(key)
        busy = _VIEW.get(("busy", key))
        fresh = v is not None and time.time() - v["made"] < max_age
        if not fresh and not busy:
            _VIEW[("busy", key)] = True

            def run():
                try:
                    r = build(store, source, today)
                    with _LOCK:
                        _VIEW[key] = r
                except Exception as e:
                    with _LOCK:
                        _VIEW[("err", key)] = str(e)
                finally:
                    with _LOCK:
                        _VIEW.pop(("busy", key), None)
            threading.Thread(target=run, daemon=True, name="grade").start()
            busy = True
        err = _VIEW.get(("err", key))
    if v is None:
        return {"computing": bool(busy), "error": err, "rows": []}
    return {**v, "computing": bool(busy and not fresh)}
