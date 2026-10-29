"""Edge study helpers and the live 'what history says' line."""
import json

import numpy as np

from lab.edge import har_forecast, setup_table
from live.edgebook import EdgeBook


def test_har_and_table():
    assert abs(har_forecast(10, 10, 10) - 9.9) < 1e-9
    import pandas as pd
    sig = pd.DataFrame({"sym": ["NIFTY"] * 6, "setup": ["ema_pullback"] * 6, "dir": [1] * 6,
                        "pnl": [100, -50, 200, -50, 100, -50], "exit_reason": ["target", "stop"] * 3, "hold_min": [10] * 6})
    t = setup_table(sig).iloc[0]
    assert t["n"] == 6 and round(t["win_pct"]) == 50 and t["avg_rs"] == 41.666666666666664 and t["pf"] == 400 / 150


def test_edgebook_verdict(tmp_path):
    (tmp_path / "lab").mkdir()
    rep = {"period": {"from": "2024-10-01", "to": "2026-10-27"},
           "setups": [{"sym": "NIFTY", "setup": "vwap_reclaim", "dir": 1, "n": 150, "win_pct": 29, "avg_rs": -207, "t": -1.2},
                      {"sym": "NIFTY", "setup": "cpr_breakout", "dir": -1, "n": 80, "win_pct": 49, "avg_rs": 760, "t": 2.3}]}
    (tmp_path / "lab" / "meta.json").write_text(json.dumps(rep))
    eb = EdgeBook(tmp_path)
    v = eb.verdict("NIFTY", "vwap_reclaim", "up", {})
    assert v["grade"] == "losing" and "info only" in v["line"] and "−₹207" in v["line"]
    assert eb.verdict("NIFTY", "cpr_breakout", "down", {})["grade"] == "edge"
    assert eb.verdict("RELIANCE", "cpr_breakout", "down", {})["line"] is None
