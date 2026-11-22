"""2.89: the two EMA videos Mandeep sent on 13 Nov 2026 inside Trend Rider (Mandeep, 22 Nov: "saare factors ke sath ye
bhi include karo jo transcript me hai"). Rules copied from Tanu 0.11 / 0.12 (engine/tf.py), judged on closed
5-minute bars; on trial: the decision stays exactly 2.84's until the bullets are switched on."""
import json

import numpy as np

from lab import rider as R
from lab import rider_exit as X
from lab import rider_lab as RL


def _b(rows, start=1795000000.0):
    o, h, l, c = (np.array([r[i] for r in rows], float) for i in range(4))
    return {"t": start + 300 * np.arange(len(c)), "o": o, "h": h, "l": l, "c": c, "v": np.zeros(len(c))}


# a steady rise, then a candle wholly above the 5 EMA, then a bar that breaks its low
UP = [(100 + i, 100.6 + i, 99.8 + i, 100.5 + i) for i in range(8)]
ALERT = (108.6, 110.0, 108.6, 109.9)          # low 108.6 above the 5 EMA (~107.9), close above it


def test_5_ema_alert_fires_a_put_on_the_break_of_the_alert_low():
    rows = UP + [ALERT, (109.9, 110.0, 108.2, 108.4)]                # next bar breaks 108.6
    x = R.ema_alert(_b(rows), 5, 1)
    assert x[-1] and x[-1]["level"] == 108.6 and x[-1]["stop"] == 110.0 and x[1] is None
    held = R.ema_alert(_b(UP + [ALERT, (109.9, 110.4, 108.9, 110.2)]), 5, 1)   # no break: nothing fires
    assert held[-1] is None
    late = R.ema_alert(_b(UP + [ALERT, (109.9, 110.4, 108.9, 110.2), (110.2, 110.3, 108.0, 108.1)]), 5, 1)
    assert late[-1]["level"] == 108.9 and late[-1]["stop"] == 110.4   # the next such candle becomes the alert


def test_5_ema_call_side_on_15_minute_candles():
    down = [(130 - i, 130.2 - i, 129.4 - i, 129.5 - i) for i in range(12)]       # four 15-min candles falling
    alert15 = [(117.6, 117.8, 117.2, 117.4), (117.4, 117.5, 116.9, 117.0), (117.0, 117.3, 116.8, 117.1)]
    rows = down + alert15 + [(117.1, 117.6, 117.0, 117.5), (117.5, 118.2, 117.4, 118.1)]
    g, cur = R.tf_groups(_b(rows), 3)
    assert len(g["c"]) == 5 and cur == 5
    x = R.ema_alert(_b(rows), 5, 3)
    assert x[1] and x[1]["level"] == 117.8 and x[1]["stop"] == 116.8                  # high 117.8 broke, stop its low
    first = R.ema_alert(_b(rows[:-1]), 5, 3)                          # the bar before did not break it yet
    assert first[1] is None


def test_10_ema_pullback_long_trigger_and_its_ema_stop():
    rows = [(100 + i, 100.8 + i, 99.9 + i, 100.7 + i) for i in range(12)]
    red = (112.0, 112.3, 111.2, 111.4)                              # red, low far above the 10 EMA
    x = R.ema_pullback(_b(rows + [red, (111.4, 112.6, 111.3, 112.5)]), 10, 1)
    assert x[1] and x[1]["level"] == 112.3 and x[1]["stop"] < 111.2 and x[-1] is None
    assert R.ema_pullback(_b(rows + [red, (111.4, 112.2, 111.0, 111.9)]), 10, 1)[1] is None


def test_the_family_vote_remembers_recent_bars_and_is_not_counted_by_default():
    c_ = R.cfg()
    assert c_["ema_videos"]["count_in_agreement"] is False and c_["ema_videos"]["as_trigger"] is False
    b = _b(UP + [ALERT, (109.9, 110.4, 108.9, 110.2)])
    ev = R.ema_videos(b, c_, hist=[[], [-1]])                        # fired a put one bar ago
    assert ev["vote"][-1] and not ev["now"]
    assert not R.ema_videos(b, c_, hist=[[-1], [], []])["vote"][-1]  # too long ago (3 bars)
    r = R.readings(b, {}, c_, None, None, None, None, None, None, [[], [-1]])
    dn = r["down"]
    assert dn["agree"]["ema_vote"] is True and "ema" not in dn["agree"]["agree"]          # recorded, not counted
    assert "ema" in dn["agree_ema"]["agree"] and dn["agree_ema"]["families"] == dn["agree"]["families"] + 1


