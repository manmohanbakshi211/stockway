"""2.87: Trend Rider in the Strategy Lab (lab/rider_lab.py): the live setup and exit on every Lab day, graded by days,
walk-forward and holdout. Reports only."""
import datetime as dt
import inspect
import json

import numpy as np

from lab import rider_lab as RL
from lab.data import Day

D0 = dt.date(2026, 3, 2)


def _day(date=D0, sym="NIFTY", drift=0.0, start=25000.0, seed=1):
    """A synthetic Lab day: minute spot, and ATM calls / puts priced off it (0.5 delta, slow decay)."""
    rng = np.random.default_rng(seed)
    spot = start + np.cumsum(rng.normal(drift, 3.0, 375))
    px = {}
    for k in np.arange(24000, 26050, 50.0):
        m = np.arange(375)
        ce = np.maximum(1.0, 120 + 0.5 * (spot - k) - 0.05 * m)
        pe = np.maximum(1.0, 120 - 0.5 * (spot - k) - 0.05 * m)
        px[(float(k), "CE")] = {"c": ce, "h": ce, "l": ce, "iv": np.full(375, 12.0), "oi": np.zeros(375)}
        px[(float(k), "PE")] = {"c": pe, "h": pe, "l": pe, "iv": np.full(375, 12.0), "oi": np.zeros(375)}
    return Day(date=date, sym=sym, step=50.0, spot=spot, px=px)


def test_pick_follows_live_first_look_cooldown_and_two_a_day():
    sigs = [[2, 1, 0, 1, 0], [3, 1, 0, 1, 0], [4, 1, 0, 1, 0], [8, -1, 0, 1, 0], [30, 1, 0, 1, 0]]
    assert [s[0] for s in RL.pick(sigs, 15, 15, 2)] == [2, 8]          # 09:30, then 15+ minutes later
    assert [s[0] for s in RL.pick(sigs, 30, 15, 2)] == [8, 30]         # first look 09:45: the 09:30-09:40 bars drop
    assert [s[0] for s in RL.pick(sigs, 15, 15, 1)] == [2]


def test_simulate_trades_the_rider_exit_and_the_bench_exit_with_charges():
    d = _day(drift=1.5)                                                # a steady up day
    m0 = 5 * 6 + 4
    s0 = float(d.spot[m0])
    sig = [6, 1, s0 - 15.0, 8.0, s0]
    r = RL.simulate(d, "NIFTY", sig, 65, dict(RL.X.DEFAULT))
    assert r and r["why"] in ("trail", "time", "no move", "stop") and r["mins"] > 0
    f = RL.simulate(d, "NIFTY", sig, 65, fixed=RL.FIXED)
    assert f and f["why"] in ("target", "stop loss", "time")
    flat = (f["exit"] - f["entry"]) * 65
    assert f["net"] < flat                                           # charges come off
    down = _day(drift=-2.0, seed=3)
    s0 = float(down.spot[m0])
    r2 = RL.simulate(down, "NIFTY", [6, 1, s0 - 6.0, 4.0, s0], 65, dict(RL.X.DEFAULT))
    assert r2["why"] in ("stop", "premium stop", "no move") and r2["net"] < 0


def test_the_setup_runs_on_lab_days_without_live_only_inputs():
    d = _day(drift=1.2, seed=5)
    ctx = {"pdh": 25010.0, "pdl": 24800.0, "datr": 180.0, "adr": 170.0, "dte": 3}
    sigs = RL._signals_day(d, "NIFTY", ctx, RL.R.cfg(), 65, 500000.0)
    assert set(sigs) == set(RL.ENTRIES)                              # 2.89: as live, EMA family counted, EMA trigger
    for s in sum(sigs.values(), []):                                 # [bar, dir, stop, atr, s0, ema10], inside windows
        assert len(s) == 6 and s[1] in (1, -1) and 5 * (s[0] + 1) >= RL.WIN_STARTS[0]
    assert {tuple(x[:2]) for x in sigs["base"]} <= {tuple(x[:2]) for x in sigs["trig"]}   # the trigger only adds


def _trades(per_day_by_month: dict, n=9):
    out = []
    for mo, val in per_day_by_month.items():
        for i in range(n):
            out.append({"date": f"{mo}-{10 + i:02d}", "sym": "NIFTY", "bar": 3, "dir": 1, "net": val + (i % 3 - 1) * 10.0,
                        "pct": val / 100, "why": "trail"})
    return out


