"""Settings: one typed object, loaded in a fixed order.

Order (later wins): defaults in this class -> tanu/settings.json (committed) -> <data_dir>/local.json (this machine,
not in git) -> environment variables TANU_<FIELD> (e.g. TANU_LOG_LEVEL=DEBUG). Unknown keys are rejected so a typo
fails loudly instead of being ignored. Secrets (broker tokens, keys) are never settings: they live in their own
token files, read only by the component that needs them.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

ROOT = Path(__file__).resolve().parents[2]          # the repository root
PKG = Path(__file__).resolve().parents[1]           # tanu/
DEFAULTS_FILE = PKG / "settings.json"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data_dir: str = "data/tanu"
    log_level: str = "INFO"
    log_keep_days: int = Field(30, ge=1, le=3650)
    min_free_disk_gb: float = Field(10, ge=0)
    timezone: str = "Asia/Kolkata"
    web_port: int = Field(8790, ge=1024, le=65535)     # 8765 (the Upstox redirect URL's port) only after cutover
    live: bool = False                                 # the cutover switch (ADR-0009): only `python -m tanu cutover` sets it
    legacy_port: int = Field(8765, ge=1024, le=65535)  # the legacy app's port: Tanu never goes live while it answers

    @field_validator("log_level")
    @classmethod
    def _level(cls, v: str) -> str:
        v = v.upper()
        if v not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            raise ValueError(f"log_level must be DEBUG, INFO, WARNING or ERROR, not {v!r}")
        return v

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        return p if p.is_absolute() else ROOT / p


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    raw.pop("about", None)
    return raw


def load(defaults_file: Path = DEFAULTS_FILE, env: dict | None = None) -> Settings:
    env = os.environ if env is None else env
    vals = _read(defaults_file)
    data_dir = Path(env.get("TANU_DATA_DIR") or vals.get("data_dir") or Settings.model_fields["data_dir"].default)
    local = (data_dir if data_dir.is_absolute() else ROOT / data_dir) / "local.json"
    vals.update(_read(local))
    for name in Settings.model_fields:
        v = env.get(f"TANU_{name.upper()}")
        if v is not None:
            vals[name] = v
    return Settings(**vals)
