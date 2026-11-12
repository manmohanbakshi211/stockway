"""Where Tanu keeps its files. Everything Tanu writes is under settings.data_dir (default data/tanu/), never in the
legacy app's folders. Legacy data (data/lab, data/recordings, data/history, data/live.db) is read-only to Tanu and
is copied in by the import step (T1), never edited in place."""
from __future__ import annotations

from pathlib import Path

from .config import ROOT, Settings

LEGACY_DATA = ROOT / "data"


def ensure(s: Settings) -> dict[str, Path]:
    base = s.data_path
    dirs = {"base": base, "logs": base / "logs", "tape": base / "tape", "bars": base / "bars", "store": base / "store",
            "imports": base / "imports"}
    for p in dirs.values():
        p.mkdir(parents=True, exist_ok=True)
    return dirs
