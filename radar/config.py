"""Config = config.yaml (defaults) + user_settings.json (changes made in the app)."""
from __future__ import annotations

import copy
import json
import re
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
    "live.paper.aggressive": bool,
    "live.paper.max_lots": int,
    "live.paper.max_open": int,
    "live.paper.profit_lock": float,
    "live.filings": bool,
    "live.briefs": bool,
    "live.mute_hit_rate": float,
    "lab.dhan_client_id": str,
    "lab.dhan_token": str,
    "lab.dhan_api_key": str,
    "lab.dhan_api_secret": str,
    "lab.capital": float,
    "lab.risk_pct": float,
    "lab.daily_stop_pct": float,
    "lab.years": int,
    "lab.auto_trade": bool,
    "lab.stock_rules_tested_only": bool,
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
    "ai.language": str,
}
SECRET_KEYS = {"telegram.bot_token", "live.upstox_api_secret", "ai.api_key", "lab.dhan_token", "lab.dhan_api_secret"}


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


# 2.49: settings whose names come from a bullet file (an extra paper account's capital, rules/paper_books.json), so a
# new account needs no edit here
EDITABLE_PATTERNS = [(re.compile(r"^live\.books\.[a-z0-9_]{1,24}\.capital$"), float)]


def _caster(k: str):
    if k in EDITABLE:
        return EDITABLE[k]
    for rx, f in EDITABLE_PATTERNS:
        if rx.match(k):
            return f
    return None


def save_user_settings(changes: dict) -> dict:
    current = read_user_settings()
    for k, v in changes.items():
        f = _caster(k)
        if f is None:
            raise ValueError(f"Setting '{k}' can't be changed from the app")
        current[k] = f(v) if v is not None else v
    USER_SETTINGS.write_text(json.dumps(current, indent=2))
    return current


def editable_values(cfg: dict) -> dict:
    return {k: _get(cfg, k) for k in EDITABLE}


_PARSED: dict = {}          # 2.38: file -> ((mtime, size), parsed): read again only when the file changes


def _cached(f: Path, parse):
    try:
        st = f.stat()
        sig = (st.st_mtime_ns, st.st_size)
    except FileNotFoundError:
        return None
    hit = _PARSED.get(str(f))
    if hit and hit[0] == sig:
        return hit[1]
    val = parse(f)
    _PARSED[str(f)] = (sig, val)
    return val


def _user_settings_cached() -> dict:
    try:
        return _cached(USER_SETTINGS, lambda f: json.loads(f.read_text())) or {}
    except Exception:
        return {}


def load_config(path: str | None = None) -> dict:
    """config.yaml + your settings. 2.38: the parsed files are kept until they change (the app asks for its settings
    many times a second; parsing the YAML each time cost ~30 ms a call). Every call still gets its own copy."""
    cfg_path = Path(path) if path else ROOT / "config.yaml"

    def _yaml(f):
        with open(f, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    cfg = copy.deepcopy(_cached(cfg_path, _yaml))
    if cfg is None:
        raise FileNotFoundError(str(cfg_path))
    for k, v in copy.deepcopy(_user_settings_cached()).items():
        if _caster(k) is not None:
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