def test_grade_by_days_with_holdout_and_walk_forward():
    months = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
    tr = {RL.V1: _trades({m: 50.0 for m in months}), "w30_nm4_em35": _trades({m: 120.0 for m in months}),
          "fixed": _trades({m: -20.0 for m in months})}
    g = RL.grade(tr)
    v = g["variants"]
    assert v[RL.V1]["days"] == 54 and v[RL.V1]["per_day"] == 50.0 and v[RL.V1]["lcb"] > 0
    assert g["best_on_train"] == "w30_nm4_em35" and v["w30_nm4_em35"]["holdout"]["days"] > 0
    assert g["holdout_from"] >= "2026-05"
    assert [m for m, _ in g["walk"]["months"]] == ["2026-04", "2026-05", "2026-06"]   # 3+ months and 20+ days before
    assert g["walk"]["walk"]["per_day"] == 120.0 and g["walk"]["bench_exit"]["per_day"] == -20.0
    assert g["verdict"].startswith("As live it makes money by days")
    assert RL.day_stats({})["days"] == 0 and RL.grade({RL.V1: []})["verdict"].startswith("no Trend Rider signal")


def test_run_end_to_end_caches_signals_and_writes_the_report(tmp_path, monkeypatch):
    import lab.data as LD
    days = [_day(date=D0 + dt.timedelta(days=i), drift=1.0, seed=i) for i in range(5)]
    monkeypatch.setattr(LD, "register_stocks", lambda d: [])
    monkeypatch.setattr(LD, "iter_days", lambda data_dir, sym, start=None, end=None: iter(days))
    calls = []

    def fake_signals(d, sym, ctx, c_, lot, capital, nifty_spot=None):
        calls.append(d.date)
        s0 = float(d.spot[34])
        return [[6, 1, s0 - 12.0, 6.0, s0], [12, 1, s0 - 12.0, 6.0, float(d.spot[64])]]
    monkeypatch.setattr(RL, "_signals_day", fake_signals)
    logs = []
    res = RL.run(tmp_path, log=logs.append, syms=["NIFTY"], out_dir=tmp_path / "out")
    assert len(calls) == 5 and res["sym_days"] == 5 and res["new_days"] == 5
    rep = json.loads((tmp_path / "out" / "rider_lab.json").read_text())
    assert rep["variants"][RL.V1]["trades"] == 10 and rep["variants"]["fixed"]["trades"] == 10
    assert set(rep["variants"]) >= {RL.V1, "fixed", "w45_nm99_em25"} and "Proxy" in rep["note"]
    calls.clear()
    res2 = RL.run(tmp_path, log=logs.append, syms=["NIFTY"], out_dir=tmp_path / "out")
    assert calls == [] and res2["new_days"] == 0                       # the second night reuses the signals
    assert any("Trend Rider test:" in m for m in logs)


def test_nightly_chain_runs_it_in_the_lab_process_paused_in_market_hours():
    from lab import proc
    from live.engine import LiveEngine
    assert "rider" in proc.JOBS and "rider_lab" in inspect.getsource(proc._inproc)
    src = inspect.getsource(LiveEngine._nightly_lab)
    assert 'proc.run("rider", self.data_dir' in src and "pause=self.lab_paused" in src


def test_lab_page_api_reads_both_reports_and_starts_a_run(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from lab import proc
    import webapp.server as S
    monkeypatch.setattr(S, "ROOT", tmp_path)
    (tmp_path / "data" / "lab").mkdir(parents=True)
    rep = RL.grade({RL.V1: _trades({"2026-01": 50.0}), "fixed": _trades({"2026-01": -5.0})})
    (tmp_path / "data" / "lab" / "rider_lab.json").write_text(json.dumps(rep))
    cl = TestClient(S.app)
    v = cl.get("/api/lab/rider").json()
    assert v["1y"]["variants"][RL.V1]["days"] == 9 and v["5y"] is None and v["busy"] is False
    ran = []
    monkeypatch.setattr(proc, "run", lambda job, d, **kw: ran.append((job, str(d))))
    assert cl.post("/api/lab/rider/run").json() == {"started": True}
    import time as _t
    for _ in range(50):
        if ran:
            break
        _t.sleep(0.05)
    assert ran and ran[0][0] == "rider"
    js = (RL.Path(__file__).resolve().parents[1] / "webapp" / "static" / "live.js").read_text() if hasattr(RL, "Path") else ""
    assert 'id="lab-rider"' in js and "/api/lab/rider" in js
