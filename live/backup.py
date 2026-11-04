"""Daily safe copy of the app's databases (gun, 2.42).

The app's own record (alerts and their grades, paper and test-bench trades, next-candle and candle-forecast grades,
the stored readings) lives in two SQLite files, live.db and bench.db, and nowhere else. Once a day after the close
(after 16:30, while the scanner is not running) each is copied with SQLite's own backup (safe while the app keeps
writing), checked (quick_check), and kept for `keep` days in data/backups/<day>/. The folder can be moved with
rules/backup.json "dir" (an iCloud Drive or external-disk folder keeps a copy off this disk). Token, key and settings
files are never copied. Read-only towards brokers: nothing here talks to a broker.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from .clock import IST

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "rules" / "backup.json"
DEFAULTS = {"on": True, "after": "16:30", "keep": 7, "dir": None, "files": ["live.db", "bench.db"], "min_free_gb": 5}
NEVER = ("token", "dhan_", "user_settings", "keychain", ".env")
LAST: dict = {"at": None, "ok": None, "dir": None, "files": {}, "error": None, "running": False}
_LOCK = threading.Lock()
_C: dict = {}


def cfg() -> dict:
    try:
        mt = FILE.stat().st_mtime
        if _C.get("mt") != mt:
            raw = json.loads(FILE.read_text())
            _C.update(mt=mt, cfg={**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}, error=None)
    except FileNotFoundError:
        _C.update(cfg=dict(DEFAULTS), error=None)
    except Exception as e:
        _C.update(cfg=_C.get("cfg") or dict(DEFAULTS), error=str(e)[:160])
    return _C["cfg"]


def target(data_dir, c: dict | None = None) -> Path:
    c = c or cfg()
    return Path(c["dir"]).expanduser() if c.get("dir") else Path(data_dir) / "backups"


def run(data_dir, day: str | None = None, log=print) -> dict:
    """Copy each database now (SQLite online backup), check it, write done.json, drop copies older than `keep` days."""
    c = cfg()
    day = day or dt.datetime.now(IST).date().isoformat()
    base = target(data_dir, c)
    out_dir = base / day
    out_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(out_dir).free / 1e9
    need = sum((Path(data_dir) / f).stat().st_size for f in c["files"] if (Path(data_dir) / f).exists()) / 1e9
    if free - need < float(c["min_free_gb"]):
        raise RuntimeError(f"only {free:.1f} GB free: backup skipped (keeps {c['min_free_gb']} GB for the app)")
    res = {}
    for name in c["files"]:
        if any(x in name.lower() for x in NEVER):
            continue
        src = Path(data_dir) / name
        if not src.exists():
            continue
        dst = out_dir / name
        tmp = out_dir / (name + ".tmp")
        t0 = time.time()
        s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
        d = sqlite3.connect(str(tmp))
        try:
            s.backup(d, pages=4096, sleep=0.01)
            ok = d.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            d.close()
            s.close()
        if not ok:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"{name}: the copy failed its check (kept the earlier copies)")
        tmp.replace(dst)
        res[name] = {"mb": round(dst.stat().st_size / 1e6, 1), "secs": round(time.time() - t0, 1)}
    (out_dir / "done.json").write_text(json.dumps({"at": time.time(), "files": res}))
    kept = sorted(p for p in base.iterdir() if p.is_dir() and len(p.name) == 10 and (p / "done.json").exists())
    for old in kept[:-int(c["keep"])]:
        shutil.rmtree(old, ignore_errors=True)
    log(f"Backup saved to {out_dir} ({', '.join(str(k) + ' ' + str(v['mb']) + ' MB' for k, v in res.items())})")
    return res


def tick(engine, now: dt.datetime | None = None) -> bool:
    """Called by the watcher loop: once a day after `after`, outside the session, in its own thread."""
    c = cfg()
    if not c.get("on", True) or LAST["running"]:
        return False
    now = now or dt.datetime.now(IST)
    hh, mm = map(int, str(c["after"]).split(":"))
    if now.time() < dt.time(hh, mm) or getattr(engine, "running", False):
        return False
    day = now.date().isoformat()
    if (target(engine.data_dir, c) / day / "done.json").exists() or LAST.get("tried") == day:
        return False
    if not _LOCK.acquire(blocking=False):
        return False
    LAST.update(running=True, tried=day)

    def go():
        try:
            r = run(engine.data_dir, day, log=getattr(engine, "log", print))
            LAST.update(at=time.time(), ok=True, files=r, error=None, dir=str(target(engine.data_dir, c) / day))
        except Exception as e:
            LAST.update(at=time.time(), ok=False, error=str(e)[:200])
            try:
                engine.log(f"Backup failed: {e}")
            except Exception:
                pass
        finally:
            LAST["running"] = False
            _LOCK.release()
    threading.Thread(target=go, daemon=True, name="backup").start()
    return True


def status(data_dir) -> dict:
    """The latest copy on disk (System page)."""
    base = target(data_dir)
    days = sorted(p.name for p in base.iterdir() if p.is_dir() and (p / "done.json").exists()) if base.exists() else []
    last = None
    if days:
        try:
            last = {"day": days[-1], **json.loads((base / days[-1] / "done.json").read_text())}
        except Exception:
            last = {"day": days[-1]}
    return {"dir": str(base), "days": days, "last": last, "run": {k: v for k, v in LAST.items() if k != "tried"}}
