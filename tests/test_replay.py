"""2.76 Replay a past day: a recorded day played through the same engine, never later than its own clock, kept apart
from the real market (source 'replay', its own recording folder, no Telegram), and the candles it rebuilds are the
recorded ones."""
import datetime as dt
import gzip
import json
import os
import time

import numpy as np
import pandas as pd

from live import replay as RP
from live.clock import IST

DAY = dt.date(2026, 11, 13)                  # a Friday, a regular NSE session
PREV = dt.date(2026, 11, 12)
EXP = dt.date(2026, 11, 17)
OLD_EXP = dt.date(2026, 11, 13)              # expires on the day: gone from a later instrument list
O = dt.datetime.combine(DAY, dt.time(9, 15), IST).timestamp()
SYMS = {"NIFTY": ("NSE_INDEX|Nifty 50", True, 25000.0, 50.0), "AAA": ("NSE_EQ|AAA", False, 1000.0, 10.0),
        "BBB": ("NSE_EQ|BBB", False, 500.0, 5.0)}
VIX = "NSE_INDEX|India VIX"


def _ms(d):
    return int(dt.datetime.combine(d, dt.time(15, 30), IST).timestamp() * 1000)


def _opt_key(sym, k, side, e=EXP):
    return f"NSE_FO|{sym}_{e:%m%d}_{int(k)}_{side}"


