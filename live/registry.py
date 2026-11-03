"""The bullet catalogue: every kind of alert, setup, chart pattern and strategy the app can produce, in one place.

Each entry says what it is called, its family, how long the same alert stays quiet (cooldown, minutes), whether the
paper account takes it automatically, and a few display details. The engine, the detectors, the chart scanner, the
paper account and the screens read these tables instead of keeping their own copies.

Adding a new alert / setup / pattern: write its detection code (a bullet) and add one line here. No engine (gun) edit.
A kind that isn't listed still works (default cooldown, not auto-paper-traded, its raw name as the label).

This module imports nothing from the app, so any part can read it.
"""
from __future__ import annotations

KINDS = {
    "desk": {"family": "desk", "label": "Strategy desk"},
    "absorption": {"family": "detector", "label": "Absorption", "cooldown": 20},
    "beyond_priced": {"family": "detector", "label": "Bigger than priced", "cooldown": 999, "paper": True},
    "big_print": {"family": "detector", "label": "Big trade", "cooldown": 10},
    "day_high": {"family": "detector", "label": "New day high", "cooldown": 30},
    "day_low": {"family": "detector", "label": "New day low", "cooldown": 30},
    "gamma_flip": {"family": "detector", "label": "Gamma flip", "cooldown": 30, "any_direction": True},
    "iv_spike": {"family": "detector", "label": "IV spike", "cooldown": 30},
    "oi_buildup": {"family": "detector", "label": "Futures OI buildup", "cooldown": 60, "paper": True},
    "oi_wall": {"family": "detector", "label": "OI wall shift", "cooldown": 20, "any_direction": True},
    "orb": {"family": "detector", "label": "Opening-range breakout", "cooldown": 999, "paper": True},
    "order_flow": {"family": "detector", "label": "Order flow", "cooldown": 15},
    "pcr_shift": {"family": "detector", "label": "PCR shift", "cooldown": 60},
    "sharp_move": {"family": "detector", "label": "Sharp move", "paper": True},
    "unusual_options": {"family": "detector", "label": "Unusual options", "cooldown": 10, "paper": True},
    "vix_drop": {"family": "detector", "label": "VIX drop"},
    "vix_jump": {"family": "detector", "label": "VIX jump"},
    "volume_surge": {"family": "detector", "label": "Volume surge", "paper": True},
    "vwap_cross": {"family": "detector", "label": "VWAP cross", "cooldown": 30, "any_direction": True},
    "filing": {"family": "info", "label": "Company filing", "cooldown": 5},
    "flat_by": {"family": "info", "label": "Square-off reminder", "cooldown": 999},
    "insider": {"family": "info", "label": "Insider trade", "cooldown": 5},
    "macro": {"family": "info", "label": "Global / macro", "cooldown": 30},
    "news": {"family": "info", "label": "News", "cooldown": 60},
    "factory": {"family": "lab", "label": "Strategy Lab rule", "paper": True},
    "manual": {"family": "manual", "label": "By hand"},
    "paper": {"family": "manual", "label": "Paper"},
    "pattern_breakout_retest": {"family": "pattern", "label": "Pattern: breakout + retest", "name": "Breakout + retest", "cooldown": 45, "noisy_for_stocks": True},
    "pattern_candle_at_level": {"family": "pattern", "label": "Pattern: candle at level", "name": "Reversal candle at level", "cooldown": 30, "noisy_for_stocks": True},
    "pattern_double_bottom": {"family": "pattern", "label": "Pattern: double bottom", "name": "Double bottom", "cooldown": 60},
    "pattern_double_top": {"family": "pattern", "label": "Pattern: double top", "name": "Double top", "cooldown": 60},
    "pattern_flag": {"family": "pattern", "label": "Pattern: flag", "name": "Flag breakout", "cooldown": 30, "noisy_for_stocks": True},
    "pattern_head_shoulders": {"family": "pattern", "label": "Pattern: head & shoulders", "name": "Head & shoulders", "cooldown": 90},
    "pattern_inv_hs": {"family": "pattern", "label": "Pattern: inverse H&S", "name": "Inverse head & shoulders", "cooldown": 90},
    "pattern_rsi_divergence": {"family": "pattern", "label": "Pattern: RSI divergence", "name": "RSI divergence", "cooldown": 45},
    "pattern_triangle": {"family": "pattern", "label": "Pattern: triangle", "name": "Triangle breakout", "cooldown": 45},
    "setup_cpr_breakout": {"family": "setup", "label": "Setup: CPR breakout", "name": "CPR breakout", "cooldown": 999, "short": "CPR"},
    "setup_ema_pullback": {"family": "setup", "label": "Setup: EMA pullback", "name": "EMA pullback", "cooldown": 45, "short": "EMA"},
    "setup_pdh_break": {"family": "setup", "label": "Setup: prev-day break", "name": "Previous-day high/low break", "cooldown": 999, "short": "PDH/L"},
    "setup_vwap_reclaim": {"family": "setup", "label": "Setup: VWAP reclaim", "name": "VWAP reclaim", "cooldown": 45, "short": "VWAP"},
    "setup_wall_fade": {"family": "setup", "label": "Setup: OI wall rejection", "name": "OI wall rejection", "cooldown": 45, "short": "Wall"},}


def get(kind: str) -> dict:
    return KINDS.get(kind) or {"family": "other", "label": kind}


def label(kind: str) -> str:
    return get(kind)["label"]


def cooldowns() -> dict:
    return {k: v["cooldown"] for k, v in KINDS.items() if v.get("cooldown") is not None}


def any_direction() -> set:
    return {k for k, v in KINDS.items() if v.get("any_direction")}


def paper_kinds() -> list:
    """Kinds the paper account takes automatically by default (your Paper settings can change the list)."""
    return [k for k, v in KINDS.items() if v.get("paper")]


def names(family: str) -> dict:
    """setup / pattern names without the prefix, e.g. names("setup")["ema_pullback"] == "EMA pullback"."""
    pre = family + "_"
    return {k[len(pre):]: v["name"] for k, v in KINDS.items() if v["family"] == family and k.startswith(pre) and v.get("name")}


def shorts() -> dict:
    return {k[len("setup_"):]: v["short"] for k, v in KINDS.items() if v.get("short")}


def noisy_for_stocks() -> set:
    return {k[len("pattern_"):] for k, v in KINDS.items() if v.get("noisy_for_stocks")}


def view() -> list:
    """For the screens: [{kind, family, label, cooldown, paper, ...}]."""
    return [{"kind": k, **v} for k, v in KINDS.items()]
