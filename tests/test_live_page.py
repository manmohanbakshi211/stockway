"""2.29: Live page when the market is closed, world board, extremes, merged levels, Alerts views, long chart history,
the all-timeframes report."""
import datetime as dt
import json
import time

import numpy as np

from live import giftnifty, levels
from live.clock import IST


def test_levels_merge_and_order():
    lv = levels.key_levels(23810, [("Put OI wall", 23700), ("Yesterday's low", 23705), ("S1", 23690), ("Call OI wall", 23850),
                                   ("R1", 23860), ("Pivot", 23400), ("Max pain", 23750), ("Junk", None), ("Far", 30000)])
    s1, r1 = lv["support"][0], lv["resistance"][0]
    assert r1["price"] > 23810 > s1["price"]
    assert {"Call OI wall", "R1"} <= set(r1["names"])                     # 23850 and 23860 are one zone
    assert {"Put OI wall", "Yesterday's low", "S1"} & set(lv["support"][1]["names"] + s1["names"])
    assert [z["price"] for z in lv["support"]] == sorted([z["price"] for z in lv["support"]], reverse=True)
    assert all(z["strength"] >= 1 for z in lv["support"] + lv["resistance"])
    assert levels.key_levels(None, [("R1", 1)]) == {"support": [], "resistance": []}


