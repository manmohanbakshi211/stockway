"""2.37 shadow Focus: challengers judge the same alerts as the current Focus, are saved with them and change nothing;
the honest record comes from earlier days of the test bench only; a type cap keeps one alert type from taking over;
the comparison and the Alerts page view work on live and replayed days."""
import json
import time

import pytest

from live import focuslab as FL
from live.bench import db_for
from live.store import Store

DAY0, DAY1 = "2026-11-03", "2026-11-04"
T1 = 1793784600.0                         # 2026-11-04 10:00 IST


def _v(**kw):
    return FL._validate(kw.pop("name", "t"), kw)


def _alert(kind, sym, direction="up", idx=False):
    return {"kind": kind, "sym": sym, "direction": direction, "is_index": idx}


def _bench(store, day, kind, nets, style="buy", variant=None, idx=0, alert_id=None):
    b = db_for(store)
    with b.lock:
        for k, n in enumerate(nets):
            b.db.execute("INSERT INTO bench_trades(ts, day, source, sym, kind, variant, style, direction, is_index, alert_id, "
                         "lot, entry, net, cost_lot, risk_lot) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (T1 - 86400 + k, day, "upstox", "X", kind, variant, style, "up", idx,
                          alert_id if alert_id is not None else None, 50, 100.0, float(n), 5000.0, 1000.0))
        b.db.commit()


def test_versions_load_and_a_broken_one_is_skipped(tmp_path, monkeypatch):
    real = FL.load()
    assert not real["error"] and not real["errors"] and real["main"] == "honest_cap30"
    assert {"honest_cap30", "cap30", "honest"} <= set(real["versions"])
    f = tmp_path / "f.json"
    f.write_text(json.dumps({"main": "nope", "versions": {"good": {"rank": "none"}, "bad": {"rank": "magic"},
                                                          "worse": {"type_cap": 3}}}))
    monkeypatch.setattr(FL, "FILE", f)
    FL._C.clear()
    try:
        out = FL.load()
        assert list(out["versions"]) == ["good"] and out["main"] == "good" and len(out["errors"]) == 2
        f.write_text("{ not json")
        assert FL.load()["error"] and list(FL.load()["versions"]) == ["honest_cap30"]     # built-in fallback
    finally:
        FL._C.clear()


