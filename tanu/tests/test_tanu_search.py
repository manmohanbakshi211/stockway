"""Exit variants (premium trail, time, no-move, premium stop gap) and the controlled search (combos, same-exit
yardstick, ledger raising the bar)."""
import datetime as dt
import json

import numpy as np
import pandas as pd

from tanu.engine.core import Engine, Position
from tanu.evaluate import report as EV
from tanu.evaluate import search as SR
from tanu.setups import registry as RG
from tanu.setups.registry import Exit
from tanu.state import day as SD


def _pos(entry=100.0, d=1):
    p = Position(setup="s", sym="NIFTY", day="2026-11-13", d=d, strike=25000.0, side="CE", signal_t=0.0, state={"spot": 25000.0})
    p.entry, p.entry_t, p.peak, p.stop = entry, 0.0, entry, 24900.0
    return p


def test_exit_rules_fire_in_order_and_fill_at_their_level():
    t = lambda mins: mins * 60.0
    prem = Exit(type="premium", premium_stop_pct=-30, prem_trail_pct=20, trail_after_pct=15)
    p = _pos()
    assert Engine._exit_reason(p, prem, t(5), 60, 25010, 24990, (100, 110, 98, 108)) == (None, 0.0)
    p.peak = 130.0                                                     # up 30%: the trail is armed at 104
    assert Engine._exit_reason(p, prem, t(10), 65, 0, 0, (110, 112, 101, 103)) == ("premium_trail", 104.0)
    assert Engine._exit_reason(_pos(), prem, t(3), 60, 0, 0, (60, 62, 55, 58)) == ("premium_stop", 60)   # gapped below 70
    tm = Exit(type="time", max_minutes=30)
    assert Engine._exit_reason(_pos(), tm, t(29), 60, 0, 0, (100, 101, 99, 100))[0] is None
    assert Engine._exit_reason(_pos(), tm, t(30), 60, 0, 0, (100, 101, 99, 100.5)) == ("time", 100.5)
    nm = Exit(type="time", no_move_min=20, no_move_gain_pct=5)
    assert Engine._exit_reason(_pos(), nm, t(20), 60, 0, 0, (101, 104, 100, 103)) == ("no_move", 103)
    q = _pos()
    q.peak = 106.0                                                     # it did move 6%: no "no move" exit
    assert Engine._exit_reason(q, nm, t(20), 60, 0, 0, (101, 104, 100, 103))[0] is None
    ch = Exit(type="chandelier")
    assert Engine._exit_reason(_pos(), ch, t(5), 60, 25000, 24899, (100, 101, 99, 97)) == ("stop", 97)
    assert Engine._exit_reason(_pos(), ch, t(5), 360, 25000, 24950, (100, 101, 99, 99)) == ("flat_by", 99)


def test_variants_file_is_valid_and_combos_are_complete():
    v = json.loads(SR.VARIANTS.read_text())
    for x in v["exits"]:
        Exit(**x)
    entries = RG.load()[0]
    cs = SR.combos(entries, v)
    assert len(cs) == len(entries) * len(v["exits"]) * len(v["filters"])
    c = next(s for s in cs if s.id == "orb15_break__time_30__no_expiry_day")
    assert c.exit.max_minutes == 30 and tuple(c.when[-1]) == ("dte", ">", 0)


def test_yardstick_is_matched_by_exit_and_filter():
    rng = np.random.default_rng(3)
    rows = []
    for i in range(300):
        d = f"2025-{i // 28 + 1:02d}-{i % 28 + 1:02d}"
        for s, mu in (("good__x__all", 10), ("random_baseline__x__all", 0), ("good__y__all", -1),
                      ("random_baseline__y__all", 5)):
            rows.append({"setup": s, "sym": "NIFTY", "day": d, "pct": rng.normal(mu, 12), "pnl_rs": 1.0, "reason": "time"})
    rep = EV.evaluate(pd.DataFrame(rows), k=2)
    by = {r["setup"]: r for r in rep["setups"]}
    assert by["good__x__all"]["passes"] and not by["good__y__all"]["passes"]
    assert "does not beat the baseline" in by["good__y__all"]["why_not"]
    assert "passes" not in by["random_baseline__x__all"] and rep["k"] == 2


class FakeOpts:
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

    def expiry(self, sym, day):
        return day if day.endswith("17") else "2026-12-29"              # 17 Nov is an expiry day here

    def days(self, sym):
        return None


def test_search_runs_every_combo_and_the_ledger_raises_the_bar(tmp_path):
    days = ["2026-11-1" + str(i) for i in (0, 1, 2, 3, 6, 7, 8, 9)] + [f"2026-11-{d}" for d in (20, 23, 24, 25, 26, 27)]
    rows, spot = [], {}
    rng = np.random.default_rng(5)
    level = 25000.0
    for d in days:
        base = dt.datetime.fromisoformat(d + "T09:15:00+05:30").timestamp()
        for m in range(375):
            level += rng.normal(0, 6) + (8 if 60 <= m < 120 else 0)
            rows.append({"t": base + 60 * m, "o": level, "h": level + 3, "l": level - 3, "c": level})
            spot[int(base + 60 * m)] = level
    (tmp_path / "history").mkdir()
    pd.DataFrame(rows).to_parquet(tmp_path / "history" / "NIFTY.parquet")
    entries = [s for s in RG.load()[0] if s.id in ("orb15_break", "random_baseline")]
    rep = SR.search(tmp_path, tmp_path, syms=("NIFTY",), entries=entries, options=FakeOpts(spot))
    v = json.loads(SR.VARIANTS.read_text())
    n_combo = 2 * len(v["exits"]) * len(v["filters"])
    assert rep["combos"] == n_combo and rep["k"] == n_combo // 2 + len(SR.pretest_ids())
    led = tmp_path / "research" / "ledger.jsonl"
    assert len(led.read_text().splitlines()) == len(rep["setups"])
    t = pd.read_parquet(rep["out"] + "/trades.parquet")
    assert not len(t[(t["setup"].str.endswith("no_expiry_day")) & (t["day"] == "2026-11-17")])   # filter works
    v2 = {"exits": v["exits"] + [{"name": "time_15", "type": "time", "max_minutes": 15}], "filters": v["filters"]}
    p2 = tmp_path / "v2.json"
    p2.write_text(json.dumps(v2))
    rep2 = SR.search(tmp_path, tmp_path, syms=("NIFTY",), entries=entries, variants_path=p2, options=FakeOpts(spot))
    assert rep2["k"] == rep["k"] + len(v["filters"]) and rep2["t_bar"] > rep["t_bar"]          # more ideas, higher bar