def test_extremes_from_monthly_chart():
    from live.world import extremes_from_chart
    ts = [int(dt.datetime(2008 + i // 12, 1 + i % 12, 1, tzinfo=IST).timestamp()) for i in range(30)]
    hi = [100 + i for i in range(30)]
    lo = [50 + i for i in range(30)]
    lo[3] = 10
    js = {"chart": {"result": [{"timestamp": ts, "meta": {"fiftyTwoWeekHigh": 129, "fiftyTwoWeekLow": 70},
                                "indicators": {"quote": [{"high": hi, "low": lo}]}}]}}
    e = extremes_from_chart(js)
    assert e["ath"] == 129 and e["atl"] == 10 and e["atl_date"] == "2008-04" and e["since"] == "2008" and e["hi52"] == 129
    assert extremes_from_chart({}) is None


def test_gift_parse_keeps_session_details():
    d = {"data": [{"SYMBOL": "NIFTY", "INSTRUMENT": "Index Futures", "LASTPRICE": 23766.5, "CHANGE": 6.5, "PERCHANGE": 0.03,
                   "EXPIRYDATE": "23-Nov-2026", "OPEN": 23554, "HIGH": 23887.5, "LOW": 23505.5, "CLOSE": 23760, "ATP": 23706.2,
                   "CONTRACTSTRADED": 83010, "TIMESTMP": "03-Nov-2026 22:45:08"}]}
    g = giftnifty.parse(d)
    assert g["open"] == 23554 and g["day_h"] == 23887.5 and g["prev"] == 23760 and g["contracts"] == 83010
    g2 = giftnifty.parse({"data": [{"SYMBOL": "NIFTY", "INSTRUMENT": "FUT", "LASTPRICE": 1, "EXPIRYDATE": "x", "HIGH": "-"}]})
    assert g2["day_h"] is None


def _engine(tmp_path):
    from live.engine import LiveEngine
    return LiveEngine(lambda: {"live": {}}, tmp_path)


def test_index_cards_when_the_scanner_is_off(tmp_path):
    e = _engine(tmp_path)
    card = {"sym": "NIFTY", "name": "Nifty 50", "is_index": True, "spot": 23810.9, "chg_pct": 1.58, "day_h": 23905, "day_l": 23482.8,
            "opt": {"support": 23700, "resistance": 23850, "max_pain": 23750, "pcr": 1.2}}
    (tmp_path / "last_session.json").write_text(json.dumps({"asof": time.time() - 3600, "day": "2026-11-03", "indices": [card],
                                                             "daily": {"NIFTY": {"htf": {"hi20": 24100, "lo20": 23000, "trend": 1, "w_trend": 0}}}}))
    e.world.extremes = {"^NSEI": {"ath": 26373.2, "ath_date": "2026-01", "atl": 2252.75, "atl_date": "2008-10", "since": "2007",
                                  "hi52": 26373.2, "lo52": 22179.9}}
    cards = e.index_cards()
    assert cards and cards[0]["closed"] and cards[0]["opt"]["pcr"] == 1.2
    assert cards[0]["ext"]["ath"] == 26373.2 and cards[0]["ext"]["from_ath_pct"] < 0
    names = [n for z in cards[0]["levels"]["support"] + cards[0]["levels"]["resistance"] for n in z["names"]]
    assert "Put OI wall" in names and "Call OI wall" in names
    assert "Yesterday's high" in names                                 # the last session is yesterday now
    assert cards[0]["trend"] == {"daily": 1, "weekly": 0}
    snap = e.snapshot()
    assert snap["indices"][0]["sym"] == "NIFTY" and snap["last_session"]["day"] == "2026-11-03"


def test_world_board_groups(tmp_path):
    e = _engine(tmp_path)
    e.world.rows = {"^DJI": {"last": 51700.0, "chg_pct": -0.5, "prev": 51983.0, "spark_day": [1, 2, 3]},
                    "INR=X": {"last": 94.7, "chg_pct": 0.1}}
    b = e.markets_board()
    gs = {g["group"]: [x["name"] for x in g["items"]] for g in b["groups"]}
    assert gs["US"] == ["Dow Jones"] and "USD/INR" in gs["Money & commodities"]


def test_alerts_views_and_counts(tmp_path, monkeypatch):
    from webapp import live_api
    e = _engine(tmp_path)
    day = "2026-11-03"
    for i, (sev, muted, focus) in enumerate([(3, None, True), (3, None, False), (2, None, False), (2, "demoted", False), (1, None, False)]):
        e.store.add_alert({"ts": 1_000_000 + i, "day": day, "source": "upstox", "sym": "NIFTY", "kind": "orb", "direction": "up",
                           "severity": sev, "title": f"a{i}", "detail": "", "price": 1, "data": {"muted": muted, "focus": focus}})
    monkeypatch.setattr(live_api, "engine", lambda: e)
    act = live_api.live_alerts(day=day, view="act")
    foc = live_api.live_alerts(day=day, view="focus")
    al = live_api.live_alerts(day=day, view="all")
    assert act["counts"] == foc["counts"] == al["counts"]
    assert len(act["alerts"]) == 2 and len(al["alerts"]) == 5
    assert {a["title"] for a in foc["alerts"]} == {"a0", "a1", "a2"}         # focus + important, not the demoted one


def test_long_history_candles_and_periods(tmp_path, monkeypatch):
    from live import mtf as M
    e = _engine(tmp_path)
    m = M.MTF(e)
    day0 = dt.date(2026, 10, 1)
    rows = []
    for d in range(30):
        day = day0 + dt.timedelta(days=d)
        if day.weekday() >= 5:
            continue
        t0 = M._session_start(day)
        for k in range(75):
            px = 100 + d + k * 0.01
            rows.append([t0 + k * 300, px, px + 0.2, px - 0.2, px + 0.05, 10])
    monkeypatch.setattr(m, "base_rows", lambda sym, base, days: M.daily_from(rows) if base == "D" else M.resample(rows, base) if base != 1 else rows)
    monkeypatch.setattr(m, "_dhan", lambda: None)
    monkeypatch.setattr(m, "_yahoo", lambda sym, base: [])
    r = m.candles("NIFTY", "15m", limit=100)
    assert len(r["candles"]) == 100 and [c["time"] for c in r["candles"]] == sorted(c["time"] for c in r["candles"])
    assert r["more"] is True
    older = m.candles("NIFTY", "15m", before=r["candles"][0]["time"] - M.IST_SHIFT, limit=100)
    assert older["candles"][-1]["time"] < r["candles"][0]["time"]
    t = m.candles("NIFTY", "5m", tail=3)
    assert len(t["candles"]) == 3
    d = M.daily_from(rows)
    w, mo = M.period_from(d, "W"), M.period_from(d, "M")
    assert len(mo) == 1 and 4 <= len(w) <= 6
    assert mo[0][4] == d[-1][4] and mo[0][1] == d[0][1] and mo[0][2] == max(r_[2] for r_ in d)


def test_all_timeframes_report_reads_small_to_large():
    from live.mtf import MTF
    def panel(trend, up):
        cs = [{"close": 100 + (i if up else -i) * 0.5} for i in range(60)]
        return {"candles": cs, "trend": trend, "structure": "uptrend" if up else "downtrend",
                "levels": [{"name": "Swing high", "price": 200, "group": "swing"}, {"name": "PDL", "price": 50, "group": "prev"}]}
    v = {"spot": 120, "confluence": [], "panels": {"1D": panel("up", True), "4H": panel("up", True), "1H": panel("up", True),
                                                   "15m": panel("down", False), "5m": panel("down", False)}}
    r = MTF.report(v)
    assert [x["tf"] for x in r["per_tf"]] == ["5m", "15m", "1H", "4H", "1D"]          # 2.70: 4H replaces 75m
    assert "Pullback inside an uptrend" in r["verdict"] and r["bias"] > 0
    assert r["per_tf"][0]["res"]["price"] == 200 and r["per_tf"][0]["sup"]["price"] == 50
