"""2.57: strike choice for option-buying rules (Mandeep, 11 Nov 2026: "go ahead" on choosing strikes by premium).
A rule may name a strike (rules/strikes.json); without one it buys ATM exactly as before (ids and results unchanged)."""
import json
import random
import types

import numpy as np
import pandas as pd

from lab import factory as F


def test_settings_and_safe_fallback(tmp_path, monkeypatch):
    c = F.strikes_cfg()
    assert {"itm1", "otm1", "otm2", "lowprem"} <= set(c["choices"]) and "chand" in c["exits"] and "NIFTY" in c["syms"]
    bad = tmp_path / "s.json"
    bad.write_text("{not json")
    monkeypatch.setattr(F, "STRIKES_FILE", bad)
    assert F.strikes_cfg() == {"choices": {}, "exits": [], "syms": [], "share": 0.0}


def _day():
    c = lambda v: {"c": np.full(400, v, float)}
    px = {(25000.0, "CE"): c(120), (25050.0, "CE"): c(90), (25100.0, "CE"): c(60), (25150.0, "CE"): c(38), (25200.0, "CE"): c(25),
          (24950.0, "CE"): c(150), (25000.0, "PE"): c(110), (24950.0, "PE"): c(80), (24900.0, "PE"): c(55), (24850.0, "PE"): c(36)}
    return types.SimpleNamespace(step=50.0, px=px)


def test_pick_strike_steps_and_premium():
    d = _day()
    assert F.pick_strike(d, 25000.0, "CE", 10, 25000.0, {"steps": 1}) == 25050.0
    assert F.pick_strike(d, 25000.0, "CE", 10, 25000.0, {"steps": -1}) == 24950.0
    assert F.pick_strike(d, 25000.0, "PE", 10, 25000.0, {"steps": 2}) == 24900.0
    assert F.pick_strike(d, 25000.0, "CE", 10, 25000.0, {"steps": 9}) is None              # not in the chain
    assert F.pick_strike(d, 25000.0, "CE", 10, 25000.0, {"premium_pct": 0.15}) == 25150.0  # 37.5 wanted: the 38 call
    assert F.pick_strike(d, 25000.0, "PE", 10, 25000.0, {"premium_pct": 0.15}) == 24850.0


def _book():
    n = 40
    df = pd.DataFrame({"sym": ["NIFTY"] * n, "date": [pd.Timestamp("2025-01-01") + pd.Timedelta(days=i // 2) for i in range(n)],
                       "bar": [i % 2 for i in range(n)], "dir": [1] * n, "tod": [60.0] * n, "rsi": [70.0] * n,
                       "premium": [5000.0] * n, "pnl_chand": [100.0] * n, "pnl_chand@otm1": [300.0] * n, "pnl_std": [50.0] * n})
    return F.Book(df)


def test_book_reads_the_strike_column_and_old_rules_stay_atm():
    b = _book()
    assert b.strike_choices == ["otm1"]
    r = {"dir": 1, "conds": [["rsi", ">", 60.0]], "win": [15, 330], "exit": "chand", "max_day": 1, "syms": ["NIFTY"]}
    atm, otm = b.trades(r), b.trades({**r, "strike": "otm1"})
    assert len(atm) == len(otm) > 0 and atm["pnl"].iloc[0] == 100.0 and otm["pnl"].iloc[0] == 300.0
    assert b.trades({**r, "exit": "std", "strike": "otm1"}).empty                         # not priced on other strikes
    assert F.sid(r) == F.sid({**r, "strike": None}) != F.sid({**r, "strike": "otm1"})      # old ids unchanged


def test_validate_and_words():
    base = {"dir": 1, "conds": [["rsi", ">", 60]], "exit": "chand", "syms": ["NIFTY"]}
    assert F.validate({**base, "strike": "otm2"})["strike"] == "otm2"
    assert "strike" not in F.validate({**base, "strike": "atm"}) and "strike" not in F.validate({**base, "strike": "otm9"})
    assert "strike" not in F.validate({**base, "dir": 0, "strike": "otm1"})                # sells stay at the money
    assert F.words(F.validate({**base, "strike": "otm1"})).startswith("Buy the call 1 strike out of the money")
    assert F.words(F.validate(base)).startswith("Buy the ATM call")


def test_search_tries_strikes_only_where_priced():
    b = _book()
    rng = random.Random(3)
    seen = set()
    for _ in range(200):
        s = F.random_rule(b, rng)
        if s.get("strike"):
            assert f"{s['exit']}@{s['strike']}" in b.pnl and F.STOCKS not in s["syms"] and F.EACH not in s["syms"]
            seen.add(s["strike"])
        m = F.mutate(b, s, rng)
        if m.get("strike"):
            assert f"{m['exit']}@{m['strike']}" in b.pnl
    assert seen == {"otm1"}


def test_live_strike_for_the_chain():
    from live.engine import LiveEngine
    e = object.__new__(LiveEngine)
    O = lambda k: types.SimpleNamespace(key=k)
    chain = {float(k): {"CE": O(f"C{k}"), "PE": O(f"P{k}")} for k in range(24800, 25250, 50)}
    prices = {f"C{k}": max(1.0, 120 - (k - 25000) * 0.6) for k in range(24800, 25250, 50)}
    e.state = types.SimpleNamespace(get=lambda key: types.SimpleNamespace(ltp=prices.get(key)) if key in prices else None)
    assert e._strike_for(chain, 25000.0, "CE", 25000.0, "otm1") == 25050.0
    assert e._strike_for(chain, 25000.0, "PE", 25000.0, "otm2") == 24900.0
    assert e._strike_for(chain, 25000.0, "CE", 25000.0, "itm1") == 24950.0
    assert e._strike_for(chain, 25000.0, "CE", 25000.0, "lowprem") == 25150.0             # 30 nearest 37.5
    assert e._strike_for(chain, 25000.0, "CE", 25000.0, "nope") is None