def make_day(root, minutes=375, seed=7, with_chains=True):
    """A small recorded day: NIFTY + two stocks, futures, a few strikes each, VIX; the previous day's closes."""
    rng = np.random.default_rng(seed)
    rec, ins = root / "recordings", root / "instruments"
    rec.mkdir(parents=True, exist_ok=True)
    ins.mkdir(parents=True, exist_ok=True)
    rows = []
    for s, (key, idx, px, step) in SYMS.items():
        if not idx:
            rows.append({"segment": "NSE_EQ", "instrument_key": key, "short_name": s.title(), "trading_symbol": s})
        base = {"segment": "NSE_FO", "underlying_symbol": s, "underlying_key": key,
                "underlying_type": "INDEX" if idx else "EQUITY", "lot_size": 75 if idx else 500}
        rows.append({**base, "instrument_type": "FUT", "instrument_key": f"NSE_FO|{s}_FUT", "expiry": _ms(dt.date(2026, 11, 24))})
        for i in range(-3, 4):
            k = px + i * step
            for side in ("CE", "PE"):
                rows.append({**base, "instrument_type": side, "instrument_key": _opt_key(s, k, side), "expiry": _ms(EXP),
                             "strike_price": k})
    later = ins / "NSE_2026-11-14.json.gz"                    # a list from after the day (the expired contract is gone)
    with gzip.open(later, "wt") as f:
        json.dump(rows, f)
    bars, mins, chains = [], [], []
    path = {}
    for s, (key, idx, px, step) in SYMS.items():
        p, vol = px, 0.0
        for i in range(-10, minutes):                          # 10 pre-open minutes (09:05-09:14) are dropped
            t = O + 60 * i
            o = p
            r = rng.normal(0, px * 0.0008, 4)
            seq = o + np.cumsum(r)
            c = float(seq[-1])
            h, lo = max(o, c, float(seq.max())) + 0.05, min(o, c, float(seq.min())) - 0.05
            v = 0.0 if idx else float(rng.integers(100, 2000))
            bars.append((s, t, round(o, 2), round(h, 2), round(lo, 2), round(c, 2), v))
            p = c
            path[(s, i)] = c
    b = pd.DataFrame(bars, columns=["sym", "t", "o", "h", "l", "c", "v"])
    for s, (key, idx, px, step) in SYMS.items():                # minute snapshot at M = the state at M (close of M-1)
        g = b[b.sym == s].sort_values("t").reset_index(drop=True)
        cum = 0.0
        for j, r in g.iterrows():
            cum += r.v
            M = int(r.t + 60)
            mins.append((M, key, r.c, cum if not idx else None, None, None, r.c - 0.05, r.c + 0.05, 1e5, 9e4, 0.0))
            mins.append((M, f"NSE_FO|{s}_FUT", r.c * 1.002, cum * 3, 1e6 + j * 10, None, r.c * 1.002 - 0.1, r.c * 1.002 + 0.1,
                         1e5, 9e4, 0.0))
            for i in range(-3, 4):
                k = px + i * step
                for side in ("CE", "PE"):
                    intr = max(0.0, (r.c - k) if side == "CE" else (k - r.c))
                    ltp = round(intr + px * 0.004, 2)
                    mins.append((M, _opt_key(s, k, side), ltp, 1000 + j, 5e4 + j, 14.0, ltp - 0.05, ltp + 0.05, None, None, None))
        if with_chains and s == "NIFTY":
            go = g[g.t >= O].reset_index(drop=True)            # chains from the open (live fetches them from 09:15)
            for j in range(0, len(go), 3):
                r = go.iloc[j]
                T = int(r.t + 35)
                for i in range(-3, 4):
                    k = px + i * step
                    for side in ("CE", "PE"):
                        intr = max(0.0, (r.c - k) if side == "CE" else (k - r.c))
                        chains.append((T, s, EXP.isoformat(), r.c, k, side, _opt_key(s, k, side), round(intr + 100, 2),
                                       intr + 99, intr + 101, 5e4, 1e3, 1e4, 14.0, 0.5))
                chains.append((T, s, OLD_EXP.isoformat(), r.c, px, "CE", _opt_key(s, px, "CE", OLD_EXP), 5.0, 4.9, 5.1, 9e4,
                               -2e3, 2e4, 30.0, 0.5))
    for j in range(minutes + 10):
        mins.append((int(O + 60 * (j - 9)), VIX, 14.0 + 0.001 * j, None, None, None, None, None, None, None, None))
    cols = ["ts", "key", "ltp", "vol", "oi", "iv", "bid", "ask", "tbq", "tsq", "cvd"]
    pd.DataFrame(mins, columns=cols).to_parquet(rec / f"upstox_{DAY}_minutes.parquet", index=False)
    b.to_parquet(rec / f"upstox_{DAY}_bars.parquet", index=False)
    if with_chains:
        pd.DataFrame(chains, columns=["ts", "sym", "expiry", "spot", "strike", "side", "key", "ltp", "bid", "ask", "oi",
                                      "oi_chg", "vol", "iv", "delta"]).to_parquet(rec / f"upstox_{DAY}_chains.parquet", index=False)
    pd.DataFrame([(O + 600, O + 610, "Test wire", "AAA wins a big order", "https://example.com/a", "AAA", 1),
                  (O - 7200, O - 7000, "Test wire", "Before the open: BBB results", "https://example.com/b", "BBB", 0)],
                 columns=["ts", "seen", "source", "title", "link", "syms", "tone"]).to_parquet(
        rec / f"upstox_{DAY}_news.parquet", index=False)
    pd.DataFrame([(int(O + 60), "NIFTY", json.dumps({"sym": "NIFTY", "lot": 65})),
                  (int(O + 60), "AAA", json.dumps({"sym": "AAA", "lot": 500}))],
                 columns=["ts", "sym", "data"]).to_parquet(rec / f"upstox_{DAY}_metrics.parquet", index=False)
    pv = [(int(O - 86400 + 22500), k, 99.0 if "_CE" in k or "_PE" in k else 24900.0 if "Nifty" in k else 13.5, 4.0e4)
          for k in [_opt_key(s, SYMS[s][2] + i * SYMS[s][3], sd) for s in SYMS for i in range(-3, 4) for sd in ("CE", "PE")]
          + [f"NSE_FO|{s}_FUT" for s in SYMS] + [VIX, "NSE_INDEX|Nifty 50"]]
    pd.DataFrame(pv, columns=["ts", "key", "ltp", "oi"]).to_parquet(rec / f"upstox_{PREV}_minutes.parquet", index=False)
    pd.DataFrame([("NIFTY", O - 86400 + 60 * i, 24800, 24810, 24790, 24805, 0) for i in range(375)],
                 columns=["sym", "t", "o", "h", "l", "c", "v"]).to_parquet(rec / f"upstox_{PREV}_bars.parquet", index=False)
    return b


def test_days_lists_only_finished_regular_sessions(tmp_path):
    make_day(tmp_path)
    got = RP.days(tmp_path, today=dt.date(2026, 11, 15))
    assert [d["day"] for d in got] == ["2026-11-13"] and got[0]["news"]       # 12 Nov has no bars/chains parts
    (tmp_path / "recordings" / "upstox_2026-11-15_minutes.parquet").write_bytes(b"x")   # a Sunday: never offered
    assert [d["day"] for d in RP.days(tmp_path, today=dt.date(2026, 11, 16))] == ["2026-11-13"]
    assert RP.days(tmp_path, today=dt.date(2026, 11, 12)) == []                        # never a day after today
    assert RP.speed_of(13) == 10 and RP.speed_of("x") == RP.DEFAULT_SPEED and RP.speed_of(999) == max(RP.SPEEDS)


