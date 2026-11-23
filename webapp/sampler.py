"""2.97: where the app's time really goes (gun; Mandeep, 23 Nov 2026, after 2.96: the Live page and Paper trades still
took 2-4 s with the market open).

A short look at every thread of the running app: every `every_ms` for `secs` seconds it notes, per thread, which of the
app's own functions is running (the nearest frame in this project's code, with its caller) and whether the thread is
waiting (a lock, a queue, the network, sleep) or busy. Python runs one thread at a time, so many busy threads at once
mean every screen waits its turn: the table shows which work to move out or slow down. Read-only: it changes nothing,
costs a few % of one core while it runs, and only runs when asked (System page → Speed → Where the time goes).
"""
from __future__ import annotations

import collections
import linecache
import os
import re
import sys
import threading
import time
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
_LOCK = threading.Lock()                   # one look at a time
WAIT_FILES = ("threading.py", "queue.py", "selectors.py", "socket.py", "ssl.py", "connection.py", "base_events.py",
              "selector_events.py", "subprocess.py", "popen_fork.py", "synchronize.py", "concurrent/futures/thread.py")
WAIT_FUNCS = ("wait", "_wait_for_tstate_lock", "acquire", "get", "select", "recv", "recv_into", "read", "readinto",
              "accept", "poll", "_recv", "_poll", "sleep", "join", "worker", "_worker", "run_forever", "_run_once")


def _own(f, limit: int = 40):
    """The nearest frames in this project's code (not this file): [(path:function:line), ...] leaf first."""
    out = []
    n = 0
    while f is not None and n < limit and len(out) < 3:
        fn = f.f_code.co_filename
        if fn.startswith(ROOT) and not fn.endswith("sampler.py"):
            out.append(f"{os.path.relpath(fn, ROOT)}:{f.f_code.co_name}:{f.f_lineno}")
        f = f.f_back
        n += 1
    return out


NET_PARTS = ("/http/client.py", "/urllib3/", "/httpx/", "/httpcore/", "/requests/", "/websocket/", "/websockets/",
             "/ssl.py", "/socket.py", "/curl_cffi/")
WAIT_LINE = re.compile(r"\bsleep\(|\.wait\(|\.acquire\(|\.join\(|\.get\((block|timeout|True)|\.get\(\s*timeout|"
                       r"\.recv|\.recv_bytes\(|\bselect\(|\.poll\(|\.accept\(|urlopen\(|requests\.(get|post)\(|"
                       r"\.(get|post)\([^)]*timeout=|serve_forever\(|\.readline\(|run_until_complete\(")


def _waiting(f) -> bool:
    """Waiting (not using the core): in a wait / lock / queue / network / sleep, by its function or by the line it is on
    (a C call such as time.sleep has no frame of its own: the line that called it is the leaf)."""
    fn, name = f.f_code.co_filename.replace("\\", "/"), f.f_code.co_name
    if name in WAIT_FUNCS and any(fn.endswith(w) for w in WAIT_FILES):
        return True
    if any(x in fn for x in NET_PARTS):
        return True
    line = linecache.getline(f.f_code.co_filename, f.f_lineno)
    return bool(line and WAIT_LINE.search(line))


def look(secs: float = 20, every_ms: float = 10) -> dict:
    """Samples every thread; returns per thread: busy share, the app functions it is in, and the busiest code overall."""
    secs = max(2.0, min(float(secs), 60.0))
    if not _LOCK.acquire(blocking=False):
        return {"busy": True, "note": "A look is already running."}
    try:
        me = threading.get_ident()
        names = {t.ident: t.name for t in threading.enumerate()}
        per: dict = {}
        overall = collections.Counter()
        n = 0
        t_end = time.time() + secs
        t0 = time.time()
        while time.time() < t_end:
            frames = sys._current_frames()
            busy_now = 0
            for tid, f in frames.items():
                if tid == me:
                    continue
                if tid not in names:
                    names = {t.ident: t.name for t in threading.enumerate()}
                name = re.sub(r"\d+", "#", names.get(tid, "thread"))       # pool threads counted together
                p = per.setdefault(name, {"samples": 0, "busy": 0, "own": collections.Counter(), "leaf": collections.Counter(),
                                          "threads": set()})
                p["samples"] += 1
                p["threads"].add(tid)
                if _waiting(f):
                    continue
                p["busy"] += 1
                busy_now += 1
                own = _own(f)
                key = " ← ".join(own[:2]) if own else f"{os.path.basename(f.f_code.co_filename)}:{f.f_code.co_name}"
                p["own"][key] += 1
                p["leaf"][f"{os.path.basename(f.f_code.co_filename)}:{f.f_code.co_name}:{f.f_lineno}"] += 1
                overall[own[0] if own else key] += 1
            per.setdefault("_busy_threads", collections.Counter())[busy_now] += 1
            n += 1
            time.sleep(every_ms / 1000)
        took = time.time() - t0
        bt = per.pop("_busy_threads")
        rows = []
        for name, p in per.items():
            if not p["busy"]:
                continue
            rows.append({"thread": name, "threads": len(p["threads"]), "busy_pct": round(p["busy"] / max(1, n) * 100, 1),
                         "top": [{"code": k, "pct": round(v / max(1, n) * 100, 1)} for k, v in p["own"].most_common(6)],
                         "leaf": [{"code": k, "pct": round(v / max(1, n) * 100, 1)} for k, v in p["leaf"].most_common(3)]})
        rows.sort(key=lambda r: -r["busy_pct"])
        tot = sum(bt.values()) or 1
        return {"secs": round(took, 1), "samples": n, "threads": len(per),
                "busy_threads_avg": round(sum(k * v for k, v in bt.items()) / tot, 2),
                "busy_threads": {str(k): round(v / tot * 100, 1) for k, v in sorted(bt.items())},
                "rows": rows[:25],
                "code": [{"code": k, "pct": round(v / max(1, n) * 100, 1)} for k, v in overall.most_common(20)],
                "note": "busy % = share of the looks in which that thread was running app code (or waiting for its turn): "
                        "Python runs one thread at a time, so the busy threads together share one core."}
    finally:
        _LOCK.release()
