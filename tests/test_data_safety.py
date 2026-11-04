"""2.42 audit: a day's recording is never replaced by a later, smaller save (the Mac lost 3 and 4 Nov when the app
restarted after the close); prices outside the session window are not recorded as the new day (a scanner started at
night); the daily history is reloaded when it misses the last trading day (the 5 Nov copy, built at 00:46, had 3 Nov
as its previous day); and the newer readings (candle forecast, cascade, indication, data guard) are recorded with
every signal."""
import datetime as dt
import threading
import types

import pandas as pd

from live import record as R

DAY = "2026-11-04"
W0, W1 = R.session_window(DAY)


def _state(keys, ltp=100.0):
    insts = {k: types.SimpleNamespace(key=k, ltp=ltp, vol=1, oi=1, iv=1, bid=1, ask=1, tbq=1, tsq=1, cvd=1) for k in keys}
    return types.SimpleNamespace(lock=threading.Lock(), insts=insts)


def test_a_restart_after_the_close_never_replaces_the_day(tmp_path):
    r = R.Recorder(tmp_path, "upstox", DAY)
    st = _state(["a", "b", "c"])
    for m in range(30):                                       # the session: 30 minutes from 09:15
        r.snapshot(int(W0) + 900 + 60 * m, st, {"NIFTY": {"spot": 1}}, lambda x: x)
    r.chain(int(W0) + 1000, {"sym": "NIFTY", "expiry": "2026-11-11", "spot": 1.0,
                             "rows": [{"strike": 1.0, "CE": {"key": "c1", "ltp": 2.0, "oi": 5}}]})
    r.bars({"NIFTY": [[W0 + 900 + 60 * m, 1, 2, 0.5, 1.5, 0] for m in range(30)]})
    r.finalize(keep_days=100000, log=lambda *a: None)
    f = tmp_path / "recordings" / f"upstox_{DAY}_minutes.parquet"
    assert len(pd.read_parquet(f)) == 90
    r2 = R.Recorder(tmp_path, "upstox", DAY)                  # restarted at 16:24: one snapshot after the close
    r2.snapshot(int(W1) + 3000, _state(["a", "b", "c", "d"]), {"NIFTY": {"spot": 1}}, lambda x: x)
    r2.snapshot(int(W0) + 900, _state(["a"], ltp=101.0), {}, lambda x: x)    # a minute already saved: replaced, not doubled
    r2.bars({"NIFTY": [[W0 + 900, 1, 2, 0.5, 1.6, 0]]})
    r2.finalize(keep_days=100000, log=lambda *a: None)
    mins = pd.read_parquet(f)
    assert len(mins) == 90 and mins["ts"].max() <= W1         # the whole session kept, the after-close minute dropped
    assert float(mins[(mins.ts == int(W0) + 900) & (mins.key == "a")]["ltp"].iloc[0]) == 101.0
    assert len(pd.read_parquet(tmp_path / "recordings" / f"upstox_{DAY}_chains.parquet")) == 1
    assert len(pd.read_parquet(tmp_path / "recordings" / f"upstox_{DAY}_bars.parquet")) == 30


def test_night_prices_are_not_the_new_day(tmp_path):
    r = R.Recorder(tmp_path, "upstox", "2026-11-05")
    w0, _ = R.session_window("2026-11-05")
    night = int(w0) - 8 * 3600                                # 01:00 IST
    r.snapshot(night, _state(["a"]), {"NIFTY": {}}, lambda x: x)
    assert r.health()["minute_rows"] == 0 and r.chain(night, {"rows": [{"strike": 1, "CE": {"ltp": 1}}]}) == 0
    with r.lock:                                              # rows a 2.41 scanner already wrote at night
        r.db.execute("INSERT INTO minutes(ts, key, ltp) VALUES (?, 'a', 1)", (night,))
        r.db.execute("INSERT INTO minutes(ts, key, ltp) VALUES (?, 'a', 2)", (int(w0) + 1200,))
        r.db.commit()
    r.news([{"title": "Overnight headline", "ts": night}], night)          # news keeps its own time
    r.finalize(keep_days=100000, log=lambda *a: None)
    mins = pd.read_parquet(tmp_path / "recordings" / "upstox_2026-11-05_minutes.parquet")
    assert list(mins["ts"]) == [int(w0) + 1200]
    assert len(pd.read_parquet(tmp_path / "recordings" / "upstox_2026-11-05_news.parquet")) == 1


def test_simulator_days_are_replaced_and_an_unreadable_file_is_kept(tmp_path):
    s = R.Recorder(tmp_path, "sim", DAY)
    s.snapshot(1000, _state(["a"]), {}, lambda x: x)
    s.finalize(keep_days=100000, log=lambda *a: None)
    s2 = R.Recorder(tmp_path, "sim", DAY)
    s2.snapshot(2000, _state(["a"]), {}, lambda x: x)
    s2.finalize(keep_days=100000, log=lambda *a: None)
    assert list(pd.read_parquet(tmp_path / "recordings" / f"sim_{DAY}_minutes.parquet")["ts"]) == [2000]
    bad = tmp_path / "recordings" / f"upstox_{DAY}_minutes.parquet"
    bad.write_bytes(b"not parquet")
    r = R.Recorder(tmp_path, "upstox", DAY)
    r.snapshot(int(W0) + 600, _state(["a"]), {}, lambda x: x)
    logs = []
    r.finalize(keep_days=100000, log=logs.append)
    assert len(pd.read_parquet(bad)) == 1 and list((tmp_path / "recordings").glob("*.unreadable.parquet"))
    assert any("could not be read" in x for x in logs)


