"""Cost model (spread, slippage, charges, theta), ATM spreads from chain snapshots, Lab drag, the tradeable list,
the instrument list."""
import gzip
import json

import pandas as pd
import pytest

from tanu.costs import measure as M
from tanu.costs import model
from tanu.costs import tradeable as TR
from tanu.data import instruments as I

DAY = "2026-11-13"


def test_theta_follows_the_square_root_rule():
    assert model.theta_pct(0, 375) == 0
    assert model.theta_pct(375, 375) == 100                     # held to expiry: all time value gone
    t = model.minutes_to_expiry(1, 187.5)                         # today half left + 1 more session
    assert t == 562.5 and model.theta_pct(60, t) == pytest.approx((1 - (502.5 / 562.5) ** 0.5) * 100, abs=1e-3)
    assert model.theta_pct(60, model.minutes_to_expiry(5)) < model.theta_pct(60, model.minutes_to_expiry(0))


def test_round_trip_adds_up():
    c = model.round_trip(100.0, 0.5, 65, DAY, hold_min=60, mins_to_expiry=10_000, extra_slip_ticks=1, tick=0.05)
    entry, exit_ = 100.275, 99.725
    assert c["paid_rs"] == pytest.approx(entry * 65, abs=0.01)
    assert c["spread_rs"] == pytest.approx((entry - exit_) * 65, abs=0.01)
    assert c["total_rs"] == pytest.approx(c["spread_rs"] + c["charges_rs"] + c["theta_rs"], abs=0.02)
    assert 0 < c["charges_pct"] < 1 and c["total_pct"] > c["spread_pct"]
    tiny = model.round_trip(2.0, 0.0, 5500, DAY, hold_min=60, mins_to_expiry=10_000)     # a ₹2 option: the tick hurts
    assert tiny["spread_pct"] > 4


def _chains():
    rows = []
    for ts, spot in ((100, 1000.0), (200, 1004.0)):
        for strike in (990.0, 1000.0, 1010.0):
            for side in ("CE", "PE"):
                mid = 20.0 if strike == 1000.0 else 15.0
                rows.append({"ts": ts, "sym": "ABC", "expiry": "2026-11-25", "spot": spot, "strike": strike, "side": side,
                             "key": f"K{strike}{side}", "ltp": mid, "bid": mid - 0.2, "ask": mid + 0.2})
        rows.append({"ts": ts, "sym": "ABC", "expiry": "2026-12-30", "spot": spot, "strike": 1000.0, "side": "CE",
                     "key": "far", "ltp": 30.0, "bid": 29.0, "ask": 31.0})                       # later expiry: ignored
    rows.append({"ts": 300, "sym": "ABC", "expiry": "2026-11-25", "spot": 1000.0, "strike": 1000.0, "side": "CE",
                 "key": "x", "ltp": 0, "bid": 0.0, "ask": 0.0})                                   # no quote: dropped
    return pd.DataFrame(rows)


def test_atm_spreads_from_chain_snapshots():
    q = M.nearest_expiry(M.atm_quotes(_chains()))
    assert set(q["strike"]) == {1000.0} and len(q) == 4 and set(q["expiry"]) == {"2026-11-25"}
    s = M.day_spreads(_chains(), DAY).iloc[0]
    assert (s.sym, s.snapshots, s.mid) == ("ABC", 2, 20.0) and s.spread_pct == pytest.approx(2.0)


def test_lab_drag_reads_the_table_in_place(tmp_path):
    p = tmp_path / "lab.parquet"
    pd.DataFrame({"sym": ["A"] * 4 + ["B"] * 2, "date": ["2026-01-01"] * 6, "tod": [15, 30, 20, 0, 15, 15],
                  "dir": [1, -1, 1, 0, 1, -1], "premium": [100.0] * 6, "pnl_std": [-2.0, -4.0, 50.0, 9.0, -10.0, -20.0]}
                 ).to_parquet(p)
    d = M.lab_drag(p).set_index("sym")
    assert d.loc["A", "lab_drag_pct"] == -3.0 and d.loc["A", "lab_n"] == 2          # tod 20 off-grid, dir 0 a straddle
    assert d.loc["B", "lab_drag_pct"] == -15.0


def test_tradeable_list_marks_cost_and_reasons(tmp_path):
    sp = pd.DataFrame([{"sym": "CHEAP", "day": DAY, "expiry": "2026-11-17", "spread_pct": 0.5, "spread": 0.5, "mid": 100.0,
                        "snapshots": 50},
                       {"sym": "WIDE", "day": DAY, "expiry": "2026-11-25", "spread_pct": 8.0, "spread": 0.4, "mid": 5.0,
                        "snapshots": 10},
                       {"sym": "NOLOT", "day": DAY, "expiry": "2026-11-25", "spread_pct": 0.5, "spread": 0.5, "mid": 100.0,
                        "snapshots": 10}])
    items = TR.build(DAY, sp, {"CHEAP": 65, "WIDE": 5500})
    by = {i["sym"]: i for i in items}
    assert by["CHEAP"]["tradeable"] and by["CHEAP"]["dte"] == 2 and items[0]["sym"] == "CHEAP"
    assert not by["WIDE"]["tradeable"] and any("spread" in r for r in by["WIDE"]["why_not"])
    assert by["NOLOT"]["why_not"] == ["lot size unknown"]
    p = TR.write(DAY, items, tmp_path)
    assert json.loads(p.read_text())["tradeable"] == 1


def test_instrument_list_gives_lots_ticks_and_expiries(tmp_path):
    import datetime as dt
    EXP_MS = int(dt.datetime(2026, 11, 17, 15, 29, 59, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))).timestamp() * 1000)
    rows = [{"segment": "NSE_FO", "instrument_type": "CE", "underlying_symbol": "NIFTY", "underlying_key": "NSE_INDEX|Nifty 50",
             "underlying_type": "INDEX", "lot_size": 65, "tick_size": 5.0, "expiry": EXP_MS},
            {"segment": "NSE_FO", "instrument_type": "FUT", "underlying_symbol": "NIFTY", "lot_size": 65},
            {"segment": "NSE_EQ", "instrument_type": "EQ", "name": "SBIN"}]
    p = tmp_path / "NSE.json.gz"
    with gzip.open(p, "wt") as f:
        json.dump(rows, f)
    u = I.underlyings(I.load(p))
    assert list(u) == ["NIFTY"] and u["NIFTY"]["lot"] == 65 and u["NIFTY"]["tick"] == 0.05 and u["NIFTY"]["is_index"]
    assert u["NIFTY"]["expiries"] == ["2026-11-17"]
