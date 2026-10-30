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
    # live scanner
    "live.source": str,
    "live.upstox_api_key": str,
    "live.upstox_api_secret": str,
    "live.auto_start": bool,
    "live.sim_speed": float,
    "live.telegram_min_severity": int,
    "live.news": bool,
    "live.paper.auto": bool,
    "live.paper.lots": int,
    "live.paper.target_pct": float,
    "live.paper.stop_pct": float,
    "live.paper.capital": float,
    "live.paper.risk_pct": float,
    "live.paper.max_daily_loss_pct": float,
    "live.paper.max_trades_day": int,
    "live.filings": bool,
    "live.briefs": bool,
    "live.mute_hit_rate": float,
    "lab.dhan_client_id": str,
    "lab.dhan_token": str,
    "lab.capital": float,
    "lab.risk_pct": float,
    "lab.daily_stop_pct": float,
    "lab.years": int,
    "lab.auto_trade": bool,
    "ai.provider": str,
    "ai.api_key": str,
    "ai.model": str,
    "ai.pulse_every_min": int,
    "ai.explain_min_severity": int,
    "ai.brain_every_min": int,
    "ai.brain_telegram": int,
    "ai.pro_model": str,
    "ai.pro_auto_per_day": int,
    "ai.daily_limit": int,
}
SECRET_KEYS = {"telegram.bot_token", "live.upstox_api_secret", "ai.api_key", "lab.dhan_token"}


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
