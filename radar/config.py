"""Config = config.yaml (defaults) + user_settings.json (changes made in the app)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
USER_SETTINGS = ROOT / "user_settings.json"

# Settings the app is allowed to change, as dotted paths into the config
EDITABLE = {
    "labels.big_move_quantile": float,
    "model.flag_rate": float,
    "news.use_gdelt": bool,
    "schedule.enabled": bool,
    "schedule.time": str,
    "schedule.weekly_retrain": bool,
    "telegram.bot_token": str,
    "telegram.chat_id": str,
}


def _set(d: dict, dotted: str, value):
    keys = dotted.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def _get(d: dict, dotted: str):
    for k in dotted.split("."):
        d = d.get(k, {}) if isinstance(d, dict) else {}
    return d if d != {} else None


def read_user_settings() -> dict:
    if USER_SETTINGS.exists():
        try:
            return json.loads(USER_SETTINGS.read_text())
        except Exception:
            return {}
    return {}


def save_user_settings(changes: dict) -> dict:
    current = read_user_settings()
    for k, v in changes.items():
        if k not in EDITABLE:
            raise ValueError(f"Setting '{k}' can't be changed from the app")
        current[k] = EDITABLE[k](v) if v is not None else v
    USER_SETTINGS.write_text(json.dumps(current, indent=2))
    return current


def editable_values(cfg: dict) -> dict:
    return {k: _get(cfg, k) for k in EDITABLE}


def load_config(path: str | None = None) -> dict:
    cfg_path = Path(path) if path else ROOT / "config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg = copy.deepcopy(cfg)
    for k, v in read_user_settings().items():
        if k in EDITABLE:
            _set(cfg, k, v)
    for key in ("cache_dir", "output_dir", "model_dir"):
        p = ROOT / cfg[key]
        p.mkdir(parents=True, exist_ok=True)
        cfg[key] = str(p)
    if cfg.get("events_csv"):
        cfg["events_csv"] = str(ROOT / cfg["events_csv"])
    if cfg.get("news", {}).get("headlines_csv"):
        cfg["news"]["headlines_csv"] = str(ROOT / cfg["news"]["headlines_csv"])
    (ROOT / "data").mkdir(exist_ok=True)
    return cfg
