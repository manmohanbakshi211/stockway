"""Strategy lab tests: exits, costs, sizing, selection."""
import datetime as dt

import numpy as np
import pandas as pd

from lab.data import N_MIN, Day
from lab.engine import Leg, Position, charges, simulate
from lab.walk import _stats, choose, DEFAULTS


def flat_day(spot=25000.0, step=50):
    d = Day(date=dt.date(2026, 1, 5), sym="NIFTY", step=step, spot=np.full(N_MIN, spot))
    for k in range(24500, 25551, 50):
        for side in ("CE", "PE"):
            intrinsic = max(0, spot - k) if side == "CE" else max(0, k - spot)
            px = np.full(N_MIN, intrinsic + 60.0)
            d.px[(float(k), side)] = {"c": px.copy(), "h": px.copy(), "l": px.copy(), "iv": np.full(N_MIN, 14.0)}
    return d


def test_iron_fly_leg_stop_and_costs():
    d = flat_day()
    # the call side explodes at minute 100
    d.px[(25000.0, "CE")]["h"][100:] = 200.0
    d.px[(25000.0, "CE")]["c"][100:] = 200.0
    pos = Position("NIFTY", "iron_fly", [Leg(25000, "CE", -1), Leg(25000, "PE", -1), Leg(25300, "CE", 1), Leg(24700, "PE", 1)],
                   groups=[[0, 2], [1, 3]], leg_sl_pct=0.4, entry_m=5, exit_m=360)
    r = simulate(d, pos)
    assert r["reason"] == "leg stop"
    ce = pos.legs[0]
    assert abs(ce.exit - (ce.sl * 1.02 + max(0.30, 0.004 * ce.sl * 1.02))) < 1e-6
    assert pos.legs[1].exit_m == 360          # the put side stays until the time exit
    assert r["gross_pts"] < 0
    c = charges(r["fills"], 65)
    assert 100 < c < 400


def test_condor_target():
    d = flat_day()
    for k in (25200.0, 24800.0):
        side = "CE" if k > 25000 else "PE"
        d.px[(k, side)]["c"][50:] = 10.0
        d.px[(k, side)]["h"][50:] = 10.0
    pos = Position("NIFTY", "iron_condor", [Leg(25200, "CE", -1), Leg(24800, "PE", -1), Leg(25500, "CE", 1), Leg(24500, "PE", 1)],
                   stop_x=1.5, target_x=0.5, entry_m=15, exit_m=360)
    r = simulate(d, pos)
    assert r["reason"] == "target" and r["gross_pts"] > 0


def test_planned_loss_below_worst():
    pos = Position("NIFTY", "iron_condor", [Leg(25200, "CE", -1), Leg(24800, "PE", -1), Leg(25500, "CE", 1), Leg(24500, "PE", 1)],
                   stop_x=1.5)
    for l, p in zip(pos.legs, (60, 60, 15, 15)):
        l.entry = p
    assert pos.net_premium == 90
    assert abs(pos.max_loss_pts() - 210) < 1e-9
    assert pos.planned_loss_pts() <= pos.max_loss_pts()


def test_selection_needs_significance():
    rng = np.random.default_rng(1)
    noise = pd.DataFrame({"pid": "a", "net_lot": rng.normal(5, 400, 60)})
    edge = pd.DataFrame({"pid": "b", "net_lot": rng.normal(250, 400, 60)})
    pid, s = choose(pd.concat([noise, edge]), "iron_fly", DEFAULTS)
    assert pid == "b" and s["t"] >= 2
    pid, _ = choose(noise, "iron_fly", DEFAULTS)
    assert pid is None
