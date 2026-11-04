"""One counter and one speed limit for every Dhan Data API call the app makes (5-year download, news, movers, tests).

Dhan allows 5 requests a second and 100,000 a day on the Data APIs (Dhan docs, "Rate Limits"). All Dhan clients in
the app share this, so the download and the live news / movers never push each other over the limit:
  - speed: at most RATE calls a second in total (a little under 5, to stay clear of Dhan's edge);
  - day: the 5-year download stops at DOWNLOAD_CAP calls, leaving the rest of the 100,000 for live news and movers.
The count is kept in data/dhan_calls.json per calendar day (it resets at midnight).
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
from pathlib import Path

RATE = 4.6                    # calls per second, all Dhan clients together
DAY_LIMIT = 100_000           # Dhan's own daily limit for Data APIs
DOWNLOAD_CAP = 92_000         # the download stops here; the last 8,000 stay for live news / movers

_lock = threading.Lock()
_last = 0.0
_file: Path | None = None
_day: str | None = None
_used = 0
_since_save = 0


def setup(path):
    """Where the daily count lives (data/dhan_calls.json). Older per-download counts are honoured for today."""
    global _file, _day, _used
    _file = Path(path)
    today = dt.date.today().isoformat()
    best = 0
    for f in (_file, _file.parent / "lab5y" / "dhan_calls.json"):
        try:
            best = max(best, int(json.loads(f.read_text()).get(today, 0)))
        except Exception:
            pass
    with _lock:
        _day, _used = today, max(best, _used if _day == today else 0)


PRIO_HOLD = 2.0                # background calls wait this long after a screen / news / movers call (2.38)
_prio_waiting = 0
_prio_last = 0.0
_bg_waits = 0                  # times the background download gave way (System page)


def wait(min_gap: float = 0.0, priority: bool = True):
    """Block until another call is allowed (shared speed limit). Each caller books the next free slot and sleeps
    outside the lock (2.38), so callers queue in order instead of whoever grabs the lock. priority=False (the 5-year
    download) gives way: it waits while a priority call (a chart, news, movers) is waiting or ran in the last
    PRIO_HOLD seconds, so a screen's few calls never queue behind thousands of download calls."""
    global _last, _prio_waiting, _prio_last, _bg_waits
    if priority:
        with _lock:
            _prio_waiting += 1
    try:
        gave_way = False
        while True:
            with _lock:
                now = time.time()
                nxt = _last + max(1.0 / RATE, min_gap)
                if not priority and (_prio_waiting > 0 or now - _prio_last < PRIO_HOLD):
                    slot = None
                    if not gave_way:
                        gave_way = True
                        _bg_waits += 1
                elif not priority and nxt > now + 0.05:
                    slot = None                         # background never books slots ahead: a screen's call gets the next one
                else:
                    slot = max(_last + max(1.0 / RATE, min_gap), now)
                    _last = slot
            if slot is not None:
                break
            time.sleep(0.05)
        d = slot - time.time()
        if d > 0:
            time.sleep(d)
    finally:
        if priority:
            with _lock:
                _prio_waiting -= 1
                _prio_last = time.time()


def gave_way() -> int:
    return _bg_waits


def spend(n: int = 1):
    global _day, _used, _since_save
    today = dt.date.today().isoformat()
    with _lock:
        if _day != today:
            _day, _used = today, 0
        _used += n
        _since_save += n
        save = _since_save >= 25
        if save:
            _since_save = 0
    if save:
        _save()


def used() -> int:
    today = dt.date.today().isoformat()
    return _used if _day == today else 0


def _save():
    if not _file:
        return
    try:
        tmp = _file.with_suffix(".tmp")
        tmp.write_text(json.dumps({_day: _used}))
        tmp.replace(_file)
    except Exception:
        pass


def flush():
    _save()
