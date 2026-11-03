"""The bigger picture on every alert (2.28): daily / weekly trend, results ahead, index events. Recorded and shown as
warnings only; the champion model, score, Focus and risk verdict do not change."""
import datetime as dt
import json

import numpy as np

from live import htf, riskgate
from live.context import Context, parse_events


def _candles(n=300, slope=1.0, start=dt.date(2025, 6, 2)):
    out, d, px = [], start, 1000.0
    while len(out) < n:
        if d.weekday() < 5:
            px += slope + np.sin(len(out) / 3)
            out.append([d.isoformat() + "T00:00:00+05:30", px - 2, px + 5, px - 5, px, 1000, 0])
        d += dt.timedelta(days=1)
    return out, d


def test_daily_trend_uses_finished_days_only():
    cs, today = _candles(300, 1.0)
    f = htf.daily(cs, today)
    assert f["trend"] == 1 and f["w_trend"] == 1 and f["st"] == 1 and f["pos20"] > 0.8
    crash = cs + [[today.isoformat() + "T00:00:00+05:30", 1, 1, 1, 1, 1, 0]]       # today's unfinished candle
    assert htf.daily(crash, today) == f
    down, today2 = _candles(300, -1.0)
    assert htf.daily(down, today2)["trend"] == -1
    up = htf.aligned(f, "up", f["close"])
    dn = htf.aligned(f, "down", f["close"])
    assert up["htf_d_al"] == 1 and dn["htf_d_al"] == -1 and up["htf_room_atr"] >= 0
    assert "daily ↑" in htf.words(f, "up")


def test_results_calendar_parsing_and_next_event(tmp_path):
    cal = [{"symbol": "TCS", "company": "TCS", "purpose": "Financial Results/Dividend", "bm_desc": "To consider results", "date": "05-Nov-2026"},
           {"symbol": "INFY", "purpose": "Fund Raising", "date": "10-Nov-2026"}, {"junk": 1}]
    bm = {"data": [{"bm_symbol": "SBIN", "bm_date": "06-Nov-2026", "bm_purpose": "Financial Results"}]}
    ev = parse_events(cal)
    assert [e["sym"] for e in ev] == ["TCS", "INFY"] and ev[0]["results"] and not ev[1]["results"] and ev[0]["date"] == "2026-11-05"
    assert parse_events(bm)[0]["sym"] == "SBIN" and parse_events(bm)[0]["results"]
    assert parse_events("nonsense") == [] and parse_events(None) == []
    c = Context(tmp_path, log=lambda *a: None, live=False)
    c.events_cal = ev
    n = c.next_event("TCS", dt.date(2026, 11, 3))
    assert n["days"] == 2 and n["results"]
    assert c.next_event("TCS", dt.date(2026, 11, 6)) is None


def _gate(bigger=None):
    a = {"sym": "TCS", "direction": "up", "data": {"setup_levels": {"rr": 2.0}}}
    opt = {"ltp": 50, "ask": 50.5, "bid": 49.5, "lot": 175}
    return riskgate.check(a, opt, now=dt.datetime(2026, 11, 4, 10, 30), paper_cfg={"capital": 500000, "risk_pct": 1.0, "stop_pct": 20},
                          guard={}, open_syms=set(), bigger=bigger)


def test_risk_verdict_never_changes_only_warnings_are_added():
    base = _gate()
    big = {"event": {"date": "2026-11-05", "days": 1, "results": True}, "daily": {"trend": -1, "pos20": 0.97}}
    with_big = _gate(big)
    for k in ("verdict", "reasons", "hard", "lots", "max_loss"):
        assert base[k] == with_big[k], k
    w = " ".join(with_big["warnings"])
    assert "Results tomorrow" in w and "Against the daily trend" in w and "20-day high" in w
    assert _gate({})["warnings"] == base["warnings"]


def test_champion_model_is_unchanged_by_the_new_facts(tmp_path):
    from live.learn import NEW, Ranker
    rng = np.random.default_rng(0)
    rows = []
    for i in range(400):
        f = {"kind": "orb", "direction": "up", "regime": "trend", "severity": 2, "agree": int(rng.integers(0, 4)),
             "against": int(rng.integers(0, 3)), "rel_vol": float(rng.random() * 3), "chg_al": float(rng.normal())}
        y = "hit" if f["agree"] + rng.normal() > 2 else "miss"
        rows.append({"result": y, "features": f, "o0": 10, "ts": i, "day": f"2026-10-{1 + i // 40:02d}", "o60": 1.0})
    plain = [{**r, "features": json.dumps(r["features"])} for r in rows]
    extra = [{**r, "features": json.dumps({**r["features"], **{k: float(rng.normal()) for k in NEW}})} for r in rows]
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = Ranker(tmp_path / "a", "sim").train(plain)
    b = Ranker(tmp_path / "b", "sim").train(extra)
    hgb = lambda info: next(m["auc"] for m in info["models"] if m["kind"] == "hgb")
    assert hgb(a) == hgb(b)                                         # same champion on the same unseen days
    assert any(m["kind"] == "hgb_plus" for m in b["models"]) and not any(m["kind"] == "hgb_plus" for m in a["models"])
    assert b["model"] == "hgb" or next(m["auc"] for m in b["models"] if m["kind"] == "hgb_plus") >= hgb(b) + 0.02


def test_lab_daily_trend_fact_and_queue_untested(tmp_path):
    from lab import factory as F, library
    for k in ("d_trend", "d_vs_ema20", "d_pos20"):
        assert k in F.FEATURES
    o = h = l = c = np.linspace(100, 101, 20)
    f = F.bar_features(o, h + 0.2, l - 0.2, c, {"d_trend": 1, "d_pos20": 0.9}, [np.nan] * 20, [np.nan] * 20)
    assert (f["d_trend"] == 1).all() and np.isnan(f["d_vs_ema20"]).all()
    lib = library.rules()[:30]
    q1 = F.queue_untested(tmp_path, lib, source="library")
    st = F.load(tmp_path)
    st["rules"] = {x["id"]: x for x in st["ideas"][:10]}            # pretend 10 were tested in an earlier run
    st["ideas"] = []
    F.save(tmp_path, st)
    q2 = F.queue_untested(tmp_path, lib, source="library")
    assert q1["queued"] == 30 and q2["queued"] == 20 and q2["already_tested"] == 10


def test_live_lab_rules_get_the_same_daily_facts_as_the_lab():
    import inspect
    from live import chartdata
    src = inspect.getsource(chartdata.ChartData._factory)
    for k in ("d_trend", "d_vs_ema20", "d_pos20"):
        assert f'"{k}"' in src
