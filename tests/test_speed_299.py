"""2.99 (Mandeep, 23 Nov 2026, 15:03: "why the system is still slow ... check thoroughly"). Measured in the running app:
the conviction model split every one-row prediction over 10 threads; the Dhan scrip master (30 MB) was read again for
every stock chart; the 5-minute scorecard and the Alerts page held the store's writer lock while they read; every screen
read through one connection and one lock; the restart's gap fill kept the recording locked. Each fix gives the same
answers (tests below)."""
import csv
import datetime as dt
import threading
import time
import types

import numpy as np
import pytest

from live.store import Store, reading


def _alert(i, day="2026-11-23", src="upstox"):
    return {"ts": 1795400000.0 + i, "day": day, "source": src, "sym": "NIFTY", "kind": "orb_break", "direction": "up",
            "severity": 3, "title": f"t{i}", "detail": "d", "price": 1.0, "data": {"i": i, "focus": True}}


def _held(lock, secs=3.0):
    """Hold a lock in another thread; returns (started event, release event, thread)."""
    held, done = threading.Event(), threading.Event()

    def run():
        with lock:
            held.set()
            done.wait(secs)
    th = threading.Thread(target=run)
    th.start()
    held.wait(2)
    return done, th


# ---------------------------------------------------------------- the conviction model on one thread
def test_forest_predicts_on_one_thread_with_the_same_answers(tmp_path):
    import joblib
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from live import learn
    rng = np.random.default_rng(0)
    X = rng.normal(size=(600, 6))
    y = (X[:, 0] + rng.normal(size=600) > 0).astype(int)
    m = make_pipeline(StandardScaler(), RandomForestClassifier(n_estimators=40, n_jobs=-1, random_state=0)).fit(X, y)
    before = m.predict_proba(X[:50])
    learn.single_thread(m)
    assert m.steps[-1][1].n_jobs == 1
    assert np.allclose(before, m.predict_proba(X[:50]), rtol=0, atol=1e-12)
    joblib.dump((make_pipeline(RandomForestClassifier(n_estimators=5, n_jobs=-1).fit(X, y)), {"trained": True}, None),
                tmp_path / "ranker2_upstox.joblib")
    r = learn.Ranker(tmp_path, "upstox", log=lambda m: None)
    assert r.model.steps[-1][1].n_jobs == 1                      # loaded for live predictions on one thread
    assert learn.make_model("forest", ["a"]).steps[-1][1].n_jobs == -1       # training is unchanged
    learn.single_thread(object())                                # anything else: left alone


def test_ml_strategy_models_also_predict_on_one_thread(tmp_path, monkeypatch):
    import joblib
    from sklearn.ensemble import RandomForestClassifier
    from lab import mlstrat
    f = tmp_path / "lab" / "ml" / "m.joblib"
    f.parent.mkdir(parents=True)
    joblib.dump(RandomForestClassifier(n_estimators=3, n_jobs=-1).fit([[0, 1], [1, 0]] * 5, [0, 1] * 5), f)
    monkeypatch.setattr(mlstrat, "matrix", lambda d, dirs: [[0, 1]])
    mlstrat._CACHE.pop(f, None)
    from lab import factory as F
    feats = {k: [0.0] for k in F.ML_FEATURES}
    feats["tod"] = [5]
    mlstrat.fires(tmp_path, {"ml": "m.joblib", "win": [0, 10], "dir": 1, "thr": 0.5}, feats)
    assert mlstrat._CACHE[f].n_jobs == 1


# ---------------------------------------------------------------- the Dhan scrip master, read once
COLS = ["SEM_EXM_EXCH_ID", "SEM_INSTRUMENT_NAME", "SEM_TRADING_SYMBOL", "SEM_SERIES", "SEM_SMST_SECURITY_ID", "SEM_LOT_UNITS"]


