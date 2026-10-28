"""Institution-style intraday index option strategies. Every one has a capped worst case.

Premium selling (earns the volatility risk premium on calm days):
  iron_fly       sell the ATM straddle, buy wings; each side has its own stop
  iron_condor    sell OTM call + put, buy wings; combined stop and profit target
  expiry_condor  same, only on expiry day when time decay is fastest
Directional (only when the market is clearly moving):
  orb_spread     opening-range breakout -> debit spread in the breakout direction
  trend_spread   strong one-way morning -> debit spread with the trend
"""
from __future__ import annotations

import datetime as dt
import itertools
import math

from .engine import Leg, Position

M = lambda hh, mm: (hh * 60 + mm) - (9 * 60 + 15)     # minute index from 09:15

FILTERS = {
    "loose": {"max_gap": 1.2, "vix_rise": None, "iv_rv": 0.0, "events": False},
    "strict": {"max_gap": 0.6, "vix_rise": 4.0, "iv_rv": 1.0, "events": True},
}

# scheduled high-volatility days (Budget, RBI policy, election results); extend with data/lab_events.csv
EVENTS = {
    "2023-02-01", "2023-02-08", "2023-04-06", "2023-06-08", "2023-08-10", "2023-10-06", "2023-12-08",
    "2024-02-01", "2024-02-08", "2024-04-05", "2024-06-04", "2024-06-07", "2024-07-23", "2024-08-08",
    "2024-10-09", "2024-12-06", "2025-02-01", "2025-02-07", "2025-04-09", "2025-06-06", "2025-08-06",
    "2025-10-01", "2025-12-05", "2026-02-01",
}


def _rnd(x, step):
    return round(x / step) * step


def blocked(f: dict, flt: str, selling: bool) -> str | None:
    """Reason to skip today, from facts known before the entry."""
    c = FILTERS[flt]
    g = f.get("gap_pct")
    if g is not None and not math.isnan(g) and abs(g) > c["max_gap"]:
        return "big gap"
    if not selling:
        return None
    if c["events"] and str(f.get("date")) in EVENTS:
        return "event day"
    vr = c["vix_rise"]
    if vr and f.get("vix_open") and f.get("vix_prev") and (f["vix_open"] / f["vix_prev"] - 1) * 100 > vr:
        return "VIX jumping"
    if c["iv_rv"] and f.get("iv_open") and f.get("rv20_prev") and f["iv_open"] < c["iv_rv"] * f["rv20_prev"]:
        return "options cheap vs actual moves"
    return None


# ---------------------------------------------------------------- premium selling
def iron_fly(day, f, p):
    if blocked(f, p["flt"], True):
        return None
    m = p["entry"]
    k = day.atm(m)
    w = max(2 * day.step, _rnd(day.spot[m] * p["wing"] / 100, day.step))
    legs = [Leg(k, "CE", -1), Leg(k, "PE", -1), Leg(k + w, "CE", 1), Leg(k - w, "PE", 1)]
    return Position(day.sym, "iron_fly", legs, exit_m=M(15, 15), groups=[[0, 2], [1, 3]], leg_sl_pct=p["sl"],
                    trail_to_cost=p["trail"], entry_m=m)


def iron_condor(day, f, p):
    if blocked(f, p["flt"], True):
        return None
    m = p["entry"]
    s = day.spot[m]
    d = max(day.step, _rnd(s * p["short"] / 100, day.step))
    w = max(2 * day.step, _rnd(s * p["wing"] / 100, day.step))
    k = day.atm(m)
    legs = [Leg(k + d, "CE", -1), Leg(k - d, "PE", -1), Leg(k + d + w, "CE", 1), Leg(k - d - w, "PE", 1)]
    return Position(day.sym, p.get("family", "iron_condor"), legs, exit_m=M(15, 15), groups=[[0, 2], [1, 3]],
                    stop_x=p["stop"], target_x=p["target"], entry_m=m)


def expiry_condor(day, f, p):
    if not f.get("is_expiry"):
        return None
    return iron_condor(day, f, {**p, "family": "expiry_condor"})


# ---------------------------------------------------------------- directional
def _debit_spread(day, m, direction, width_steps=2):
    k = day.atm(m)
    w = width_steps * day.step
    if direction > 0:
        return [Leg(k, "CE", 1), Leg(k + w, "CE", -1)]
    return [Leg(k, "PE", 1), Leg(k - w, "PE", -1)]


