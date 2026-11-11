"""Heavy Strategy Lab work in its own low-priority process (gun, 2.38).

A Lab round (thousands of rules scored on a 200 MB bar table) and the model-based strategies keep a CPU busy for
minutes to hours. Run inside the app's own process they compete with every screen for Python's single interpreter
lock, so pages crawl in the evening and at night while the nightly Lab runs. Here the same code runs in a child
process at lower priority (nice 10; on a Mac also macOS background mode, 2.66.1): the same results, and the app
and the Mac stay quick.

- One run per Lab folder at a time still holds: the parent keeps lab.factory's per-folder lock while the child works.
- Log lines stream back to the caller's log; Stop asks the child to finish cleanly (SIGTERM -> its should_stop), and
  ends it if it does not within a minute.
- If a child process cannot start, the work runs in the app's process exactly as before.
Read-only towards brokers: nothing here talks to a broker.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOBS = ("factory", "mlstrat", "candlecast", "paths", "multiverse", "mass", "daytype", "echo")
NICE = 10
RESULT = "@@RESULT "
LAST: dict = {}                   # the latest run: how it ran (child / in-process), when, how long (System page)


class _NoChild(Exception):
    pass


def _inproc(job: str, data_dir, log, should_stop, kw: dict):
    if job == "factory":
        from .factory import _run
        return _run(data_dir, int(kw.get("n_random", 2000)), int(kw.get("n_mutate", 1000)), log, should_stop, kw.get("extra"))
    if job == "mlstrat":
        from . import mlstrat
        return mlstrat.run(data_dir, log=log, should_stop=should_stop)
    if job == "candlecast":                              # 2.41: the Fortune candle forecast models (live/candlecast.py)
        from live import candlecast
        return candlecast.run(data_dir, log=log, should_stop=should_stop, tfs=kw.get("tfs"))
    if job == "paths":                                   # 2.45: trade journeys from the day's recording (live/paths.py)
        from live import paths
        return paths.run(data_dir, log=log, should_stop=should_stop, days=kw.get("days"), source=kw.get("source") or "upstox")
    if job == "multiverse":                              # 2.58: every bench signal down every timeline (lab/multiverse.py)
        from . import multiverse
        return multiverse.run(data_dir, log=log, should_stop=should_stop, days=kw.get("days"))
    if job == "mass":                                    # 2.60: every single / paired condition at once (lab/mass.py)
        from . import mass
        return mass.run(data_dir, log=log, should_stop=should_stop)
    if job == "daytype":                                 # 2.66: the 09:45 day-type forecaster (lab/daytype.py)
        from . import daytype
        return daytype.learn(data_dir, log=log)
    if job == "echo":                                    # for the tests: proves the child runs, logs and stops
        for i in range(int(kw.get("n", 3))):
            if should_stop():
                log("stopped")
                return {"stopped": True, "pid": os.getpid()}
            log(f"line {i}")
            time.sleep(float(kw.get("sleep", 0)))
        return {"done": int(kw.get("n", 3)), "pid": os.getpid()}
    raise ValueError(f"unknown Lab job {job!r}")


def _lock_for(data_dir):
    from . import factory
    key = str(Path(data_dir).resolve())
    with factory._RUN_LOCKS_GUARD:
        return factory._RUN_LOCKS.setdefault(key, threading.Lock())


def run(job: str, data_dir, log=print, should_stop=lambda: False, child: bool = True, nice: int = NICE, **kw):
    """Run a Lab job (factory = one research round, mlstrat = model-based strategies) in a child process."""
    if job not in JOBS:
        raise ValueError(f"unknown Lab job {job!r}")
    lk = _lock_for(data_dir) if job in ("factory", "mass") else None     # 2.60: mass search queues ideas: one at a time
    if lk is not None and not lk.acquire(blocking=False):
        log("Another Strategy Lab run is in progress on this data: waiting for it to finish…")
        while not lk.acquire(timeout=5):
            if should_stop():
                raise RuntimeError("Stopped while waiting for the other Strategy Lab run.")
    t0 = time.time()
    how = "child process"
    try:
        if child:
            try:
                return _child(job, data_dir, log, should_stop, nice, kw)
            except _NoChild as e:
                how = "app process"
                log(f"(Lab running inside the app: a separate process could not start: {e})")
        else:
            how = "app process"
        return _inproc(job, data_dir, log, should_stop, kw)
    finally:
        LAST.update(job=job, how=how, at=time.time(), secs=round(time.time() - t0, 1), folder=str(data_dir))
        if lk is not None:
            lk.release()


def lower_priority(nice: int = NICE):
    """Runs in the child before the job starts. nice 10 everywhere; on a Mac also macOS background mode (2.66.1):
    efficiency cores, throttled disk and network, so a long Lab evening no longer makes the whole Mac lag.
    Same results, only slower when the Mac is busy."""
    if nice and hasattr(os, "nice"):
        os.nice(nice)
    if sys.platform == "darwin" and hasattr(os, "setpriority"):
        try:
            os.setpriority(getattr(os, "PRIO_DARWIN_PROCESS", 4), 0, getattr(os, "PRIO_DARWIN_BG", 0x1000))
        except Exception:
            pass


def _child(job, data_dir, log, should_stop, nice, kw):
    spec = json.dumps({"job": job, "data_dir": str(data_dir), "kw": kw}, default=str)
    env = {**os.environ, "PYTHONWARNINGS": "ignore", "PYTHONUNBUFFERED": "1"}
    pre = (lambda: lower_priority(nice)) if nice else None
    try:
        p = subprocess.Popen([sys.executable, "-u", "-m", "lab.proc", spec], cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, preexec_fn=pre)
    except Exception as e:
        raise _NoChild(str(e) or type(e).__name__)
    done = threading.Event()

    def watch():                                         # Stop: ask nicely, then end it
        asked = None
        while not done.wait(1.0):
            if p.poll() is not None:
                return
            if asked is None and should_stop():
                asked = time.time()
                try:
                    p.send_signal(signal.SIGTERM)
                except Exception:
                    pass
            if asked is not None and time.time() - asked > 60:
                try:
                    p.kill()
                except Exception:
                    pass
                return
    threading.Thread(target=watch, daemon=True, name="lab-child-watch").start()
    result, tail = None, []
    try:
        for line in p.stdout:
            line = line.rstrip("\n")
            if line.startswith(RESULT):
                try:
                    result = json.loads(line[len(RESULT):])
                except Exception:
                    result = {}
                continue
            tail = (tail + [line])[-8:]
            if line.strip():
                log(line)
        rc = p.wait()
    finally:
        done.set()
    if result is None:
        if should_stop():
            raise RuntimeError("Stopped.")
        raise RuntimeError(f"the Lab process ended early (code {rc}): " + " | ".join(t for t in tail if t.strip())[-400:])
    if isinstance(result, dict) and result.get("_error"):
        raise RuntimeError(result["_error"])
    return result


def _main(argv) -> int:
    spec = json.loads(argv[1])
    stop = threading.Event()
    try:
        signal.signal(signal.SIGTERM, lambda *a: stop.set())
    except Exception:
        pass
    sys.path.insert(0, str(ROOT))
    try:
        out = _inproc(spec["job"], spec["data_dir"], lambda m: print(m, flush=True), stop.is_set, spec.get("kw") or {})
    except Exception as e:
        out = {"_error": str(e) or type(e).__name__}
    try:
        print(RESULT + json.dumps(out if isinstance(out, (dict, list)) else {"value": out}, default=str), flush=True)
    except Exception:
        print(RESULT + "{}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
