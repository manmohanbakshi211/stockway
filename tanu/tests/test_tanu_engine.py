"""T3: setup registry (isolation), day state (point-in-time), the engine (signals, next-minute fills, exits,
determinism), historical option prices, and the evaluator (a planted edge passes, noise does not)."""
import datetime as dt
import json

import numpy as np
import pandas as pd

from tanu.engine import replay as RP
from tanu.engine.core import Engine
from tanu.engine.options import OptionHistory
from tanu.evaluate import report as EV
from tanu.setups import registry as RG
from tanu.state import day as SD

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _t(d, m):
    return dt.datetime.fromisoformat(f"{d}T09:15:00+05:30").timestamp() + 60 * m


def test_registry_loads_good_setups_and_isolates_bad_ones(tmp_path):
    good, bad = RG.load()
    ids = {s.id for s in good}
    assert {"sharp_move_trend", "vwap_pullback_trend", "orb15_break", "random_baseline"} <= ids and not bad
    for s in good:
        assert s.status == "shadow"                                    # new bullets start in shadow
    (tmp_path / "ok.json").write_text((RG.LIBRARY / "orb15_break.json").read_text().replace('"orb15_break"', '"ok"'))
    (tmp_path / "typo.json").write_text(json.dumps({"id": "typo", "nme": "x"}))
    (tmp_path / "wrong_name.json").write_text((RG.LIBRARY / "orb15_break.json").read_text())
    g, b = RG.load(tmp_path)
    assert [s.id for s in g] == ["ok"] and {x["file"] for x in b} == {"typo.json", "wrong_name.json"}


def test_day_context_uses_only_earlier_days():
    rng = np.random.default_rng(1)
    n = 40
    C = 100 + np.cumsum(rng.normal(0, 0.05, (n, 375)), axis=1)
    H, L = C + 0.1, C - 0.1
    days = [f"d{i:02d}" for i in range(n)]
    a = SD.build_contexts(days, H, L, C)
    H2 = H.copy()
    H2[30:] += 50                                                     # change day 30 and later
    b = SD.build_contexts(days, H2, L, C)
    assert a["d30"].med_range == b["d30"].med_range and np.allclose(a["d30"].frac, b["d30"].frac)
    assert a["d31"].med_range != b["d31"].med_range
    ctx = a["d20"]
    day, rel, sign = SD.classify(ctx.expected_range_pct(200) * 1.5, ctx.expected_range_pct(200) * 1.2, 200, ctx)
    assert (day, sign) == ("trend", 1) and rel > 1


class FakeOptions:
    """A call gains 0.5 per point the index rises, a put the opposite; ATM = spot rounded to 50."""

    def __init__(self, spot_at):
        self.spot_at = spot_at

    def atm(self, sym, day, t, spot, side):
        return round(spot / 50) * 50

    def bar(self, sym, day, strike, side, t):
        s = self.spot_at.get(int(t))
        if s is None:
            return None
        p = max(1.0, 100 + (0.5 if side == "CE" else -0.5) * (s - strike))
        return (p, p + 1, p - 1, p)


def _trend_day(day="2026-11-13", prev=25000.0):
    c = np.full(375, prev)
    c[:60] = prev + np.linspace(0, 20, 60)                       # quiet start
    c[60:120] = c[59] + np.linspace(0, 600, 60)                  # a sharp rally from 10:15
    c[120:] = c[119] - np.linspace(0, 40, 255)
    t = np.array([_t(day, m) for m in range(375)])
    return t, c