def test_universe_takes_that_days_contracts_and_lots(tmp_path):
    make_day(tmp_path)
    rec = RP.Recording(tmp_path, "2026-11-13")
    uni = RP.universe_for(tmp_path, rec, log=lambda m: None)
    n = uni.underlyings["NIFTY"]
    assert n.lot == 65 and all(o.lot == 65 for st in n.chains.values() for p in st.values() for o in p.values())
    assert OLD_EXP in n.expiries and uni.by_key[_opt_key("NIFTY", 25000, "CE", OLD_EXP)][0] == "OPT"
    assert n.near_expiry(DAY) == OLD_EXP                     # the day's own weekly expiry, as live had it
    assert uni.underlyings["AAA"].lot == 500 and rec.prev_day == PREV


def test_prices_follow_the_candle_and_never_run_ahead_of_the_clock(tmp_path):
    bars = make_day(tmp_path, minutes=30)
    rec = RP.Recording(tmp_path, "2026-11-13")
    uni = RP.universe_for(tmp_path, rec, log=lambda m: None)
    clk = RP.ReplayClock(DAY, 10)
    feed = RP.RecordingFeed(rec, clk, uni, lambda t: None)
    feed.subscribe([u.key for u in uni.underlyings.values()] + [u.fut_key for u in uni.underlyings.values()] + [VIX], "full")
    seen = {}
    for _ in range(int(20 * 60 / RP.STEP_S)):
        t0 = clk.ts()
        clk.set(t0 + RP.STEP_S)
        for t in feed.ticks(t0, clk.ts()):
            seen.setdefault(t["key"], []).append((clk.ts(), t))
    g = bars[bars.sym == "AAA"].set_index("t")
    for T, t in seen["NSE_EQ|AAA"]:
        if "ltp" not in t:
            continue
        mnt = int(T // 60) * 60
        r = g.loc[mnt]                                          # the price is inside the candle running now
        assert r.l - 1e-9 <= t["ltp"] <= r.h + 1e-9
    for T, t in seen["NSE_FO|AAA_FUT"]:                         # snapshots: never one stamped after the clock
        assert t["ltt"] / 1000 <= T
    by_min = {}
    for T, t in seen["NSE_EQ|AAA"]:
        if "ltp" in t:
            by_min.setdefault(int(T // 60) * 60, []).append(t["ltp"])
    for mnt, pxs in by_min.items():                              # open, both extremes and close, in a possible order
        if mnt + 60 > clk.ts():
            continue                                             # the minute still running
        r = g.loc[mnt]
        assert pxs[0] == r.o and pxs[-1] == r.c and max(pxs) == r.h and min(pxs) == r.l
    vols = [t["vol"] for _, t in seen["NSE_EQ|AAA"] if "vol" in t]
    assert vols == sorted(vols)                                  # day volume never goes back
    rest = RP.RecordingREST(rec, RP.ReplayClock(DAY, 10), uni)
    assert rest.option_chain(uni.underlyings["NIFTY"].key, EXP) == []      # 09:15:00: the first chain is 09:15:35
    rest.clock.set(O + 120)
    ch = rest.option_chain(uni.underlyings["NIFTY"].key, EXP)
    assert len(ch) == 7 and ch[3]["call_options"]["market_data"]["prev_oi"] == 5e4 - 1e3
    assert ch[3]["call_options"]["market_data"]["close_price"] == 99.0     # the previous day's close
    q = rest.quotes([uni.underlyings["NIFTY"].fut_key, VIX])
    assert q[VIX]["prev_close_price"] == 13.5 and q[uni.underlyings["NIFTY"].fut_key]["previous_oi"] == 4.0e4
    h = rest.get("/v3/historical-candle/NSE_INDEX%7CNifty%2050/minutes/1/2026-11-12/2026-10-28")["candles"]
    assert len(h) == 375 and all(c[0].startswith("2026-11-12") for c in h)  # earlier recorded days only
    intra = rest.get("/v3/historical-candle/intraday/NSE_INDEX%7CNifty%2050/minutes/1")["candles"]
    assert len(intra) == 2 and all(dt.datetime.fromisoformat(c[0]).timestamp() + 60 <= O + 120 for c in intra)


def test_prepare_store_clears_only_the_last_replay(tmp_path):
    from live.store import Store
    st = Store(tmp_path / "live.db")
    with st.lock:
        for src in ("upstox", "sim", "replay", "replay#best"):
            st.db.execute("INSERT INTO paper (source, day, sym) VALUES (?, ?, ?)", (src, "2026-11-13", "X"))
        st.db.commit()
    st.put("daily:upstox:2026-11-13", {"NIFTY": {"prev": {"c": 1}}})
    st.put("ivhist:upstox", {"NIFTY": [["2026-11-12", 12.0], ["2026-11-13", 13.0], ["2026-11-14", 14.0]]})
    out = RP.prepare_store(st, "2026-11-13", tmp_path)
    with st.lock:
        left = sorted(r[0] for r in st.db.execute("SELECT source FROM paper"))
    assert left == ["sim", "upstox"] and out["cleared"]["paper"] == 2 and out["daily"]
    assert st.get("daily:replay:2026-11-13") == {"NIFTY": {"prev": {"c": 1}}}
    assert st.get("ivhist:replay") == {"NIFTY": [["2026-11-12", 12.0]]}     # never a later day's IV
    assert st.get("ivhist:upstox")["NIFTY"][-1][0] == "2026-11-14"          # the real one untouched


def test_whole_engine_replays_the_day_apart_from_the_real_market(tmp_path, monkeypatch):
    from radar.config import load_config
    from live.engine import LiveEngine
    bars = make_day(tmp_path)
    real = {f.name: f.stat().st_mtime for f in (tmp_path / "recordings").iterdir()}
    monkeypatch.setattr(RP, "SPEEDS", RP.SPEEDS + (900,))
    monkeypatch.setattr(RP, "MAX_WAIT_S", 0.0)                   # no wait for analysis passes: a 25-second day

    def cfg():
        c = load_config()
        c["live"] = {**(c.get("live") or {}), "auto_start": False, "news": False, "filings": False, "briefs": False}
        c["ai"] = {**(c.get("ai") or {}), "provider": "off"}
        return c
    e = LiveEngine(cfg, tmp_path)
    sent = []
    e.telegram.send = lambda *a, **k: sent.append(a)
    e.replay_req = {"day": "2026-11-13", "speed": 900}
    e.start("replay")
    try:
        t0 = time.time()
        from fastapi.encoders import jsonable_encoder
        from webapp.live_api import clean
        while time.time() - t0 < 200 and e.status.get("state") != "closed":
            time.sleep(1)
            assert e.status.get("state") != "error", e.status
            if e.metrics:                                        # the pages can read it (plain numbers, no numpy types)
                jsonable_encoder(clean(e.snapshot()))
        assert e.status["state"] == "closed" and "Replay of 2026-11-13 finished" in e.status["message"], e.status
        snap = e.snapshot()
        assert snap["status"]["replay"]["day"] == "2026-11-13" and snap["status"]["replay"]["progress"] == 100.0
        got = {int(r[0]): r for r in e.state.bars["AAA"].bars}
        g = bars[(bars.sym == "AAA") & (bars.t >= O)].set_index("t")
        same = sum(1 for t, r in g.iterrows() if t in got and tuple(got[t][1:5]) == (r.o, r.h, r.l, r.c))
        assert same >= len(g) - 1, (same, len(g))               # the candles the replay builds are the recorded ones
    finally:
        e.stop()
    with e.store.lock:
        news = [r[0] for r in e.store.db.execute("SELECT title FROM alerts WHERE kind='news' AND source='replay'")]
    assert any("AAA wins a big order" in t for t in news) and not any("BBB" in t for t in news)   # fresh ones only, as live
    assert not any("AAA wins" in n["title"] for n in e.news.items)    # today's headlines are back after the replay
    with e.store.lock:
        srcs = {r[0] for t in ("alerts", "paper", "outcomes") for r in e.store.db.execute(f"SELECT DISTINCT source FROM {t}")}
    assert srcs <= {"replay"} or all(s.startswith("replay") for s in srcs)
    assert not sent                                              # no Telegram from a replay
    assert {f.name: f.stat().st_mtime for f in (tmp_path / "recordings").iterdir()} == real   # real recordings untouched
    assert any(f.name.startswith("replay_2026-11-13") for f in (tmp_path / "replay" / "recordings").iterdir())
    assert e.manual_stop_day is None                             # stopping a replay never pauses the live scanner


def test_selfcheck_knows_the_replay():
    from radar.config import load_config
    from live import selfcheck
    chk = {c["id"]: c for c in selfcheck.static_checks(load_config())}
    assert chk["replay"]["ok"] is True
