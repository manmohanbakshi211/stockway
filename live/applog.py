"""App log on disk (gun, 2.42): every engine log line also goes to data/logs/app.log (5 MB, 10 files kept), with
anything that looks like a token or key masked. Before this the log lived only in the Terminal window and the last
400 lines in memory, so the reason for a crash or a restart was lost."""
from __future__ import annotations

import logging
import re
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOCK = threading.Lock()
_LOGGERS: dict = {}
_MASK = [re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"),          # JWT access tokens
         re.compile(r"(?i)(access[_-]?token|api[_-]?key|secret|password|pin|totp|bearer)(\W{1,3})[A-Za-z0-9_\-\.]{6,}")]


def mask(line: str) -> str:
    line = _MASK[0].sub("<token>", line)
    return _MASK[1].sub(lambda m: f"{m.group(1)}{m.group(2)}<hidden>", line)


def logger(data_dir) -> logging.Logger:
    key = str(Path(data_dir).resolve())
    with _LOCK:
        lg = _LOGGERS.get(key)
        if lg is None:
            d = Path(data_dir) / "logs"
            d.mkdir(parents=True, exist_ok=True)
            lg = logging.getLogger(f"stockway.app.{len(_LOGGERS)}")
            lg.setLevel(logging.INFO)
            lg.propagate = False
            h = RotatingFileHandler(d / "app.log", maxBytes=5_000_000, backupCount=10, encoding="utf-8")
            h.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d"))
            lg.addHandler(h)
            _LOGGERS[key] = lg
    return lg


def write(data_dir, line: str) -> None:
    logger(data_dir).info(mask(str(line)))


def tail(data_dir, n: int = 200) -> list:
    f = Path(data_dir) / "logs" / "app.log"
    try:
        return f.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except Exception:
        return []