def test_switched_off_the_decision_is_exactly_the_old_one_and_switched_on_it_can_trigger():
    b = _b(UP + [ALERT, (109.9, 110.0, 108.2, 108.4)])
    c_ = R.cfg()
    r = R.readings(b, {}, c_, None, None, None, None, None, None, [])
    assert r["breakout"] is None and r["ema"]["now"] == [-1]
    off = R.decide(b, r, 30.0, c_)
    assert off["go"] is False and off["fails"][-1] == "no breakout"
    on = {**c_, "ema_videos": {**c_["ema_videos"], "as_trigger": True}}
    d = R.decide(b, r, 30.0, on)
    assert d["dir"] == -1 and d["trigger"] == "5 EMA alert" and any("video rule" in w for w in d["why"])
    assert d["stop"] is not None and d["stop"] >= 110.0                  # beyond the alert candle's high


def test_10_ema_trail_waits_for_1_to_2_then_follows_the_ema():
    t0 = 1795060800.0 - (1795060800.0 % 300)
    p = X.RiderPlan(100.0, 1, 98.0, 1.0, 50.0, t0, {**X.DEFAULT, "trail_mode": "ema", "nomove_bars": 99}, ema=99.5)

    def bar(k, o, h, l, c):
        for i, px in enumerate((o, h, l, c)):
            p.check(px, None, t0 + 300 * k + 10 + 60 * i)
        return p.check(c, None, t0 + 300 * (k + 1) + 1)
    assert bar(0, 100, 102.4, 99.9, 102.2) is None and p.locked and abs(p.stop - 100.05) < 1e-9   # +1R: to entry
    assert bar(1, 102.2, 103.0, 102.0, 102.8) is None and abs(p.stop - 100.05) < 1e-9        # below 1:2: stays
    assert bar(2, 102.8, 104.6, 102.7, 104.5) is None and p.stop > 100.05                     # 1:2 reached: EMA
    assert p.stop == p.ema
    q = X.RiderPlan.from_dict(json.loads(json.dumps(p.to_dict())))
    assert q.ema == p.ema
    plain = X.RiderPlan(100.0, 1, 98.0, 1.0, 50.0, t0)                  # default: the 2.85 ATR trail, no ema key
    assert "ema" not in plain.to_dict() and plain.c["trail_mode"] == "atr"


def test_lab_measures_every_way_without_moving_the_live_numbers():
    names = [v[0] for v in RL._variants()]
    assert names[:24] == [f"w{w}_nm{n}_em{e}" for w in (15, 30, 45) for n in (3, 4, 6, 99) for e in (35, 25)]
    assert {"ema:w15_nm3_em35", "trig:w15_nm3_em35", "w15_nm3_em35_t10", "ema:w15_nm3_em35_t10",
            "trig:w15_nm3_em35_t10"} <= set(names) and RL.V1 in names


# ---------------------------------------------------------------------- 2.90: confidence, every factor at once
def test_confidence_counts_every_factor_and_says_what_it_could_not_see():
    b = _b(UP + [ALERT, (109.9, 110.4, 108.9, 110.2)])
    c_ = R.cfg()
    r = R.readings(b, {}, c_, None, None, None, None, None, None, [])
    alone = R.confidence(r, 1, c_)
    assert alone["score"] is not None and 0 <= alone["score"] <= 100
    assert {"ghanshyam", "strategies", "mindgame", "forecast", "flow", "option"} <= set(alone["no_view"])
    up = R.confidence(r, 1, c_, {"ghanshyam": 1, "strategies": 1, "mindgame": 1, "forecast": 1,
                                 "ghanshyam_why": "1 with (Ghanshyam 9:20), 0 against"})
    dn = R.confidence(r, 1, c_, {"ghanshyam": -1, "strategies": -1, "mindgame": -1, "forecast": -1})
    assert up["score"] > alone["score"] > dn["score"]
    assert "ghanshyam" in up["with"] and "ghanshyam" in dn["against"] and up["why"]["ghanshyam"].startswith("1 with")
    every = {k: 1 for k in c_["confidence"]["weights"]}
    r2 = {"breakout": {"dir": 1, "quality": 90}, "trend_day": {"score": 80, "dir": 1}, "rs": 1.0,
          "up": {"agree_ema": {"checked": ["trend", "momentum", "structure", "vwap", "flow", "option", "ema"],
                               "agree": ["trend", "momentum", "structure", "vwap", "flow", "option", "ema"]},
                 "chase": 10, "mvd": 5, "trad": {"score": 90}}, "down": {}}
    top = R.confidence(r2, 1, c_, every)
    assert top["against"] == [] and top["label"] == "strong"
    assert top["score"] < 100 and set(top["neutral"]) == {"chase", "tradability"}       # "not late" / "tradable" are neutral


