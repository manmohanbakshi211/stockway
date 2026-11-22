"""2.83: the multiverse's strict pick rule, in shadow (Mandeep, 22 Nov: "Go").
The usual lower bound already counts days (the larger of the per-signal and the per-day error), but with few days and
very uneven days one big day can carry a timeline: 40 winning signals on one day and one loser on each of six other days
still shows a high bound. The strict rule needs 5+ days, green on 60%+ of them, and also bounds the average of the days
(every day once). It only shows; the Multiverse pick paper account keeps the usual picks."""
import copy
import json
import pathlib

import numpy as np

import tests.test_multiverse as T
from lab import multiverse as MV

DAYS7 = ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13"]
BIG = "2026-10-12"


def _acc(days):
    """days: list of (day total %, signals) for one filter and one exit."""
    a = MV.Acc(1, 1)
    for s, n in days:
        s1 = np.array([[s]], np.float32)
        a.add(s1, s1 * s1 / max(n, 1), (s1 > 0).astype(np.float32), np.array([float(n)], np.float32))
    return a


def test_the_day_level_bound_counts_every_day_once():
    days = [(1200.0, 40)] + [(-20.0, 1)] * 6
    a = _acc(days)
    lcb, mean = MV.day_lcb_table(a, 2.0)
    avgs = np.array([s / n for s, n in days])
    want = avgs.mean() - MV.t_crit(np.array([6.0]), 2.0)[0] * avgs.std(ddof=1) / np.sqrt(7)
    assert abs(float(mean[0, 0]) - avgs.mean()) < 1e-4 and abs(float(lcb[0, 0]) - want) < 1e-3
    assert MV.cell_day_lcb(a, 0, 0, 2.0) == round(float(want), 2)
    assert float(lcb[0, 0]) < 0 < float(MV.lcb_table(a, 2.0, 1, 2)[0][0, 0])     # the usual bound likes the big day


def test_strict_needs_enough_days_and_mostly_green_days():
    sc = {"on": True, "min_days": 5, "min_green_share": 0.6}
    four = _acc([(50.0, 5)] * 4)
    assert not np.isfinite(MV.strict_table(four, 2.0, 1, sc)[0][0, 0])                 # 4 days: not enough
    two_green = _acc([(60.0, 5), (55.0, 5), (-5.0, 5), (-4.0, 5), (-6.0, 5), (-3.0, 5)])
    assert not np.isfinite(MV.strict_table(two_green, 2.0, 1, sc)[0][0, 0])            # 2 of 6 green
    good = _acc([(50.0, 5), (45.0, 5), (55.0, 5), (40.0, 5), (-5.0, 5), (48.0, 5)])
    st = float(MV.strict_table(good, 2.0, 1, sc)[0][0, 0])
    usual = float(MV.lcb_table(good, 2.0, 1, 2)[0][0, 0])
    assert np.isfinite(st) and st <= usual + 1e-6                                    # never above the usual bound


def _one_big_day(tmp_path):
    rng = np.random.default_rng(5)

    def spec(day, i):
        if day == BIG or i < 1:                          # 40 winners on one day, one loser on each other day
            return "orb", {"mins": 45}, T._ramp(70 if day == BIG else -20)
        if 30 <= i < 39:                                 # 9 modest winners every other day
            return "day_high", {"mins": 45}, T._ramp(18 + float(rng.normal(0, 3)))
        return "sharp_move", {"mins": 45}, T._ramp(float(rng.normal(-2, 12)))
    old = T.DAYS
    T.DAYS = DAYS7
    try:
        return T._universe(tmp_path, spec)
    finally:
        T.DAYS = old


def _cfg(strict_on=True):
    c = copy.deepcopy(MV.cfg())
    c["kinds_min"] = 30
    c.setdefault("strict", {})["on"] = strict_on
    return c


def test_strict_skips_a_timeline_carried_by_one_big_day(tmp_path):
    data = _one_big_day(tmp_path)
    r = MV.run(data, log=lambda m: None, days=DAYS7, c=_cfg())
    top = r["best"][0]
    assert top["filter"].get("kind") == "orb" and top["lcb"] > 0 and top["green_days"] == 1 and top["day_lcb"] < 0
    s = r["strict"]
    assert s["best"] and all(b["filter"].get("kind") != "orb" for b in s["best"])
    assert s["best"][0]["filter"].get("kind") == "day_high" and s["best"][0]["green_days"] / s["best"][0]["days"] >= 0.6
    assert s["live_picks"] and all(p["filter"].get("kind") != "orb" for p in s["live_picks"])
    assert any(p["filter"].get("kind") == "orb" for p in r["live_picks"])          # the paper account's picks: unchanged
    wt = s["walk_total"]
    assert wt["days"] >= 1 and {"usual_best_avg", "usual_port_avg"} <= set(wt)
    assert all(isinstance(w.get("champ_avg"), (float, type(None))) for w in r["walk"])   # no float32 long numbers
    saved = json.loads((pathlib.Path(data) / "multiverse.json").read_text())
    assert saved["strict"]["rule"] == {"on": True, "min_days": 5, "min_green_share": 0.6}


def test_the_shadow_changes_nothing_the_usual_picks_show(tmp_path):
    data = _one_big_day(tmp_path)
    on = MV.run(data, log=lambda m: None, days=DAYS7, c=_cfg(True))
    off = MV.run(data, log=lambda m: None, days=DAYS7, c=_cfg(False))

    def usual(r):
        r = {k: v for k, v in r.items() if k not in ("made", "seconds", "strict")}
        for w in r["walk"]:
            w.pop("strict", None)
        return json.loads(json.dumps(r, default=str))
    assert "strict" in on and "strict" not in off
    assert usual(on) == usual(off)
