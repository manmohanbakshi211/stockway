"""3.10 (Mandeep, 26 Nov 2026: "ye sab solve karo"): the day-type read again at 10:15-12:15 (the 09:45 facts cannot
spot a trend day), a trend-day call only where it was right often enough on unseen days, and the two Trailing
challengers that use it: "Trailing · protect" and "Trailing · day type"."""
import datetime as dt
import json
import types

import numpy as np
import pandas as pd

from lab import daytype as D
from lab.exits import LivePlan
from live import books as B
from live.store import Store
from tests.test_base_exit_309 import BASE
from tests.test_books import DAY, _state, _t
from tests.test_trail_book import _alert

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
CKS = (60, 90, 120, 150, 180)


def _bars(n_months=26, seed=7):
    """Synthetic index days where the morning's own path tells more and more about the day as it goes."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2024-01-01", periods=n_months * 21)
    rows = []
    for d in days:
        for sym in ("NIFTY", "BANKNIFTY"):
            rng_end = float(np.exp(rng.normal(0, 0.35)))
            eff_end = float(rng.uniform(0, 1))
            side = 1 if rng.random() < 0.5 else -1
            gap = float(rng.normal(0, 0.3))
            move_end = side * eff_end * rng_end
            for tod in (30.0, *map(float, CKS), 330.0):
                k = min(1.0, tod / 330.0)                       # later in the day: closer to the day's real path
                noise = (1 - k) * 0.6
                r = rng_end * k * float(rng.uniform(1 - noise / 2, 1 + noise / 2)) if tod < 330 else rng_end
                mv = move_end * k + (float(rng.normal(0, noise * 0.3)) if tod < 330 else 0.0)
                rows.append({"sym": sym, "date": d.date(), "tod": tod, "dir": 1, "gap": gap, "day_ret": gap + mv,
                             "day_range": max(abs(mv), r), "vix": float(rng.normal(14, 2)), "dte": float(rng.integers(0, 6)),
                             "or_pos": float(rng.normal()), "range_x": float(rng.normal(1, 0.2)), "pdh_dist": float(rng.normal()),
                             "pdl_dist": float(rng.normal()), "cpr_width": np.nan, "adx": float(rng.normal(20, 5))})
    return pd.DataFrame(rows)


def test_rules_carry_the_checkpoints_and_the_evidence():
    c = D.cfg()
    assert c["checkpoints"] == list(CKS) and c["trend_call"]["min_right"] == 55
    assert "37%" in c["checkpoints_why"] and "1,395" in c["checkpoints_why"]
    assert c["at_tod"] == 30                                  # the 09:45 forecast is unchanged


def test_trend_threshold_needs_enough_right_calls():
    Y = np.array([0] * 40 + [1] * 60)
    P = np.zeros((100, 3))
    P[:40, 0] = 0.7                                          # the real trend days look trend-like
    P[40:70, 0] = 0.5                                        # some range days half-look like it
    P[70:, 0] = 0.2
    c = {"trend_call": {"min_right": 55, "min_called": 30, "grid": [0.3, 0.45, 0.6]}}
    r = D.trend_threshold(P, Y, c)
    assert r["threshold"] == 0.6 and r["right"] == 100.0 and r["caught"] == 100.0
    c2 = {"trend_call": {"min_right": 55, "min_called": 50, "grid": [0.6]}}
    assert D.trend_threshold(P, Y, c2)["threshold"] is None   # too few calls: no honest call


def test_learn_checkpoints_and_read_at_a_checkpoint(tmp_path):
    r = D.learn(tmp_path, df=_bars(), log=lambda m: None)
    ck = r["checkpoints"]
    assert set(ck) == {str(x) for x in CKS}
    assert ck["60"]["hhmm"] == "10:15" and ck["180"]["hhmm"] == "12:15"
    assert ck["180"]["walk"]["logloss_skill"] > ck["60"]["walk"]["logloss_skill"]   # later = better in this data
    assert ck["180"]["call"]["threshold"] is not None and ck["180"]["call"]["right"] >= 55
    assert r["walk"]["tested_days"] > 200                                             # the 09:45 model is still there
    q = D.predict_at([tmp_path], "NIFTY", {"gap": 0.0, "day_ret": 1.2, "day_range": 1.25, "vix": 14}, 180)
    assert q["hhmm"] == "12:15" and q["trend_call"] is True and q["call"] == "trend" and q["dir_hint"] == 1
    q2 = D.predict_at([tmp_path], "NIFTY", {"gap": 0.0, "day_ret": 0.05, "day_range": 1.2, "vix": 14}, 180)
    assert q2["trend_call"] is False and q2["call"] in ("range", "quiet")
    assert D.predict_at([tmp_path], "NIFTY", {"gap": 0}, 45) is None                 # no such checkpoint
    assert D.predict([tmp_path], "NIFTY", {"gap": 0.1, "day_ret": 0.1, "day_range": 0.2})["probs"]


def test_trend_now_is_dated_and_needs_a_call():
    D.NOW.clear()
    day = "2026-11-26"
    ts = dt.datetime(2026, 11, 26, 11, 0, tzinfo=IST).timestamp()
    D.set_now("NIFTY", day, {"hhmm": "10:45", "trend_call": True, "dir_hint": -1, "probs": {"trend": 62}})
    assert D.trend_now("NIFTY", ts)["dir"] == -1
    assert D.trend_now("NIFTY", ts + 86400) is None                    # yesterday's read is not today's
    D.set_now("NIFTY", day, {"hhmm": "11:15", "trend_call": False, "dir_hint": -1, "probs": {"trend": 30}})
    assert D.trend_now("NIFTY", ts) is None
    assert D.ctx_index("BANKNIFTY") == "BANKNIFTY" and D.ctx_index("TRENT") == "NIFTY" and D.ctx_index("FINNIFTY") == "NIFTY"
    D.NOW.clear()


def test_live_reads_saved_graded_and_shown(tmp_path):
    D.learn(tmp_path, df=_bars(), log=lambda m: None)
    from live.chartdata import ChartData
    logs = []
    fake = types.SimpleNamespace(e=types.SimpleNamespace(data_dir=tmp_path, log=logs.append, source="upstox"),
                                 _rate=lambda k, s: True)
    now = dt.datetime(2026, 11, 26, 10, 50)
    n = 20
    f = {"tod": (np.arange(n) + 1) * 5.0, "gap": np.full(n, 0.0), "day_ret": np.linspace(0.1, 1.2, n),
         "day_range": np.linspace(0.2, 1.25, n), "vix": np.full(n, 14.0)}
    D.NOW.clear()
    ChartData._daytype(fake, "NIFTY", now, 18, f)            # 10:45 bar closed: the 09:45 row (late) and the 10:45 read
    rows = D.live_load(tmp_path)
    assert [r.get("at") for r in rows] == [None, 90]
    assert rows[1]["hhmm"] == "10:45" and "Day type NIFTY at 10:45" in logs[-1]
    ChartData._daytype(fake, "NIFTY", now, 19, f)            # once per checkpoint
    assert len(D.live_load(tmp_path)) == 2
    assert D.NOW["NIFTY"]["at"] == "10:45" and D.NOW["NIFTY"]["day"] == "2026-11-26"
    n = 66
    f2 = {"tod": (np.arange(n) + 1) * 5.0, "gap": np.full(n, 0.0), "day_ret": np.full(n, 1.3), "day_range": np.full(n, 1.35)}
    ChartData._daytype(fake, "NIFTY", now.replace(hour=14, minute=50), 66, f2)
    rows = D.live_load(tmp_path)
    assert all(r.get("label") == "trend" for r in rows)       # every forecast of the day graded at the close
    v = D.live_view(tmp_path)
    assert v["graded"] == 1 and v["checkpoints"]["90"]["graded"] == 1          # the 09:45 record stays on its own
    td = D.today(tmp_path, "2026-11-26")
    assert len(td["rows"]) == 1 and td["reads"][0]["hhmm"] == "10:45"
    D.NOW.clear()


def test_a_replay_never_writes_the_real_record(tmp_path):
    D.learn(tmp_path, df=_bars(), log=lambda m: None)
    from live.chartdata import ChartData
    fake = types.SimpleNamespace(e=types.SimpleNamespace(data_dir=tmp_path, log=lambda m: None, source="replay"),
                                 _rate=lambda k, s: True)
    n = 20
    f = {"tod": (np.arange(n) + 1) * 5.0, "gap": np.full(n, 0.0), "day_ret": np.linspace(0.1, 1.2, n),
         "day_range": np.linspace(0.2, 1.25, n)}
    ChartData._daytype(fake, "NIFTY", dt.datetime(2026, 11, 19, 10, 50), 18, f)
    assert D.live_load(tmp_path) == []
    D.NOW.clear()


def test_liveplan_protect_cap_and_lock():
    plain = LivePlan("chand", 1000.0, 1, 12.0, 10.0, 0.0)
    assert "protect" not in plain.to_dict() and plain.stop_move() == -12.0       # without protect: as before
    pr = {"lock_at_r": 1.5, "lock_keep_r": 0.5, "max_stop_pct": 0.8}
    p = LivePlan("chand", 1000.0, 1, 12.0, 10.0, 0.0, protect=pr)
    assert p.stop_move() == -8.0                              # 0.8% of 1,000, not the 12-point chart stop
    p.best = 17.9
    assert p.stop_move() < 6.0
    p.best = 18.0                                             # +1.5R: the stop never falls below +0.5R
    assert p.stop_move() == 6.0
    assert p.check(1005.0, 10.0, 60.0) == "stop"              # came back to +5 (< +0.5R): out
    q = LivePlan.from_dict(LivePlan("chand", 1000.0, 1, 12.0, 10.0, 0.0, protect=pr).to_dict())
    assert q.protect == pr


def test_day_type_lets_a_trend_days_winner_run():
    D.NOW.clear()
    ts = dt.datetime(2026, 11, 26, 11, 30, tzinfo=IST).timestamp()
    pr = {"lock_at_r": 1.5, "lock_keep_r": 0.5, "max_stop_pct": 0.8, "unless_trend": True, "ctx": "NIFTY"}
    p = LivePlan("chand", 1000.0, 1, 12.0, 10.0, ts, best=18.0, protect=pr)
    assert p.check(1007.0, 10.0, ts) is None                  # +7 is above the +0.5R lock (6): still in
    D.set_now("NIFTY", "2026-11-26", {"hhmm": "11:15", "trend_call": True, "dir_hint": 1, "probs": {"trend": 70}})
    p._now = ts
    assert p.stop_move() < 6.0                                # a trend day the trade's way: no lock, the trail decides
    D.set_now("NIFTY", "2026-11-26", {"hhmm": "11:15", "trend_call": True, "dir_hint": -1, "probs": {"trend": 70}})
    assert p.stop_move() == 6.0                               # a trend day the other way: locked
    D.NOW.clear()
    assert p.stop_move() == 6.0


def test_accounts_protect_and_day_type(tmp_path):
    D.NOW.clear()
    sp = B.cfg()["books"]
    assert sp["trail_protect"]["protect"] == {"lock_at_r": 1.5, "lock_keep_r": 0.5, "max_stop_pct": 0.8}
    assert sp["trail_daytype"]["protect"]["unless_trend"] and sp["trail_daytype"]["pick"]["skip_against_trend_day"]
    assert sp["trail_protect"]["pick"]["mirror_main"] and sp["trail_protect"]["review"] == "show"
    st = Store(tmp_path / "t.db")
    bs = B.Books("upstox", st, dict(BASE), None, log=lambda m: None)
    now = _t(DAY, 10, 0)
    out = bs.consider(_alert(R=12.0), _state(O1=10.0), now, main={"id": 7, "lots": 2})
    assert out["trail_protect"] == "taken" and out["trail_daytype"] == "taken" and out["trail"] == "taken"
    t = st.trades(source="upstox#trail_protect")[0]
    pj = json.loads(t["plan"])
    assert pj["style"] == "chand" and pj["protect"]["ctx"] == "NIFTY" and pj["protect"]["lock_at_r"] == 1.5
    assert "protect" not in json.loads(st.trades(source="upstox#trail")[0]["plan"])          # Trailing unchanged
    D.set_now("NIFTY", DAY, {"hhmm": "10:45", "trend_call": True, "dir_hint": -1, "probs": {"trend": 66}})
    out2 = bs.consider(_alert(R=12.0, i=2, key="O2"), _state(O2=10.0), now, main={"id": 8, "lots": 1})
    assert out2["trail_daytype"].startswith("against today's trend day") and out2["trail_protect"] == "taken"
    D.NOW.clear()


def test_on_the_system_page():
    from live.selfcheck import static_checks
    c = next(x for x in static_checks() if x["id"] == "daytype_reads")
    assert c["ok"] and "10:15" in c["name"] and "12:15" in c["name"]
