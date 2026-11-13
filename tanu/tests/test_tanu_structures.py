"""Multi-leg structures (iron condor, straddle) on a small synthetic option history."""
import datetime as dt
import json

import pandas as pd
import pytest

from tanu.data.facts import Facts
from tanu.engine.options import OptionHistory
from tanu.journal.store import Journal
from tanu.structures import registry
from tanu.structures.registry import Structure
from tanu.structures.runner import Runner

D1, EXP = "2026-11-12", "2026-11-13"          # Thursday, then expiry Friday


def _t(day, hhmm):
    return int(dt.datetime.fromisoformat(f"{day}T{hhmm}:00+05:30").timestamp())


def _price(spot, k, side, tv):
    return round(max(spot - k, 0) if side == "CE" else max(k - spot, 0), 2) + tv


@pytest.fixture()
def hist(tmp_path):
    """Bank Nifty around 60,000; strikes every 100 from 58,500 to 61,500; the index drifts to `close` on expiry."""
    def make(close=60100.0, spike=None):
        rows, mins = [], []
        for day, (a, b) in ((D1, (60000.0, 60000.0)), (EXP, (60000.0, close))):
            for m in range(375):
                t = _t(day, "09:15") + 60 * m
                s = a + (b - a) * m / 374
                if spike and day == EXP and spike[0] <= m < spike[1]:
                    s = spike[2]
                mins.append((t, s))
                left = (2 if day == D1 else 1) - m / 375                # time value shrinks to 0 at expiry close
                for k in range(58500, 61600, 100):
                    for side in ("CE", "PE"):
                        tv = max(left, 0) * 60 * pow(0.7, abs(k - s) / 300)
                        p = _price(s, k, side, tv)
                        rows.append((t, 0, side, p, p, p, p, 0.15, 10, 100, float(k), s))
        d = tmp_path / "hist" / "BANKNIFTY"
        d.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows, columns=["ts", "off", "side", "open", "high", "low", "close", "iv", "volume", "oi", "strike",
                                    "spot"]).to_parquet(d / f"raw_{EXP}.parquet", index=False)
        return OptionHistory(tmp_path / "hist"), {"BANKNIFTY": pd.DataFrame(mins, columns=["t", "c"])}
    return make


def _condor(**exit_):
    return Structure(id="ic", name="t", why="t", source="t", valid_from=D1, symbols=["BANKNIFTY"],
                     entry={"dte": 1, "at": "15:15"},
                     legs=[{"action": "sell", "side": "CE", "pct": 1.0}, {"action": "buy", "side": "CE", "pct": 2.0},
                           {"action": "sell", "side": "PE", "pct": -1.0}, {"action": "buy", "side": "PE", "pct": -2.0}],
                     exit={"at": "expiry", **exit_})


def test_iron_condor_one_session_before_expiry_settles_at_intrinsic(hist):
    oh, mins = hist(close=60100.0)
    r = Runner(oh, mins, {"BANKNIFTY": 0.33})
    assert r.plan(_condor(), "BANKNIFTY") == [(D1, EXP)]
    (row,) = r.run([_condor()], ["BANKNIFTY"])
    strikes = {(x["action"], x["side"]): x["strike"] for x in row["legs"]}
    assert strikes == {("sell", "CE"): 60600.0, ("buy", "CE"): 61200.0, ("sell", "PE"): 59400.0, ("buy", "PE"): 58800.0}
    assert row["credit"] > 0 and row["reason"] == "expiry" and row["settle_spot"] == pytest.approx(60100.0)
    assert all(x["exit"] == 0 for x in row["legs"])                   # all four expire worthless at 60,100
    qty = Facts().lot("BANKNIFTY", D1)
    assert row["pnl_rs"] == pytest.approx(row["credit"] * qty - row["charges"], abs=0.5)   # credit shown rounded
    assert row["margin"] == pytest.approx((600 - row["credit"]) * qty, abs=1)   # defined risk: wing width - credit
    assert _condor().defined_risk


def test_iron_condor_loss_is_capped_by_the_wing(hist):
    oh, mins = hist(close=62000.0)                                    # far through the call wing
    (row,) = Runner(oh, mins, {"BANKNIFTY": 0.33}).run([_condor()], ["BANKNIFTY"])
    qty = Facts().lot("BANKNIFTY", D1)
    assert row["pnl_rs"] < 0 and row["pnl_rs"] >= -(600 - row["credit"]) * qty - row["charges"] - 1


def test_stop_at_twice_the_credit_buys_everything_back(hist):
    oh, mins = hist(close=60000.0, spike=(30, 60, 61150.0))
    (row,) = Runner(oh, mins, {"BANKNIFTY": 0.33}).run([_condor(stop_x_credit=2)], ["BANKNIFTY"])
    assert row["reason"] == "stop" and row["exit_t"] >= _t(EXP, "09:45")
    assert all(x["exit"] > 0 for x in row["legs"] if x["action"] == "buy" and x["side"] == "CE")


def test_intraday_straddle_every_session_and_its_stop(hist):
    oh, mins = hist(close=60000.0, spike=(100, 375, 60400.0))
    st = Structure(id="sd", name="t", why="t", source="t", valid_from=D1, symbols=["BANKNIFTY"],
                   entry={"dte": None, "at": "09:20"},
                   legs=[{"action": "sell", "side": "CE", "pct": 0}, {"action": "sell", "side": "PE", "pct": 0}],
                   exit={"at": "15:15", "stop_x_credit": 0.3})
    r = Runner(oh, mins, {"BANKNIFTY": 0.33})
    rows = {x["day"]: x for x in r.run([st], ["BANKNIFTY"])}
    assert set(rows) == {D1, EXP} and not st.defined_risk
    assert rows[D1]["reason"] == "time"                               # quiet day: bought back at 15:15
    assert rows[EXP]["reason"] == "stop"                              # 400-point jump: stopped
    assert rows[D1]["margin"] == pytest.approx(60000 * Facts().lot("BANKNIFTY", D1) * 0.12, rel=0.01)


def test_missing_wing_strike_is_skipped_not_guessed(hist):
    oh, mins = hist()
    wide = Structure(id="w", name="t", why="t", source="t", valid_from=D1, symbols=["BANKNIFTY"], entry={"dte": 1},
                     legs=[{"action": "sell", "side": "CE", "pct": 1.0}, {"action": "buy", "side": "CE", "pct": 5.0}])
    (row,) = Runner(oh, mins).run([wide], ["BANKNIFTY"])
    assert "skipped" in row and "strike" in row["skipped"]


def test_library_loads_and_every_session_needs_an_exit_time():
    good, bad = registry.load()
    assert not bad and {"bn_iron_condor_1d", "straddle_sell_920"} <= {s.id for s in good}
    with pytest.raises(ValueError):
        Structure(id="x", name="t", why="t", source="t", valid_from=D1, symbols=["NIFTY"], entry={"dte": None},
                  legs=[{"action": "sell", "side": "CE", "pct": 0}], exit={"at": "expiry"})


def test_journal_keeps_structure_trades(tmp_path, hist):
    oh, mins = hist()
    (row,) = Runner(oh, mins, {"BANKNIFTY": 0.33}).run([_condor()], ["BANKNIFTY"])
    j = Journal(tmp_path / "j.db")
    j.structure({**row, "account": "shadow_multileg"})
    (got,) = j.structures()
    assert got["structure"] == "ic" and len(got["legs"]) == 4 and got["pnl_rs"] == row["pnl_rs"]