def test_engine_signals_fill_next_minute_and_exit_deterministically():
    ctx = SD.DayContext(prev_close=25000.0, med_range=0.8, frac=np.linspace(0.1, 1.0, 375))
    t, c = _trend_day()
    setups = [s for s in RG.load()[0] if s.id in ("sharp_move_trend", "random_baseline")]

    def run():
        e = Engine(setups, FakeOptions({int(a): b for a, b in zip(t, c)}), spread_pct={"NIFTY": 0.3})
        e.start_day("NIFTY", "2026-11-13", ctx)
        for a, b in zip(t, c):
            e.on_bar("NIFTY", float(a), b, b + 1, b - 1, b)
        e.end_day("NIFTY")
        return e.trades(), e.signals
    tr, sig = run()
    assert run() == (tr, sig)                                         # same bars, same trades
    sm = [x for x in tr if x["setup"] == "sharp_move_trend"]
    assert len(sm) == 1 and sm[0]["d"] == 1 and sm[0]["side"] == "CE"  # only with the move, once
    s0 = next(s for s in sig if s["setup"] == "sharp_move_trend")
    assert sm[0]["entry_t"] == s0["t"] + 60                           # filled at the next minute's open
    assert sm[0]["pct"] > 0 and sm[0]["pnl_rs"] > 0 and sm[0]["reason"] in ("stop", "flat_by")
    rb = [x for x in tr if x["setup"] == "random_baseline"]
    assert len(rb) == 1 and rb[0]["at_m"] == 45


def test_option_history_picks_the_nearest_expiry_and_atm(tmp_path):
    sym = tmp_path / "NIFTY"
    sym.mkdir()
    for exp, day in (("2026-11-17", "2026-11-13"), ("2026-11-24", "2026-11-13"), ("2026-11-24", "2026-11-20")):
        rows = [{"ts": int(_t(day, m)), "off": 0, "side": sd, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
                 "iv": None, "volume": 1, "oi": 1, "strike": k, "spot": 25010.0}
                for m in (0, 1) for sd in ("CE", "PE") for k in (24950.0, 25000.0, 25050.0)]
        f = sym / f"raw_{exp}.parquet"
        old = pd.read_parquet(f) if f.exists() else None
        pd.concat([old, pd.DataFrame(rows)] if old is not None else [pd.DataFrame(rows)]).to_parquet(f)
    oh = OptionHistory(tmp_path)
    assert oh.file_for("NIFTY", "2026-11-13").name == "raw_2026-11-17.parquet"
    assert oh.file_for("NIFTY", "2026-11-20").name == "raw_2026-11-24.parquet"
    assert oh.atm("NIFTY", "2026-11-13", _t("2026-11-13", 1), 25010.0, "CE") == 25000.0
    assert oh.bar("NIFTY", "2026-11-13", 25000.0, "PE", _t("2026-11-13", 0)) == (10.0, 11.0, 9.0, 10.5)
    assert oh.atm("NIFTY", "2026-11-14", _t("2026-11-14", 0), 25000.0, "CE") is None


def test_day_arrays_fill_small_gaps():
    t = [_t("2026-11-13", m) for m in range(375) if m != 100]
    df = pd.DataFrame({"t": t, "o": 1.0, "h": 2.0, "l": 0.5, "c": [float(i) for i in range(374)]})
    days, T, O, H, L, C = RP.day_arrays(df)
    assert days == ["2026-11-13"] and C.shape == (1, 375) and C[0, 100] == C[0, 99]


def test_evaluator_finds_a_planted_edge_and_rejects_noise():
    rng = np.random.default_rng(7)
    days = [f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}" for i in range(300)]
    rows = []
    for d in days:
        rows.append({"setup": "edge", "sym": "NIFTY", "day": d, "pct": rng.normal(10, 15), "pnl_rs": 1.0, "reason": "stop"})
        rows.append({"setup": "noise", "sym": "NIFTY", "day": d, "pct": rng.normal(-2, 20), "pnl_rs": 1.0, "reason": "stop"})
        rows.append({"setup": "random_baseline", "sym": "NIFTY", "day": d, "pct": rng.normal(-2, 20), "pnl_rs": 1.0,
                     "reason": "stop"})
    rep = EV.evaluate(pd.DataFrame(rows))
    by = {r["setup"]: r for r in rep["setups"]}
    assert by["edge"]["passes"] and not by["noise"]["passes"] and "passes" not in by["random_baseline"]
    assert rep["holdout_from"] == days[240] and "| edge |" in EV.markdown(rep)
