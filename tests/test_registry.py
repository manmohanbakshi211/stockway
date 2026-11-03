"""The bullet catalogue (live/registry.py) gives exactly the tables the app used before it existed, and every alert
kind the code can produce is listed in it."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BEFORE = {'COOLDOWN': {'orb': 999, 'day_high': 30, 'day_low': 30, 'vwap_cross': 30, 'oi_wall': 20, 'pcr_shift': 60, 'iv_spike': 30, 'beyond_priced': 999, 'oi_buildup': 60, 'news': 60, 'unusual_options': 10, 'order_flow': 15, 'absorption': 20, 'gamma_flip': 30, 'big_print': 10, 'filing': 5, 'setup_ema_pullback': 45, 'setup_vwap_reclaim': 45, 'setup_cpr_breakout': 999, 'setup_pdh_break': 999, 'setup_wall_fade': 45, 'macro': 30, 'pattern_double_top': 60, 'pattern_double_bottom': 60, 'pattern_head_shoulders': 90, 'pattern_inv_hs': 90, 'pattern_triangle': 45, 'pattern_flag': 30, 'pattern_breakout_retest': 45, 'flat_by': 999, 'insider': 5, 'pattern_rsi_divergence': 45, 'pattern_candle_at_level': 30}, 'NO_DIR': ['gamma_flip', 'oi_wall', 'vwap_cross'], 'PAPER': ['beyond_priced', 'factory', 'oi_buildup', 'orb', 'sharp_move', 'unusual_options', 'volume_surge'], 'SETUPS': {'ema_pullback': 'EMA pullback', 'vwap_reclaim': 'VWAP reclaim', 'cpr_breakout': 'CPR breakout', 'pdh_break': 'Previous-day high/low break', 'wall_fade': 'OI wall rejection'}, 'PATTERNS': {'double_top': 'Double top', 'double_bottom': 'Double bottom', 'head_shoulders': 'Head & shoulders', 'inv_hs': 'Inverse head & shoulders', 'triangle': 'Triangle breakout', 'flag': 'Flag breakout', 'breakout_retest': 'Breakout + retest', 'rsi_divergence': 'RSI divergence', 'candle_at_level': 'Reversal candle at level'}, 'SHORT': {'ema_pullback': 'EMA', 'vwap_reclaim': 'VWAP', 'cpr_breakout': 'CPR', 'pdh_break': 'PDH/L', 'wall_fade': 'Wall'}, 'NOISY': ['breakout_retest', 'candle_at_level', 'flag']}


def test_tables_identical_to_before():
    """Every entry the app had before the registry is unchanged. New bullets may add entries (that is what the registry
    is for), but only kinds listed in it, and a new kind is never auto-paper-traded while it is on trial."""
    from live import chartdata, detectors, paper, patterns, registry, setups
    now = {"COOLDOWN": detectors.COOLDOWN, "NO_DIR": sorted(detectors.NO_DIRECTION_COOLDOWN), "PAPER": sorted(paper.DEFAULTS["kinds"]),
           "SETUPS": setups.NAMES, "PATTERNS": patterns.NAMES, "SHORT": chartdata.SHORT, "NOISY": sorted(chartdata.NOISY_FOR_STOCKS)}
    for name, old in BEFORE.items():
        cur = now[name]
        if isinstance(old, dict):
            assert {k: cur.get(k) for k in old} == old, name
            assert all(k in registry.KINDS or f"setup_{k}" in registry.KINDS or f"pattern_{k}" in registry.KINDS
                       for k in set(cur) - set(old)), name
        else:
            assert set(old) <= set(cur), name
    assert sorted(now["PAPER"]) == BEFORE["PAPER"]                   # nothing new trades on paper by itself
    assert now["SETUPS"] == BEFORE["SETUPS"] and now["PATTERNS"] == BEFORE["PATTERNS"]
    assert set(now["COOLDOWN"]) - set(BEFORE["COOLDOWN"]) <= set(registry.shadow_kinds())   # 2.27: candle ideas on trial


def test_every_produced_kind_is_registered():
    from live import registry
    kinds = set()
    for p in list((ROOT / "live").glob("*.py")) + list((ROOT / "lab").glob("*.py")):
        s = p.read_text()
        kinds |= set(re.findall(r'_a\(\s*m\s*,\s*"([a-z_]+)"', s))
        kinds |= set(re.findall(r'"kind"\s*:\s*"([a-z_]+)"', s))
    for fam in ("setup", "pattern", "candle"):
        kinds |= {fam + "_" + k for k in registry.names(fam)}
    kinds -= {"c", "setup_", "pattern_", "candle_"}       # a field name in a fixture; prefixes joined with a name in code
    missing = sorted(k for k in kinds if k not in registry.KINDS)
    assert not missing, missing


def test_ui_has_a_label_for_every_kind():
    from live import registry
    js = (ROOT / "webapp" / "static" / "live.js").read_text()
    assert "/api/registry" in js and all(v["label"] for v in registry.KINDS.values())