def test_daily_history_must_end_on_the_last_trading_day():
    from live.engine import LiveEngine
    fresh = LiveEngine._daily_fresh
    d = lambda p: {"NIFTY": {"prev": {"date": p}}}
    assert not fresh(d("2026-11-03"), dt.date(2026, 11, 5))   # the Mac's 5 Nov copy
    assert fresh(d("2026-11-04"), dt.date(2026, 11, 5))
    assert fresh(d("2026-10-30"), dt.date(2026, 11, 2))       # Monday: Friday is the last trading day
    assert not fresh(d("2026-10-28"), dt.date(2026, 10, 30))  # the first live day had the same problem
    assert not fresh({}, dt.date(2026, 11, 5))


def test_newer_readings_are_recorded_with_every_signal():
    from live.engine import LiveEngine
    now = dt.datetime(2026, 11, 5, 10, 2, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))
    start5 = now.replace(minute=0).timestamp()
    cast = types.SimpleNamespace(cur={("NIFTY", "5m"): {"start": start5, "stages": {1: {"dir": "down", "strength": 70.0, "pakka": False},
                                                                                     2: {"dir": "down", "strength": 91.0, "pakka": True}}},
                                      ("SBIN", "5m"): {"start": start5 - 900, "stages": {1: {"dir": "up", "strength": 99, "pakka": True}}}})
    nc = types.SimpleNamespace(_cast=cast, cur={("NIFTY", "15m"): {"start": now.replace(minute=0).timestamp(), "pred": {"p_green": 75}}})
    e = types.SimpleNamespace(nextc=nc, indication=types.SimpleNamespace(latest={"day": "2026-11-05", "word": "Gap down"}),
                              dguard=types.SimpleNamespace(symbol_problem=lambda s: "stale" if s == "SBIN" else None),
                              IND_WORDS=LiveEngine.IND_WORDS)
    f = LiveEngine.forecast_facts(e, "SBIN", "up", now)
    assert f["mfc5_al"] == -0.91 and f["mfc5_pakka"] == 1 and f["mnc15_al"] == 0.5 and f["ind_al"] == -1.0 and f["dq_bad"] == 1
    assert "fc5_al" not in f                                  # SBIN's forecast is for an earlier candle: not used
    g = LiveEngine.forecast_facts(e, "NIFTY", "down", now)
    assert g["fc5_al"] == 0.91 and "mfc5_al" not in g and g["ind_al"] == 1.0 and g["dq_bad"] == 0


def test_daily_backup_copies_checks_and_keeps_a_week(tmp_path, monkeypatch):
    import sqlite3
    from live import backup as BK
    monkeypatch.setattr(BK, "cfg", lambda: {**BK.DEFAULTS, "keep": 2, "min_free_gb": 0, "files": ["live.db", "bench.db", "upstox_token.json"]})
    for n in ("live.db", "bench.db"):
        db = sqlite3.connect(tmp_path / n)
        db.execute("CREATE TABLE t (x)")
        db.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(500)])
        db.commit()
        db.close()
    (tmp_path / "upstox_token.json").write_text('{"access_token": "x"}')
    for d in ("2026-11-02", "2026-11-03", "2026-11-04"):
        r = BK.run(tmp_path, d, log=lambda *a: None)
    assert set(r) == {"live.db", "bench.db"}                               # a token file is never copied
    days = sorted(p.name for p in (tmp_path / "backups").iterdir())
    assert days == ["2026-11-03", "2026-11-04"]                            # older copies dropped
    c = sqlite3.connect(tmp_path / "backups" / "2026-11-04" / "bench.db")
    assert c.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 500
    e = types.SimpleNamespace(data_dir=tmp_path, running=True, log=lambda *a: None)
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    assert not BK.tick(e, dt.datetime(2026, 11, 5, 17, 0, tzinfo=ist))     # not while the scanner runs
    e.running = False
    assert not BK.tick(e, dt.datetime(2026, 11, 5, 12, 0, tzinfo=ist))     # not before 16:30
    assert BK.tick(e, dt.datetime(2026, 11, 5, 17, 0, tzinfo=ist))
    for _ in range(100):
        if not BK.LAST["running"]:
            break
        threading.Event().wait(0.05)
    assert (tmp_path / "backups" / "2026-11-05" / "done.json").exists() and BK.LAST["ok"]
    assert not BK.tick(e, dt.datetime(2026, 11, 5, 18, 0, tzinfo=ist))     # once a day
    st = BK.status(tmp_path)
    assert st["last"]["day"] == "2026-11-05"


def test_app_log_on_disk_with_secrets_masked(tmp_path):
    from live import applog as AL
    AL.write(tmp_path, "10:00:01  Dhan token refreshed access_token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefgh")
    AL.write(tmp_path, "10:00:02  Live scanner error: boom")
    t = AL.tail(tmp_path)
    assert len(t) == 2 and "eyJ" not in t[0] and "boom" in t[1]
