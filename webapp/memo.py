"""2.94: screen answers that only change when their files change (gun).

Mandeep, 23 Nov 2026 (the System page's Speed panel: "check the speed one"): some screens redid the same slow work on
every call: the Daily Radar status loaded its model file and 17 price files each time, the System page parsed the
Strategy Lab's 5 MB results to show one date, Trade journeys decoded a day's 9,000 paths again. memo() keeps an answer
until one of the files it came from changes (its fingerprint: path, modified time, size), optionally for at most `ttl`
seconds, and optionally on disk so a restart doesn't pay again. The answer is the same as computing it afresh (tests).
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_mem: dict = {}


def stamp(*paths) -> list:
    """The fingerprint of some files: [path, modified time (ns), size] each; a missing file is [path, None, None]."""
    out = []
    for p in paths:
        try:
            s = os.stat(p)
            out.append([str(p), s.st_mtime_ns, s.st_size])
        except OSError:
            out.append([str(p), None, None])
    return out


def _norm(fp):
    return json.loads(json.dumps(fp, default=str))


def memo(key: str, fp, fn, ttl: float | None = None, disk: Path | None = None):
    """fn() once per fingerprint `fp` (anything JSON-able). ttl: recompute after that many seconds anyway.
    disk: a JSON file that keeps the last answer across restarts (the answer must be JSON-able)."""
    fp = _norm(fp)
    now = time.time()
    with _lock:
        hit = _mem.get(key)
    if hit is not None and hit[0] == fp and (ttl is None or now - hit[2] < ttl):
        return hit[1]
    if disk is not None and hit is None and ttl is None:
        try:
            j = json.loads(Path(disk).read_text())
            if j.get("fp") == fp:
                with _lock:
                    _mem[key] = (fp, j["value"], now)
                return j["value"]
        except Exception:
            pass
    val = fn()
    with _lock:
        _mem[key] = (fp, val, now)
    if disk is not None:
        try:
            d = Path(disk)
            d.parent.mkdir(parents=True, exist_ok=True)
            tmp = d.with_name(d.name + ".tmp")
            tmp.write_text(json.dumps({"fp": fp, "value": val}, default=str))
            tmp.replace(d)
        except Exception:
            pass
    return val


def forget(prefix: str = ""):
    with _lock:
        for k in [k for k in _mem if k.startswith(prefix)]:
            _mem.pop(k, None)
