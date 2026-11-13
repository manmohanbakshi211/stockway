"""Secrets on this machine only: <data_dir>/secrets/<name>.json, folder 0700, files 0600, never in git (data/tanu/ is
ignored), never logged, never sent anywhere but the service they belong to, never shown back in the app (the app
only says whether a secret is saved). Mandeep types them into the app on the Mac; they never pass through chat."""
from __future__ import annotations

import json
import os
from pathlib import Path


class Secrets:
    def __init__(self, base: Path | str):
        self.dir = Path(base) / "secrets"

    def _path(self, name: str) -> Path:
        if not name.replace("_", "").isalnum():
            raise ValueError("bad secret name")
        return self.dir / f"{name}.json"

    def save(self, name: str, value: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        p = self._path(name)
        tmp = p.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(value, f)
        os.chmod(tmp, 0o600)
        tmp.replace(p)

    def load(self, name: str) -> dict | None:
        p = self._path(name)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except Exception:
            return None

    def has(self, name: str) -> bool:
        return self.load(name) is not None

    def delete(self, name: str) -> None:
        p = self._path(name)
        if p.exists():
            p.unlink()