def test_confidence_is_shown_but_not_used_until_switched_on():
    b = _b(UP + [ALERT, (109.9, 110.0, 108.2, 108.4)])
    c_ = R.cfg()
    assert c_["confidence"]["use_in_decision"] is False
    r = R.readings(b, {}, c_, None, None, None, None, None, None, [])
    on = {**c_, "ema_videos": {**c_["ema_videos"], "as_trigger": True}}
    base = R.decide(b, r, 30.0, on)
    r["conf"] = {-1: {"score": 20.0, "label": "weak"}, 1: {"score": 80.0, "label": "strong"}}
    gated = R.decide(b, r, 30.0, {**on, "confidence": {**c_["confidence"], "use_in_decision": True}})
    assert not any(f.startswith("confidence") for f in base["fails"])
    assert "confidence 20.0" in gated["fails"]


def test_live_rider_gathers_ghanshyam_and_the_other_strategies_from_the_same_scan():
    import datetime as dt
    import types
    from live.rider import Rider
    now = dt.datetime(2026, 11, 19, 10, 0, 5)
    ts = now.timestamp()
    e = types.SimpleNamespace(CONF_WINDOW_S=900, mind=None)
    e._conf = {"NIFTY": [(ts - 60, 1, "Pg1"), (ts - 30, 1, "Pg2"), (ts - 10, -1, "Lx1"), (ts - 2000, -1, "Pg3")]}
    e.charts = types.SimpleNamespace(_pack_rules=lambda: [
        {"_variant": "Pg1", "_pack": "ghanshyam_course", "_strategy": "9:20 breakout"},
        {"_variant": "Pg2", "_pack": "ghanshyam_course", "_strategy": "VWAP hold"},
        {"_variant": "Pg3", "_pack": "ghanshyam_course", "_strategy": "old"}])
    e.forecast_facts = lambda sym, d, n: {"fc5_al": 0.5 if d == "up" else -0.5}
    rd = Rider.__new__(Rider)
    rd.e, rd._packs, rd._mind_s = e, (0.0, {}), 0.0
    x = rd.ext("NIFTY", now, {1: True, -1: False})
    assert x[1]["ghanshyam"] == 1 and x[-1]["ghanshyam"] == -1 and "2 with" in x[1]["ghanshyam_why"]
    assert x[1]["strategies"] == -1 and x[1]["forecast"] == 1 and x[-1]["forecast"] == -1
    assert "Pg3" not in x[1]["ghanshyam_why"]                                              # older than 15 minutes
    assert rd.ext("SBIN", now, {1: True, -1: True})[1].get("ghanshyam") is None             # no Ghanshyam view on a stock


def test_the_live_record_is_split_by_confidence(tmp_path):
    import datetime as dt
    from live.rider import Rider
    from live.store import Store
    from live.trials import Trials
    st = Store(tmp_path / "live.db")
    Trials(st, "upstox", log=lambda m: None)
    day = dt.date.today().isoformat()
    rows = [(88.0, "hit", 2.0), (86.0, "miss", -1.0), (78.0, "time", 0.2), (60.0, "miss", -1.0), (None, "hit", 1.5)]
    with st.lock:
        for sc, res, rx in rows:
            data = {"rider": {"confidence": {"score": sc}}} if sc is not None else {"rider": {}}
            st.db.execute("INSERT INTO trial_alerts(ts, day, source, sym, kind, direction, data, result, r_exit) "
                          "VALUES (0, ?, 'upstox', 'NIFTY', 'trend_rider', 'up', ?, ?, ?)", (day, json.dumps(data), res, rx))
        st.db.commit()
    v = Rider.by_confidence(st, "upstox")
    b = v["buckets"]
    assert v["min"] == 75 and v["strong"] == 85
    assert b["strong"]["n"] == 2 and b["strong"]["hit_share"] == 0.5 and b["strong"]["avg_r"] == 0.5
    assert b["ok"]["n"] == 1 and b["weak"]["n"] == 1 and b["weak"]["avg_r"] == -1.0 and b["none"]["n"] == 1
