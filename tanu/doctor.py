"""`python -m tanu doctor`: is this machine ready to run Tanu? Each check returns ok / warn / fail with a plain reason.
Read-only except creating Tanu's own folders under data_dir."""
from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path

from .core import config, paths

PACKAGES = ("numpy", "pandas", "pyarrow", "pydantic", "fastapi", "websocket", "google.protobuf")


def checks(s: config.Settings | None = None) -> list[dict]:
    out = []

    def add(name, status, why):
        out.append({"check": name, "status": status, "why": why})

    v = sys.version_info
    add("python", "ok" if v >= (3, 11) else "fail", f"Python {v.major}.{v.minor} (needs 3.11+)")
    try:
        s = s or config.load()
        add("settings", "ok", f"data_dir={s.data_dir}, log_level={s.log_level}")
    except Exception as e:
        add("settings", "fail", f"settings are invalid: {e}")
        return out
    missing = []
    for p in PACKAGES:
        try:
            importlib.import_module(p)
        except Exception:
            missing.append(p)
    add("packages", "fail" if missing else "ok", f"missing: {', '.join(missing)}" if missing else "all present")
    try:
        d = paths.ensure(s)
        probe = d["base"] / ".write_probe"
        probe.write_text("ok")
        probe.unlink()
        add("data folder", "ok", str(d["base"]))
    except Exception as e:
        add("data folder", "fail", f"cannot write: {e}")
        return out
    free = shutil.disk_usage(s.data_path).free / 1e9
    add("disk", "ok" if free >= s.min_free_disk_gb else "warn", f"{free:.0f} GB free (wants {s.min_free_disk_gb:g}+)")
    legacy = paths.LEGACY_DATA
    have = [n for n in ("lab", "lab5y", "recordings", "history", "live.db") if (legacy / n).exists()]
    add("legacy data (read-only)", "ok" if have else "warn", ", ".join(have) if have else "none found on this machine")
    return out


def main() -> int:
    rows = checks()
    w = max(len(r["check"]) for r in rows)
    for r in rows:
        mark = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}[r["status"]]
        print(f"{mark}  {r['check']:<{w}}  {r['why']}")
    return 1 if any(r["status"] == "fail" for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
