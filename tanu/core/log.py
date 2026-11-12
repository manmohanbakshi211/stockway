"""Structured logs: one JSON object per line, so logs can be searched, counted and replayed.

Every line has: ts (ISO, IST), level, run (one id per process start), comp (component), event (a short snake_case
name) and any extra fields. Files: <data_dir>/logs/tanu-YYYY-MM-DD.jsonl, one per day, older than log_keep_days
removed. The console gets a short human line. Never log secrets: values of keys named like token / secret / key /
password / pin are replaced with "***".
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import sys
import threading
import uuid
from pathlib import Path

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
RUN_ID = uuid.uuid4().hex[:8]
_SECRET_WORDS = ("token", "secret", "password", "passwd", "api_key", "apikey", "pin", "totp")
_lock = threading.Lock()
_state: dict = {"dir": None, "level": logging.INFO, "keep": 30}


def _clean(fields: dict) -> dict:
    out = {}
    for k, v in fields.items():
        if any(w in k.lower() for w in _SECRET_WORDS):
            out[k] = "***"
        elif isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        else:
            try:
                json.dumps(v)
                out[k] = v
            except TypeError:
                out[k] = repr(v)
    return out


def setup(log_dir: Path | str, level: str = "INFO", keep_days: int = 30) -> None:
    d = Path(log_dir)
    d.mkdir(parents=True, exist_ok=True)
    _state.update(dir=d, level=getattr(logging, level.upper(), logging.INFO), keep=keep_days)
    _prune()


def _prune() -> None:
    d = _state["dir"]
    if d is None:
        return
    cutoff = (dt.datetime.now(IST).date() - dt.timedelta(days=_state["keep"])).isoformat()
    for f in d.glob("tanu-*.jsonl"):
        if f.stem[5:] < cutoff:
            f.unlink(missing_ok=True)


class Logger:
    def __init__(self, comp: str):
        self.comp = comp

    def _emit(self, level: int, event: str, fields: dict) -> dict:
        now = dt.datetime.now(IST)
        rec = {"ts": now.isoformat(timespec="milliseconds"), "level": logging.getLevelName(level), "run": RUN_ID,
               "comp": self.comp, "event": event, **_clean(fields)}
        if level < _state["level"]:
            return rec
        d = _state["dir"]
        if d is not None:
            line = json.dumps(rec, ensure_ascii=False)
            with _lock:
                with open(d / f"tanu-{now.date().isoformat()}.jsonl", "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        extra = " ".join(f"{k}={v}" for k, v in rec.items() if k not in ("ts", "level", "run", "comp", "event"))
        print(f"{now:%H:%M:%S} {rec['level'][:4]} {self.comp}: {event} {extra}".rstrip(), file=sys.stderr)
        return rec

    def debug(self, event: str, **f) -> dict:
        return self._emit(logging.DEBUG, event, f)

    def info(self, event: str, **f) -> dict:
        return self._emit(logging.INFO, event, f)

    def warning(self, event: str, **f) -> dict:
        return self._emit(logging.WARNING, event, f)

    def error(self, event: str, **f) -> dict:
        return self._emit(logging.ERROR, event, f)


def get(comp: str) -> Logger:
    return Logger(comp)
