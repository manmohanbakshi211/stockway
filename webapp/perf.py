"""How quickly the app answers (gun, 2.38).

Every /api call is timed in the web server's middleware. The slow screen calls (GET over SLOW_MS) are kept, newest
first, and every kind of call keeps a small tally (count, average, slowest), so the System page shows which screen is
slow and since when, instead of guessing. Kept in memory only (reset on restart); costs microseconds a call.
"""
from __future__ import annotations

import collections
import threading
import time

SLOW_MS = 1500
_lock = threading.Lock()
_slow: collections.deque = collections.deque(maxlen=60)
_stats: dict = {}
_parts: dict = {}                 # 2.94: the pieces of a slow call (e.g. "system: checks"), so the fix goes to the right one
START = time.time()
STARTUP_S = 120                   # 2.94: a slow call in the first 2 minutes after the start is marked "just after start"


def group(path: str) -> str:
    """/api/live/candles/NIFTY -> /api/live/candles (one line per kind of call, not per symbol)."""
    parts = path.split("/")
    return "/".join(parts[:4]) if len(parts) > 4 else path


def record(method: str, path: str, query: str, ms: float, status: int):
    if not path.startswith("/api/"):
        return
    g = f"{method} {group(path)}"
    now = time.time()
    with _lock:
        s = _stats.setdefault(g, [0, 0.0, 0.0, 0.0])
        s[0] += 1
        s[1] += ms
        s[2] = max(s[2], ms)
        s[3] = now
        if method == "GET" and ms >= SLOW_MS:
            _slow.appendleft({"path": path + ("?" + query if query else ""), "ms": round(ms), "at": now, "status": status,
                              "startup": now - START < STARTUP_S})


def record_part(name: str, ms: float):
    with _lock:
        s = _parts.setdefault(name, [0, 0.0, 0.0])
        s[0] += 1
        s[1] += ms
        s[2] = max(s[2], ms)


class Marks:
    """Times the pieces of one call: m = Marks("system"); ...; m("services"); ...; m("checks")."""

    def __init__(self, prefix: str):
        self.prefix, self.t = prefix, time.perf_counter()

    def __call__(self, name: str):
        now = time.perf_counter()
        record_part(f"{self.prefix}: {name}", (now - self.t) * 1000)
        self.t = now


def view(top: int = 12) -> dict:
    with _lock:
        stats = [{"call": g, "n": s[0], "avg_ms": round(s[1] / s[0]), "max_ms": round(s[2]), "last": s[3]}
                 for g, s in _stats.items() if s[0]]
        slow = list(_slow)[:20]
        parts = sorted(({"part": k, "n": v[0], "avg_ms": round(v[1] / v[0]), "max_ms": round(v[2])} for k, v in _parts.items()
                        if v[0]), key=lambda x: -x["avg_ms"])[:10]
    stats.sort(key=lambda x: -x["avg_ms"])
    recent = [x for x in slow if time.time() - x["at"] < 3600]
    return {"slow_ms": SLOW_MS, "since": START, "slow": slow, "slow_last_hour": len(recent), "slowest": stats[:top],
            "calls": sum(x["n"] for x in stats), "parts": parts,
            "slow_last_hour_running": sum(1 for x in recent if not x.get("startup"))}