def orb_spread(day, f, p):
    if blocked(f, p["flt"], False):
        return None
    sp = day.spot
    hi, lo = max(sp[:15]), min(sp[:15])
    buf = p["buf"] / 100
    for m in range(15, p["until"] + 1):
        if sp[m] > hi * (1 + buf):
            d = 1
        elif sp[m] < lo * (1 - buf):
            d = -1
        else:
            continue
        mid = (hi + lo) / 2
        return Position(day.sym, "orb_spread", _debit_spread(day, m, d), exit_m=M(15, 0), entry_m=m,
                        stop_x=0.45, target_x=p["target"], spot_stop=(mid, "below" if d > 0 else "above"),
                        meta={"dir": d})
    return None


def trend_spread(day, f, p):
    if blocked(f, p["flt"], False):
        return None
    m = p["entry"]
    sp = day.spot[: m + 1]
    o = sp[0]
    rng = max(sp) - min(sp)
    if rng <= 0:
        return None
    eff = abs(sp[-1] - o) / rng
    iv = f.get("iv_open")
    daily = (iv / math.sqrt(252)) if iv and not math.isnan(iv) else 0.8
    move = (sp[-1] / o - 1) * 100
    if eff < 0.6 or abs(move) < p["min_move"] * daily:
        return None
    d = 1 if move > 0 else -1
    return Position(day.sym, "trend_spread", _debit_spread(day, m, d), exit_m=M(15, 0), entry_m=m,
                    stop_x=0.4, target_x=p["target"], meta={"dir": d, "eff": round(eff, 2)})


FAMILIES = {
    "iron_fly": (iron_fly, {"entry": [M(9, 20), M(9, 30), M(10, 0)], "wing": [1.0, 1.5], "sl": [0.25, 0.4],
                            "trail": [False, True], "flt": ["loose", "strict"]}),
    "iron_condor": (iron_condor, {"entry": [M(9, 30), M(10, 0)], "short": [0.4, 0.7], "wing": [0.6],
                                  "stop": [1.5, 2.5], "target": [0.5, None], "flt": ["loose", "strict"]}),
    "expiry_condor": (expiry_condor, {"entry": [M(10, 30), M(11, 30)], "short": [0.25, 0.45], "wing": [0.5],
                                      "stop": [1.5, 2.5], "target": [0.6], "flt": ["loose"]}),
    "orb_spread": (orb_spread, {"buf": [0.05, 0.15], "target": [0.6, 1.0], "until": [M(10, 0), M(11, 0)],
                                "flt": ["loose"]}),
    "trend_spread": (trend_spread, {"entry": [M(11, 0), M(12, 0)], "min_move": [0.5, 0.8], "target": [0.8],
                                    "flt": ["loose"]}),
}
SELLING = {"iron_fly", "iron_condor", "expiry_condor"}

NICE = {"iron_fly": "Iron fly (sell ATM straddle, hedged)", "iron_condor": "Iron condor (sell OTM, hedged)",
        "expiry_condor": "Expiry-day iron condor", "orb_spread": "Opening-range breakout debit spread",
        "trend_spread": "Trend-day debit spread"}


def grid(family: str):
    fn, space = FAMILIES[family]
    keys = list(space)
    for vals in itertools.product(*[space[k] for k in keys]):
        p = dict(zip(keys, vals))
        yield param_id(family, p), p


def param_id(family, p) -> str:
    return family + "|" + ",".join(f"{k}={p[k]}" for k in sorted(p))


def describe(family, p) -> str:
    t = lambda m: (dt.datetime(2000, 1, 1, 9, 15) + dt.timedelta(minutes=m)).strftime("%H:%M")
    if family == "iron_fly":
        return (f"Sell ATM straddle at {t(p['entry'])}, wings ±{p['wing']}%, each side stops at +{int(p['sl'] * 100)}%"
                f"{', other side to cost after a stop' if p['trail'] else ''}, exit 15:15, {p['flt']} filters")
    if family in ("iron_condor", "expiry_condor"):
        return (f"{'On expiry day, s' if family == 'expiry_condor' else 'S'}ell calls/puts {p['short']}% away at {t(p['entry'])}, "
                f"wings +{p['wing']}%, stop at {p['stop']}× credit"
                f"{', take profit at ' + str(int(p['target'] * 100)) + '% of credit' if p['target'] else ''}, exit 15:15")
    if family == "orb_spread":
        return (f"Break of the 9:15–9:30 range by {p['buf']}% before {t(p['until'])}: buy ATM, sell 2 strikes out; "
                f"target +{int(p['target'] * 100)}%, stop −45% or back inside the range, exit 15:00")
    if family == "trend_spread":
        return (f"At {t(p['entry'])}, if the day is trending (≥{p['min_move']}× a normal day's move, clean): debit spread "
                f"with the trend; target +{int(p['target'] * 100)}%, stop −40%, exit 15:00")
    return family