def test_type_cap_and_symbol_gap(tmp_path):
    lab = FL.FocusLab(Store(tmp_path / "live.db"), "upstox", log=lambda m: None)
    v = _v(name="cap", rank="none", type_cap=0.3, cap_floor=2, gap_min=60)
    picked = []
    for i in range(40):                                  # 30 sharp moves, 10 others, all strong, all different symbols
        kind = "sharp_move" if i % 4 else ["orb", "volume_surge"][i // 4 % 2]
        ok, why = lab._judge(v, _alert(kind, f"S{i}"), 3, False, True, T1 + i * 60, DAY1)
        if ok:
            lab._count("cap", DAY1, kind, f"S{i}", T1 + i * 60)
            picked.append(kind)
        else:
            assert why.startswith("type cap: ")
    share = picked.count("sharp_move") / len(picked)          # 3 types today: an even split is the most it can be
    assert share <= 0.4 and picked.count("orb") == 5 and picked.count("volume_surge") == 5 and len(picked) == 16
    lab2 = FL.FocusLab(Store(tmp_path / "b.db"), "upstox", log=lambda m: None)
    got = []
    for i in range(120):                                 # many types: no type above ~30%
        kind = ["a", "a", "a", "a", "b", "b", "c", "d", "e", "f"][i % 10]
        if lab2._judge(v, _alert(kind, f"M{i}"), 3, False, True, T1 + i * 60, DAY1)[0]:
            lab2._count("cap", DAY1, kind, f"M{i}", T1 + i * 60)
            got.append(kind)
    assert max(got.count(k) for k in set(got)) / len(got) <= 0.31 and len(set(got)) == 6
    ok, why = lab._judge(v, _alert("orb", "S4"), 3, False, True, T1 + 41 * 60, DAY1)    # S4 was picked 37 min ago
    assert not ok and "already picked" in why
    assert lab._judge(v, _alert("pcr_shift", "NEW"), 3, True, True, T1, DAY1) == (False, "demoted on the scorecard")
    assert lab._judge(v, _alert("pcr_shift", "NEW"), 2, False, False, T1, DAY1)[0] is False   # not strong enough
    one = _v(name="one", rank="none", type_cap=1.0)
    assert all(lab._judge(one, _alert("sharp_move", f"Z{i}"), 3, False, True, T1, DAY1)[0] for i in range(10))


def test_honest_grade_from_earlier_days_only(tmp_path):
    store = Store(tmp_path / "live.db")
    _bench(store, DAY0, "unusual_options", [-600, -450, -700, -300, -520] * 4)       # losing, 20 trades
    _bench(store, DAY0, "volume_surge", [900, 700, -200, 1100, 650] * 4)              # winning, 20 trades
    _bench(store, DAY0, "orb", [400, 300])                                            # too few
    _bench(store, DAY0, "volume_surge", [-5000] * 30, variant="15m")                  # a trial version: not the alert type
    _bench(store, DAY0, "volume_surge", [-5000] * 30, style="sell")                   # another style: not used
    _bench(store, DAY1, "orb", [3000] * 40)                                           # today: never used (no peeking)
    book = FL.honest_book(store, "upstox", DAY1, "buy", 30, 15)
    assert book[("unusual_options", 0)]["verdict"] == "bad" and book[("volume_surge", 0)]["verdict"] == "good"
    assert book[("volume_surge", 0)]["n"] == 20 and book[("orb", 0)]["n"] == 2 and book[("orb", 0)]["verdict"] == "new"
    lab = FL.FocusLab(store, "upstox", log=lambda m: None)
    v = _v(name="h", rank="honest", min_n=15, good_min_sev=2, type_cap=1.0)
    ok, why = lab._judge(v, _alert("unusual_options", "A"), 3, False, True, T1, DAY1)
    assert not ok and why.startswith("honest record losing (−₹514 a trade × 20)")
    ok, why = lab._judge(v, _alert("volume_surge", "B"), 2, True, False, T1, DAY1)    # demoted + weak: winning type still in
    assert ok and why.startswith("honest record winning")
    assert lab._judge(v, _alert("volume_surge", "C"), 1, False, False, T1, DAY1)[0] is False
    assert lab._judge(v, _alert("orb", "D"), 3, True, True, T1, DAY1) == (False, "demoted on the scorecard")
    ok, why = lab._judge(v, _alert("orb", "E"), 3, False, True, T1, DAY1)
    assert ok and "too few trades yet" in why


def _save(store, day, ts, sym, kind, sev=3, focus=False, muted=None, fch=None, direction="up"):
    d = {"focus": focus, "muted": muted}
    if fch is not None:
        d["focus_ch"] = fch
    return store.add_alert({"ts": ts, "day": day, "source": "upstox", "sym": sym, "kind": kind, "direction": direction,
                            "severity": sev, "title": f"{sym} {kind}", "detail": "", "price": 100, "data": d})


def test_decide_saves_every_version_and_warm_restores_the_day(tmp_path):
    store = Store(tmp_path / "live.db")
    lab = FL.FocusLab(store, "upstox", log=lambda m: None)
    assert lab.decide({"kind": "news", "sym": "NIFTY", "direction": "neutral"}, sev0=3, muted=False, strong=True,
                      ts=T1, day=DAY1) is None                       # market-wide news follows the current Focus
    for i, kind in enumerate(["sharp_move"] * 4 + ["orb"] + ["sharp_move"] * 3):
        a = {**_alert(kind, f"S{i}"), "severity": 3}
        fch = lab.decide(a, sev0=3, muted=False, strong=True, ts=T1 + i, day=DAY1)
        assert set(fch) == set(FL.load()["versions"]) and all({"pick", "why"} <= set(r) for r in fch.values())
        _save(store, DAY1, T1 + i, f"S{i}", kind, fch=fch)
    st = lab.status()                    # one type so far: the floor (2); with a second type, an even split at most
    assert st["today"]["honest"]["picks"] == 8 and st["today"]["cap30"]["picks"] == 3 and st["today"]["honest_cap30"]["picks"] == 3
    again = FL.FocusLab(store, "upstox", log=lambda m: None)
    assert again.warm(DAY1) == 8 + 3 + 3
    fch = again.decide({**_alert("sharp_move", "S9"), "severity": 3}, sev0=3, muted=False, strong=True, ts=T1 + 9, day=DAY1)
    assert fch["cap30"]["pick"] is False and fch["honest"]["pick"] is True


def test_a_failing_version_never_stops_the_others(tmp_path, monkeypatch):
    lab = FL.FocusLab(Store(tmp_path / "live.db"), "upstox", log=lambda m: None)
    real = lab._judge
    monkeypatch.setattr(lab, "_judge", lambda v, *a: (_ for _ in ()).throw(RuntimeError("boom")) if v["name"] == "cap30" else real(v, *a))
    fch = lab.decide({**_alert("orb", "X"), "severity": 3}, sev0=3, muted=False, strong=True, ts=T1, day=DAY1)
    assert "cap30" not in fch and fch["honest"]["pick"] is True and lab.status()["errors"]["cap30"] == "boom"


def test_compare_live_and_replayed_days(tmp_path):
    store = Store(tmp_path / "live.db")
    ids0 = [_save(store, DAY0, T1 - 86400 + i * 60, f"S{i}", "unusual_options" if i % 3 else "orb", focus=True) for i in range(12)]
    _save(store, DAY0, T1 - 86400 + 999, "NIFTY", "news", focus=True, direction="neutral")
    lab = FL.FocusLab(store, "upstox", log=lambda m: None)
    _save(store, DAY1, T1 - 600, "EARLY", "orb", focus=True)                # before the version started: not compared
    ids1 = []
    for i in range(10):
        a = {**_alert("unusual_options", f"T{i}"), "severity": 3}
        fch = lab.decide(a, sev0=3, muted=False, strong=True, ts=T1 + i * 60, day=DAY1)
        ids1.append(_save(store, DAY1, T1 + i * 60, f"T{i}", "unusual_options", focus=i < 5, fch=fch))
    _bench(store, DAY0, "unusual_options", [-300], alert_id=ids0[1])
    _bench(store, DAY0, "orb", [500], alert_id=ids0[0])
    out = FL.compare(store, "upstox", 10, today=DAY1, lc={"focus_min_severity": 3})
    sel = {s["id"]: s for s in out["selectors"]}
    assert out["days"] == [DAY0, DAY1] and sel["focus"]["champion"] and sel["all"]["baseline"]
    assert sel["focus"]["n"] == 12 + 5 and sel["all"]["n"] == 12 + 10           # the early alert and the news are left out
    assert sel["focus"]["top"][0]["kind"] == "unusual_options" and sel["focus"]["top_pct"] == round((8 + 5) / 17 * 100)
    assert sel["cap30"]["replayed_days"] == 1 and sel["cap30"]["live_days"] == 1
    d0 = next(r for r in out["per_day"] if r["day"] == DAY0)
    assert d0["mode"]["cap30"] == "replayed" and d0["sel"]["cap30"]["n"] == 9 and d0["sel"]["cap30"]["top_pct"] == 56   # 2 types: about even
    assert sel["focus"]["bench"]["n"] == 2 and sel["focus"]["bench"]["net"] == 200
    assert FL.picks(store, "upstox", DAY1, "honest", today=DAY1) == set(ids1)
    assert FL.picks(store, "upstox", DAY1, "cap30", today=DAY1) == set(ids1[:2])   # one type that day: the floor only
    json.dumps(out, default=str)


def test_engine_records_the_challengers_and_nothing_else_reads_them(tmp_path):
    from radar.config import load_config
    from live import selfcheck
    from live.engine import LiveEngine

    def cfg():
        c = load_config()
        c["live"] = {**(c.get("live") or {}), "source": "sim", "sim_speed": 240, "auto_start": False, "news": False,
                     "filings": False, "briefs": False}
        return c
    e = LiveEngine(cfg, tmp_path)
    e.start("sim")
    try:
        t0 = time.time()
        while time.time() - t0 < 90:
            time.sleep(2)
            if e.focuslab is not None and e.focuslab.n_judged >= 5:
                break
        assert e.focuslab is not None and e.focuslab.n_judged >= 5
    finally:
        e.stop()
    with e.store.lock:
        rows = e.store.db.execute("SELECT direction, data FROM alerts WHERE source='sim'").fetchall()
    dirs = [json.loads(d) for di, d in rows if di in ("up", "down")]
    assert dirs and all(set((x.get("focus_ch") or {})) == set(FL.load()["versions"]) for x in dirs[-5:])
    assert all("focus_ch" not in json.loads(d) for di, d in rows if di not in ("up", "down"))
    chk = {c["id"]: c for c in selfcheck.static_checks(load_config())}
    assert chk["focus_shadow"]["ok"] is True
    src = open(LiveEngine._emit.__code__.co_filename).read()
    tg = [ln for ln in src.splitlines() if "self.telegram.send(telegram_text(a))" in ln or "(focus or not lc.get(" in ln]
    assert tg and not any("fch" in ln or "focus_ch" in ln for ln in tg)


def test_alerts_page_shadow_view(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from webapp import live_api
    store = Store(tmp_path / "live.db")
    lab = FL.FocusLab(store, "upstox", log=lambda m: None)
    keep = []
    for i, kind in enumerate(["orb", "sharp_move", "orb", "orb", "orb"]):
        fch = lab.decide({**_alert(kind, f"Q{i}"), "severity": 3}, sev0=3, muted=False, strong=True, ts=T1 + i, day=DAY1)
        keep.append(_save(store, DAY1, T1 + i, f"Q{i}", kind, focus=True, fch=fch))
    import types
    clk = types.SimpleNamespace(now=lambda: __import__("datetime").datetime(2026, 11, 4, 12, 0))
    fake = types.SimpleNamespace(store=store, source="upstox", cfg=lambda: {"live": {"source": "upstox"}}, clock=clk,
                                 focuslab=lab)
    monkeypatch.setattr(live_api, "engine", lambda: fake)
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(live_api.router)
    c = TestClient(app)
    d = c.get(f"/api/live/alerts?day={DAY1}&view=shadow").json()
    assert d["view"] == "shadow" and d["shadow"]["version"] == "honest_cap30" and d["counts"]["shadow"] == 3
    assert {a["id"] for a in d["alerts"]} == set(keep[:3]) and d["counts"]["all"] == 5
    f = c.get("/api/live/focus_shadow?days=5").json()
    assert f["main"] == "honest_cap30" and f["status"]["judged"] == 5 and len(f["selectors"]) == 2 + len(FL.load()["versions"])


@pytest.fixture(autouse=True)
def _fresh_caches():
    FL._BOOK.clear()
    FL._DAY.clear()
    yield
    FL._BOOK.clear()
    FL._DAY.clear()
