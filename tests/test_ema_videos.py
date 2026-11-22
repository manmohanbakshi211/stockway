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