def _scrip_file(d, rows):
    f = d / "dhan_scrips_20261123.csv"
    with open(f, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(COLS)
        w.writerows(rows)
    return f


ROWS = [["NSE", "EQUITY", "RELIANCE", "EQ", "2885", ""], ["NSE", "EQUITY", "TCS", "EQ", "11536", ""],
        ["NSE", "EQUITY", "TCS", "BE", "99999", ""], ["BSE", "EQUITY", "RELIANCE", "EQ", "500325", ""],
        ["NSE", "OPTSTK", "RELIANCE-Nov2026-1300-CE", "", "1", "500"], ["NSE", "OPTSTK", "RELIANCE-Nov2026-1320-CE", "", "2", "250"],
        ["NSE", "OPTSTK", "TCS-Nov2026-4000-PE", "", "3", "175"], ["NSE", "EQUITY", "NOOPT", "EQ", "777", ""]]


def test_stock_ids_reads_the_scrip_master_once_with_the_same_answers(tmp_path, monkeypatch):
    from lab import dhan_hist as DH
    f = _scrip_file(tmp_path, ROWS)
    DH._SCRIP_MAPS.clear()
    reads = []
    real = csv.DictReader
    monkeypatch.setattr(DH.csv, "DictReader", lambda fh: reads.append(1) or real(fh))
    assert DH.stock_ids(tmp_path, ["RELIANCE"], fetch=False) == {"RELIANCE": {"id": 2885, "lot": 500}}
    assert DH.stock_ids(tmp_path, ["TCS", "NOOPT", "XYZ"], fetch=False) == {"TCS": {"id": 11536, "lot": 175},
                                                                            "NOOPT": {"id": 777, "lot": 1}}
    assert DH.stock_ids(tmp_path, None, fetch=False) == {"RELIANCE": {"id": 2885, "lot": 500}, "TCS": {"id": 11536, "lot": 175}}
    assert len(reads) == 1                                        # one read for all three answers
    time.sleep(0.01)
    _scrip_file(tmp_path, ROWS[:1] + [["NSE", "OPTSTK", "RELIANCE-Dec2026-1300-CE", "", "1", "505"]])
    assert DH.stock_ids(tmp_path, ["RELIANCE"], fetch=False) == {"RELIANCE": {"id": 2885, "lot": 505}}
    assert len(reads) == 2                                        # a new file is read again
    assert DH.stock_ids(tmp_path / "none", ["X"], fetch=False) == {}


# ---------------------------------------------------------------- reads that no longer wait for the writer
def test_every_thread_reads_on_its_own_connection(tmp_path):
    s = Store(tmp_path / "live.db")
    s.add_alert(_alert(1))
    conns = {}

    def look(k):
        conns[k] = s._rconn()
        conns[k + "_n"] = len(s.alerts(day="2026-11-23"))
    ths = [threading.Thread(target=look, args=(k,)) for k in ("a", "b")]
    [t.start() for t in ths]
    [t.join() for t in ths]
    assert conns["a"] is not conns["b"] and conns["a_n"] == conns["b_n"] == 1
    assert s._rconn() is s.rdb                                   # the thread that opened the store keeps its own
    done, th = _held(s.rlock)                                    # the 2.96 read lock no longer queues readers
    t0 = time.time()
    assert len(s.alerts(day="2026-11-23")) == 1 and s.trades() == []
    waited = time.time() - t0
    done.set()
    th.join()
    assert waited < 1.0


def test_reading_skips_the_writer_lock_and_falls_back_without_wal(tmp_path):
    s = Store(tmp_path / "live.db")
    done, th = _held(s.lock)
    t0 = time.time()
    with reading(s) as db:
        n = db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    waited = time.time() - t0
    done.set()
    th.join()
    assert n == 0 and waited < 1.0
    s.rdb = None                                                 # no WAL: the shared connection under the lock
    with reading(s) as db:
        assert db is s.db and s.lock.locked()
    fake = types.SimpleNamespace(lock=threading.Lock(), db=s.db)
    with reading(fake) as db:
        assert db is s.db and fake.lock.locked()


def test_scorecard_and_focus_reads_do_not_hold_the_writer_lock(tmp_path):
    from live import focuslab, record
    s = Store(tmp_path / "live.db")
    tr = record.Tracker(s, "upstox")
    idx = {r[1] for r in s.db.execute("PRAGMA index_list(outcomes)").fetchall()}
    assert "outcomes_src_ts" in idx
    for i in range(1, 6):
        a = _alert(i)
        a["id"] = s.add_alert(a)
        tr.add(a, {}, types.SimpleNamespace(get=lambda k: None), a["ts"], {})
    with s.lock:
        s.db.execute("UPDATE outcomes SET result='hit', o60=25, o15=5, r15=0.1")
        s.db.commit()
    expect = record.scorecard(s, "upstox")
    done, th = _held(s.lock)                                     # a long write in progress
    t0 = time.time()
    sc = record.scorecard(s, "upstox")
    rows = focuslab._alerts_of_day(s, "upstox", "2026-11-23")
    waited = time.time() - t0
    done.set()
    th.join()
    assert waited < 1.0 and sc == expect and sc[("orb_break", 0)]["n"] == 5 and len(rows) == 5
    q = "EXPLAIN QUERY PLAN SELECT kind FROM outcomes WHERE 1=1 AND source=? ORDER BY ts DESC LIMIT ?"
    plan = " ".join(str(r[3]) for r in s.db.execute(q, ("upstox", 10)).fetchall())
    assert "outcomes_src_ts" in plan and "TEMP B-TREE" not in plan.upper()


# ---------------------------------------------------------------- paper rows, asked for again and again
def _trade(src="upstox", **k):
    t = {"source": src, "day": "2026-11-23", "sym": "NIFTY", "label": "NIFTY 26000 CE 25 Nov", "entry": 100.0, "status": "open",
         "lot": 75, "lots": 1, "entry_ts": 1.0}
    t.update(k)
    return t


def test_paper_rows_are_read_again_only_when_they_change(tmp_path, monkeypatch):
    s = Store(tmp_path / "live.db")
    a = s.add_trade(_trade())
    s.add_trade(_trade("upstox#trail"))
    reads = []
    real = s._read
    monkeypatch.setattr(s, "_read", lambda q, args=(), one=False: reads.append(q) or real(q, args, one))
    for _ in range(5):                                            # the paper-exit watcher, every 0.25 s
        o = s.trades(status="open", source="upstox")
        assert [t["id"] for t in o] == [a]
        o[0]["unreal"] = 999                                      # callers change their copy, never the kept rows
    assert len(reads) == 1 and "unreal" not in s.trades(status="open", source="upstox")[0]
    s.update_trade(a, last=105.0)                                 # a write: read again at once
    assert s.trades(status="open", source="upstox")[0]["last"] == 105.0 and len(reads) == 2
    s.update_trade(a, status="closed", exit=110.0, net=700.0)
    assert s.trades(status="open", source="upstox") == [] and s.trades(source="upstox")[0]["net"] == 700.0
    with s.lock:                                                  # a direct write that says so
        s.db.execute("DELETE FROM paper WHERE source='upstox#trail'")
        s.db.commit()
    s.paper_changed()
    assert s.trades(source="upstox#trail") == [] and s.trades(status="open", source="upstox") == []
    with s.lock:                                                  # and one that doesn't: 2 s at most
        s.db.execute("INSERT INTO paper(source, day, sym, status) VALUES ('upstox', '2026-11-23', 'X', 'open')")
        s.db.commit()
    assert len(s.trades(status="open", source="upstox")) == 0
    t_ = time.monotonic() + 2.5
    monkeypatch.setattr(time, "monotonic", lambda: t_)
    assert len(s.trades(status="open", source="upstox")) == 1


def test_replay_clear_marks_paper_rows_changed(tmp_path):
    from live import replay as RP
    s = Store(tmp_path / "live.db")
    s.add_trade(_trade("replay"))
    assert len(s.trades(source="replay")) == 1
    RP.prepare_store(s, "2026-11-13", tmp_path)
    assert s.trades(source="replay") == []


# ---------------------------------------------------------------- the Alerts page
def test_alerts_page_reads_the_day_once_per_new_alert(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from live import focuslab
    from webapp import live_api
    s = Store(tmp_path / "live.db")
    for i in range(1, 4):
        s.add_alert(_alert(i))
    calls = {"alerts": 0, "picks": 0}
    real_alerts = s.alerts
    monkeypatch.setattr(s, "alerts", lambda *a, **k: calls.__setitem__("alerts", calls["alerts"] + 1) or real_alerts(*a, **k))
    monkeypatch.setattr(focuslab, "load", lambda: {"main": "v1", "versions": {"v1": {"label": "V1"}}})
    monkeypatch.setattr(focuslab, "picks", lambda *a, **k: calls.__setitem__("picks", calls["picks"] + 1) or {1})
    e = types.SimpleNamespace(store=s, source="upstox", cfg=lambda: {"live": {"source": "upstox"}},
                              clock=types.SimpleNamespace(now=lambda: dt.datetime(2026, 11, 23, 12, 0)))
    monkeypatch.setattr(live_api, "engine", lambda: e)
    live_api._ALERTS_DAY.clear()
    live_api._SHADOW_IDS.clear()
    app = FastAPI()
    app.include_router(live_api.router)
    cl = TestClient(app)
    r1 = cl.get("/api/live/alerts?day=2026-11-23&view=act").json()
    r2 = cl.get("/api/live/alerts?day=2026-11-23&view=shadow").json()
    assert r1["total"] == r2["total"] == 3 and r1["counts"] == r2["counts"] and r1["counts"]["shadow"] == 1
    assert calls == {"alerts": 1, "picks": 1}                    # the second call: nothing read or worked out again
    s.add_alert(_alert(4))                                        # a new alert: read again at once
    r3 = cl.get("/api/live/alerts?day=2026-11-23&view=act").json()
    assert r3["total"] == 4 and calls == {"alerts": 2, "picks": 2}


# ---------------------------------------------------------------- the recording
def test_gap_fill_reads_the_recording_in_slices_with_the_same_rows(tmp_path):
    import sqlite3
    from live import gapfill
    p = tmp_path / "rec.sqlite"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE bars (sym TEXT, t REAL, o REAL, h REAL, l REAL, c REAL, v REAL)")
    c.execute("CREATE TABLE minutes (ts INTEGER, key TEXT, ltp REAL, vol REAL, oi REAL, iv REAL)")
    t0 = 1795377900
    c.executemany("INSERT INTO bars VALUES (?,?,?,?,?,?,?)", [(s, t0 + 60 * i, 1, 2, 0.5, 1.5, i) for i in range(375)
                                                              for s in ("NIFTY", "TCS")])
    c.executemany("INSERT INTO minutes VALUES (?,?,?,?,?,?)", [(t0 + 60 * i, k, 10 + i, 1, 2, 3) for i in range(375)
                                                               for k in ("A", "B")])
    c.commit()
    c.close()
    b = gapfill.recording_bars(p, t0, t0 + 375 * 60)
    h = gapfill.recording_hist(p, t0, t0 + 375 * 60)
    assert {k: len(v) for k, v in b.items()} == {"NIFTY": 375, "TCS": 375} and [x[0] for x in b["TCS"]] == sorted(x[0] for x in b["TCS"])
    assert {k: len(v) for k, v in h.items()} == {"A": 375, "B": 375} and h["A"][-1] == (t0 + 374 * 60, 384, 1, 2, 3)
    parts = list(gapfill._slices(t0, t0 + 375 * 60, gapfill.SLICE_S))
    assert parts[0][0] == t0 and parts[-1][1] == t0 + 375 * 60 and all(a2 == b1 for (_, b1), (a2, _) in zip(parts, parts[1:]))


def test_candle_save_tries_again_when_the_recording_is_busy(monkeypatch):
    from live.engine import LiveEngine
    logs, tries = [], []

    def save():
        tries.append(1)
        if len(tries) == 1:
            raise Exception("database is locked")
    e = types.SimpleNamespace(_save_bars=save, log=logs.append, status={},
                              stop_evt=types.SimpleNamespace(wait=lambda s: False))
    LiveEngine._save_bars_bg(e)
    e._bars_th.join(5)
    assert len(tries) == 2 and not logs and "bars_save_ms" in e.status
