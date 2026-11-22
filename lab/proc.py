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
- 2.80: automatic (nightly) work can be paused for market hours: given a pause() check, a new job waits for it to
  clear, and a running child is frozen (SIGSTOP) while it is true and continued (SIGCONT) after: nothing is lost or
  redone, the live scanner has the Mac to itself. Runs started by hand are never paused.
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
JOBS = ("factory", "mlstrat", "candlecast", "paths", "multiverse", "mass", "daytype", "radar_indices", "situation", "rider",
        "echo")
NICE = 10
RESULT = "@@RESULT "
LAST: dict = {}                   # the latest run: how it ran (child / in-process), when, how long (System page)
STATE: dict = {}                  # 2.80: the running automatic job: {job, folder, since, paused, paused_at, paused_s}
_FROZEN: set = set()              # 2.80: pids frozen right now (woken and ended if the app quits)


def _wake_all():
    for pid in list(_FROZEN):
        for sig in ("SIGCONT", "SIGTERM"):
            try:
                os.kill(pid, getattr(signal, sig))
            except Exception:
                pass


import atexit                     # noqa: E402
atexit.register(_wake_all)


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
    if job == "situation":                               # 2.73: the market situation picker (lab/situation.py)
        from . import situation
        return situation.learn(data_dir, log=log, should_stop=should_stop)
    if job == "rider":                                   # 2.87: Trend Rider on every Lab day, graded by days (lab/rider_lab.py)
        from . import rider_lab
        return rider_lab.run(data_dir, log=log, should_stop=should_stop, syms=kw.get("syms"), out_dir=kw.get("out_dir"))
    if job == "radar_indices":                           # 2.67: the Daily Radar for every other index (radar/indices.py)
        from radar import indices
        from radar.config import load_config
        return indices.run_job(load_config(), mode=kw.get("mode", "train"), keys=kw.get("keys"), demo=bool(kw.get("demo")),
                               log=log, should_stop=should_stop)
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


def run(job: str, data_dir, log=print, should_stop=lambda: False, child: bool = True, nice: int = NICE, pause=None, **kw):
    """Run a Lab job (factory = one research round, mlstrat = model-based strategies) in a child process.
    pause (2.80, automatic work only): a check that is true while the market needs the Mac; the job waits / freezes."""
    if job not in JOBS:
        raise ValueError(f"unknown Lab job {job!r}")
    if pause is not None and _safe(pause):           # 2.80: never start automatic work in market hours
        log(f"{job}: waiting for the market to close (automatic Lab work pauses in market hours)")
        STATE.update(job=job, folder=str(data_dir), since=time.time(), paused=True, paused_at=time.time(), waiting=True)
        while _safe(pause):
            if should_stop():
                STATE.clear()
                raise RuntimeError("Stopped while waiting for the market to close.")
            time.sleep(30)
        STATE.clear()
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
                return _child(job, data_dir, log, should_stop, nice, kw, pause)
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


def _safe(fn) -> bool:
    try:
        return bool(fn())
    except Exception:
        return False


def _child(job, data_dir, log, should_stop, nice, kw, pause=None):
    spec = json.dumps({"job": job, "data_dir": str(data_dir), "kw": kw}, default=str)
    env = {**os.environ, "PYTHONWARNINGS": "ignore", "PYTHONUNBUFFERED": "1"}
    pre = (lambda: lower_priority(nice)) if nice else None
    try:
        p = subprocess.Popen([sys.executable, "-u", "-m", "lab.proc", spec], cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, preexec_fn=pre)
    except Exception as e:
        raise _NoChild(str(e) or type(e).__name__)
    done = threading.Event()
    if pause is not None:
        STATE.update(job=job, folder=str(data_dir), since=time.time(), paused=False, paused_s=0.0, waiting=False)
    frozen = [None]                                      # 2.80: when the child was frozen for market hours

    def freeze(on: bool):
        sig = getattr(signal, "SIGSTOP" if on else "SIGCONT", None)
        if sig is None:                                  # (not a Mac / Linux: nothing to freeze with)
            return
        try:
            os.kill(p.pid, sig)
        except Exception:
            return
        if on:
            _FROZEN.add(p.pid)
            frozen[0] = time.time()
            STATE.update(paused=True, paused_at=frozen[0])
            log(f"{job}: paused for market hours (nothing lost: it continues after the close)")
        else:
            _FROZEN.discard(p.pid)
            STATE.update(paused=False, paused_s=round(STATE.get("paused_s", 0.0) + time.time() - (frozen[0] or time.time()), 1))
            log(f"{job}: continuing after the close (paused {(time.time() - (frozen[0] or time.time())) / 60:.0f} min)")
            frozen[0] = None

    def watch():                                         # Stop: ask nicely, then end it
        asked = None
        while not done.wait(1.0):
            if p.poll() is not None:
                return
            if pause is not None and asked is None:      # 2.80: frozen while the market needs the Mac
                want = _safe(pause)
                if want and frozen[0] is None:
                    freeze(True)
                elif not want and frozen[0] is not None:
                    freeze(False)
            if asked is None and should_stop():
                asked = time.time()
                if frozen[0] is not None:                # a frozen child can't hear SIGTERM: wake it first
                    freeze(False)
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
        _FROZEN.discard(p.pid)
        if pause is not None:
            STATE.clear()
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
