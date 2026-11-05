"""Trade journeys (2.45): every test-bench signal followed to 15:15 with no exit, and what happened on the way.

Mandeep (6 Nov): "do not sell the trade whole day, take trade on all alerts and record ... when a trade touches the
profit, how much high profit gain was there, when profit started dropping and what was the situation, all data factors
candle for drop; and the same with loss: when it started, why, how much max loss, what made it reverse."

After the close (and for any earlier day not done yet) the app replays the day from its own recording
(data/recordings/<source>_<day>_minutes.parquet: every streamed instrument each minute; _metrics: every symbol's facts
each minute). For each bench signal it takes the at-the-money option the bench bought (entry = the ask paid) and
follows it minute by minute to 15:15, valued at the bid (what selling would have got), with no stop or target:

  max profit (MFE) and when, max loss (MAE) and when, the drawdown before the peak, the giveback after it, the first
  minute it touched +10 / +20 / +30 / +50 % and -10 / -20 / -30 %, its value at 15 / 30 / 60 / 120 minutes and 15:15,
  whether +20% or -20% came first, and whether a trade that went to -20% came back to 0.

At five moments (the peak, the start of the giveback, the first -10%, the bottom, the recovery) it saves what the
market looked like: the underlying's move since entry and over the last 5 minutes (with / against the trade), price
vs VWAP, the option's IV and OI change since entry, Nifty's move since entry. That is the "why" next to the "when".
Correlation, not proof: the page counts how often each thing was there.

Its own file, data/paths.db (the bench and the live engine never wait for it); runs in a low-priority child process
(lab/proc.py job "paths"). Paper only: nothing is traded; the bench's own trades and exits are unchanged.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
import threading
import time
from pathlib import Path

import numpy as np

SQL = """
CREATE TABLE IF NOT EXISTS trade_paths (
  sig TEXT PRIMARY KEY, source TEXT, day TEXT, ts REAL, sym TEXT, kind TEXT, variant TEXT, name TEXT, direction TEXT,
  opt_key TEXT, entry REAL, und0 REAL, end_pct REAL, mfe_pct REAL, mfe_min REAL, mae_pct REAL, mae_min REAL,
  dd_before_peak REAL, giveback REAL, t_p10 REAL, t_p20 REAL, t_p30 REAL, t_p50 REAL, t_m10 REAL, t_m20 REAL,
  t_m30 REAL, pct15 REAL, pct30 REAL, pct60 REAL, pct120 REAL, first TEXT, recovered INTEGER, events TEXT, path TEXT);
CREATE INDEX IF NOT EXISTS paths_day ON trade_paths(source, day);
CREATE INDEX IF NOT EXISTS paths_kind ON trade_paths(source, kind, variant);
CREATE TABLE IF NOT EXISTS path_days (source TEXT, day TEXT, n INTEGER, at REAL, note TEXT, PRIMARY KEY (source, day));
"""
UPS, DOWNS = (10, 20, 30, 50), (10, 20, 30)
AT_MIN = (15, 30, 60, 120)
END_HM = (15, 15)
_LOCK = threading.Lock()
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def db_path(data_dir) -> Path:
    return Path(data_dir) / "paths.db"


def connect(data_dir, ro: bool = False) -> sqlite3.Connection:
    p = db_path(data_dir)
    if ro:
        c = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=30)
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(p), timeout=30)
        try:
            c.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        c.executescript(SQL)
    c.row_factory = sqlite3.Row
    return c


def _end_ts(day: str) -> float:
    d = dt.date.fromisoformat(day)
    return dt.datetime.combine(d, dt.time(*END_HM), IST).timestamp()


def journey(ts: np.ndarray, val: np.ndarray, entry: float, t0: float, ctx_at=None) -> dict | None:
    """One trade's journey from minute values of the option (val at times ts, after entry t0) and the entry price.
    ctx_at(t) -> dict: the market at minute t (for the events). Pure function: the tests call it directly."""
    ok = np.isfinite(val) & (val > 0)
    ts, val = np.asarray(ts, float)[ok], np.asarray(val, float)[ok]
    if len(ts) < 3 or not entry or entry <= 0:
        return None
    pct = (val / entry - 1) * 100
    mins = (ts - t0) / 60.0
    i_pk, i_tr = int(np.argmax(pct)), int(np.argmin(pct))
    out = {"end_pct": round(float(pct[-1]), 2), "mfe_pct": round(float(pct[i_pk]), 2), "mfe_min": round(float(mins[i_pk]), 1),
           "mae_pct": round(float(pct[i_tr]), 2), "mae_min": round(float(mins[i_tr]), 1),
           "dd_before_peak": round(float(np.min(pct[:i_pk + 1])), 2),
           "giveback": round(float(pct[i_pk] - np.min(pct[i_pk:])), 2)}

    def first(mask):
        j = np.nonzero(mask)[0]
        return (int(j[0]), round(float(mins[j[0]]), 1)) if len(j) else (None, None)
    for u in UPS:
        out[f"t_p{u}"] = first(pct >= u)[1]
    for d_ in DOWNS:
        out[f"t_m{d_}"] = first(pct <= -d_)[1]
    for m in AT_MIN:
        j = np.nonzero(mins <= m)[0]
        out[f"pct{m}"] = round(float(pct[j[-1]]), 2) if len(j) and mins[-1] >= m * 0.8 else None
    p20, m20 = out["t_p20"], out["t_m20"]
    out["first"] = "+20" if p20 is not None and (m20 is None or p20 < m20) else "-20" if m20 is not None else "neither"
    # the five moments, with the market around each
    ev = []

    def add(what, j):
        if j is None:
            return
        e = {"what": what, "min": round(float(mins[j]), 1), "pct": round(float(pct[j]), 2)}
        if ctx_at is not None:
            try:
                e["ctx"] = ctx_at(float(ts[j]))
            except Exception:
                pass
        ev.append(e)
    if pct[i_pk] >= 5:
        add("peak", i_pk)
        drop = max(5.0, pct[i_pk] / 3)
        j = np.nonzero(pct[i_pk:] <= pct[i_pk] - drop)[0]
        add("giveback", i_pk + int(j[0]) if len(j) else None)
    add("loss_start", first(pct <= -10)[0])
    recovered = 0
    if pct[i_tr] <= -10:
        add("bottom", i_tr)
        j = np.nonzero(pct[i_tr:] >= pct[i_tr] / 2)[0]
        add("recover", i_tr + int(j[0]) if len(j) else None)
        if pct[i_tr] <= -20:
            recovered = int(bool(np.any(pct[i_tr:] >= 0)))
    out["recovered"] = recovered
    out["events"] = ev
    grid = np.arange(0, max(5, int(mins[-1]) + 5), 5)
    out["path"] = [round(float(pct[np.nonzero(mins <= g)[0][-1]]), 1) if np.any(mins <= g) else None for g in grid]
    return out


def build_day(data_dir, day: str, source: str = "upstox", log=print, should_stop=lambda: False) -> dict:
    """Journeys of one day's bench signals. Needs that day's recording and bench.db."""
    import pandas as pd
    data_dir = Path(data_dir)
    rec = data_dir / "recordings"
    fm, fx = rec / f"{source}_{day}_minutes.parquet", rec / f"{source}_{day}_metrics.parquet"
    bdb = data_dir / "bench.db"
    note = None
    if not bdb.exists():
        note = "no bench.db"
    elif not fm.exists():
        note = "no recording for the day"
    rows = []
    if note is None:
        b = sqlite3.connect(f"file:{bdb}?mode=ro", uri=True, timeout=30)
        b.row_factory = sqlite3.Row
        try:
            sigs = {r["sig"]: dict(r) for r in b.execute(
                "SELECT sig, ts, sym, kind, variant, direction FROM bench_signals WHERE source=? AND day=?", (source, day))}
            buys = {}
            for r in b.execute("SELECT sig, key1, entry, ts, name FROM bench_trades WHERE source=? AND day=? AND style='buy' "
                               "AND key1 IS NOT NULL AND entry > 0", (source, day)):
                buys.setdefault(r["sig"], dict(r))
        finally:
            b.close()
        todo = {s: (sigs[s], buys[s]) for s in sigs if s in buys}
        if not todo:
            note = "no bench signals with a bought option"
        else:
            keys = sorted({t["key1"] for _s, t in todo.values()})
            mins = pd.read_parquet(fm, columns=["ts", "key", "ltp", "bid", "iv", "oi"], filters=[("key", "in", keys)])
            if len(mins) < 50:
                note = "recording too short"
            else:
                syms = sorted({s["sym"] for s, _t in todo.values()} | {"NIFTY"})
                met = pd.read_parquet(fx, columns=["ts", "sym", "data"], filters=[("sym", "in", syms)]) if fx.exists() else None
                facts = {}
                if met is not None and len(met):
                    for sym, g in met.groupby("sym"):
                        recs = []
                        for t, d in zip(g["ts"].to_numpy(), g["data"].to_numpy()):
                            try:
                                x = json.loads(d)
                            except Exception:
                                continue
                            recs.append((float(t), x.get("spot"), x.get("vs_vwap_pct"), x.get("ret_5m")))
                        recs.sort()
                        facts[sym] = np.array([[a, *(np.nan if v is None else float(v) for v in r)] for a, *r in recs], float) \
                            if recs else None
                end = _end_ts(day)
                by_key = {k: g.sort_values("ts") for k, g in mins.groupby("key")}
                for sg, (s, t) in todo.items():
                    if should_stop():
                        break
                    ser = by_key.get(t["key1"])
                    if ser is None:
                        continue
                    ser = ser[(ser["ts"] >= float(t["ts"]) - 30) & (ser["ts"] <= end)]
                    if len(ser) < 3:
                        continue
                    tsa = ser["ts"].to_numpy(float)
                    bid, ltp = ser["bid"].to_numpy(float), ser["ltp"].to_numpy(float)
                    val = np.where(np.nan_to_num(bid) > 0, bid, ltp)
                    iv, oi = ser["iv"].to_numpy(float), ser["oi"].to_numpy(float)
                    d = 1 if s["direction"] == "up" else -1
                    F, N = facts.get(s["sym"]), facts.get("NIFTY")
                    t0 = float(t["ts"])

                    def at(arr, tt):
                        if arr is None or not len(arr):
                            return None
                        j = np.searchsorted(arr[:, 0], tt, side="right") - 1
                        return arr[max(0, j)]
                    u0, n0 = at(F, t0), at(N, t0)
                    iv0 = iv[0] if np.isfinite(iv[0]) and iv[0] > 0 else np.nan
                    oi0 = oi[0] if np.isfinite(oi[0]) and oi[0] > 0 else np.nan

                    def ctx_at(tt, F=F, N=N, u0=u0, n0=n0, d=d, iv0=iv0, oi0=oi0, tsa=tsa, iv=iv, oi=oi):
                        out = {}
                        u, nn = at(F, tt), at(N, tt)
                        if u is not None and u0 is not None and u0[1] and u[1]:
                            out["und"] = round(float((u[1] / u0[1] - 1) * 100 * d), 3)
                            if np.isfinite(u[3]):
                                out["ret5"] = round(float(u[3] * d), 3)           # last 5 minutes, + = with the trade
                            if np.isfinite(u[2]):
                                out["vwap"] = round(float(u[2] * d), 3)           # + = on the trade's side of VWAP
                        if nn is not None and n0 is not None and n0[1] and nn[1]:
                            out["nifty"] = round(float((nn[1] / n0[1] - 1) * 100 * d), 3)
                        j = int(np.searchsorted(tsa, tt, side="right") - 1)
                        if 0 <= j < len(tsa):
                            if np.isfinite(iv0) and np.isfinite(iv[j]) and iv[j] > 0:
                                dv = float(iv[j] - iv0)
                                out["iv"] = round(dv * 100 if max(iv0, iv[j]) < 5 else dv, 2)   # IV points (the feed gives 0.26 = 26%)
                            if np.isfinite(oi0) and np.isfinite(oi[j]):
                                out["oi"] = round(float((oi[j] / oi0 - 1) * 100), 1)
                        return out
                    j = journey(tsa, val, float(t["entry"]), t0, ctx_at)
                    if j is None:
                        continue
                    rows.append({"sig": sg, "source": source, "day": day, "ts": t0, "sym": s["sym"], "kind": s["kind"],
                                 "variant": s["variant"], "name": t.get("name"), "direction": s["direction"],
                                 "opt_key": t["key1"], "entry": float(t["entry"]),
                                 "und0": float(u0[1]) if u0 is not None and np.isfinite(u0[1]) else None, **j})
    with _LOCK:
        c = connect(data_dir)
        try:
            cols = ["sig", "source", "day", "ts", "sym", "kind", "variant", "name", "direction", "opt_key", "entry", "und0",
                    "end_pct", "mfe_pct", "mfe_min", "mae_pct", "mae_min", "dd_before_peak", "giveback"] + \
                   [f"t_p{u}" for u in UPS] + [f"t_m{x}" for x in DOWNS] + [f"pct{m}" for m in AT_MIN] + \
                   ["first", "recovered", "events", "path"]
            c.executemany(f"INSERT OR REPLACE INTO trade_paths({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                          [[json.dumps(r[k]) if k in ("events", "path") else r.get(k) for k in cols] for r in rows])
            c.execute("INSERT OR REPLACE INTO path_days VALUES (?,?,?,?,?)", (source, day, len(rows), time.time(), note))
            c.commit()
        finally:
            c.close()
    log(f"Trade journeys {day}: {len(rows)} signals followed to 15:15" + (f" ({note})" if note else ""))
    return {"day": day, "n": len(rows), "note": note}


def pending_days(data_dir, source: str = "upstox", limit: int = 10) -> list:
    """Recorded days with bench signals that have no journeys yet (newest first)."""
    rec = Path(data_dir) / "recordings"
    days = sorted({p.name[len(source) + 1:len(source) + 11] for p in rec.glob(f"{source}_????-??-??_minutes.parquet")}, reverse=True)
    done = set()
    if db_path(data_dir).exists():
        try:
            c = connect(data_dir, ro=True)
            done = {r[0] for r in c.execute("SELECT day FROM path_days WHERE source=?", (source,))}
            c.close()
        except Exception:
            done = set()
    today = dt.datetime.now(IST)
    out = []
    for d in days:
        if d in done:
            continue
        if d == today.date().isoformat() and today.time() < dt.time(15, 40):
            continue                                           # the session isn't over
        out.append(d)
    return out[:limit]


def run(data_dir, log=print, should_stop=lambda: False, days=None, source: str = "upstox") -> dict:
    """The child-process job: journeys for the given days, or every recorded day not done yet (at most 10 a run)."""
    days = days or pending_days(data_dir, source)
    done = []
    for d in days:
        if should_stop():
            break
        try:
            done.append(build_day(data_dir, d, source, log, should_stop))
        except Exception as e:
            log(f"Trade journeys {d} failed: {e}")
    return {"days": done}


# ---------------------------------------------------------------- reading
def _stats(rows: list) -> dict:
    a = lambda k: [r[k] for r in rows if r.get(k) is not None]
    mean = lambda xs: round(float(np.mean(xs)), 1) if xs else None
    med = lambda xs: round(float(np.median(xs)), 0) if xs else None
    n = len(rows)
    deep = [r for r in rows if (r.get("mae_pct") or 0) <= -20]
    return {"n": n, "p20_first": round(100 * sum(1 for r in rows if r["first"] == "+20") / n, 0) if n else None,
            "m20_first": round(100 * sum(1 for r in rows if r["first"] == "-20") / n, 0) if n else None,
            "mfe": mean(a("mfe_pct")), "mfe_min": med(a("mfe_min")), "giveback": mean(a("giveback")),
            "mae": mean(a("mae_pct")), "mae_min": med(a("mae_min")), "end": mean(a("end_pct")), "pct60": mean(a("pct60")),
            "recovered": round(100 * sum(r["recovered"] for r in deep) / len(deep), 0) if deep else None, "deep": len(deep)}


def _exit_value(r: dict, tgt, stop, horizon) -> float | None:
    """What a fixed target / stop / time limit would have made on this journey (option %, before charges)."""
    tt = r.get(f"t_p{tgt}") if tgt else None
    ts_ = r.get(f"t_m{stop}") if stop else None
    lim = {"30": 30, "60": 60, "120": 120}.get(str(horizon), 10 ** 6)
    hit_t = tt is not None and tt <= lim
    hit_s = ts_ is not None and ts_ <= lim
    if hit_t and (not hit_s or tt < ts_):
        return float(tgt)
    if hit_s:
        return -float(stop)
    v = r.get(f"pct{horizon}") if horizon in (30, 60, 120) else r.get("end_pct")
    return None if v is None else float(v)


def best_exit(rows: list, min_n: int = 20) -> dict | None:
    """Fixed target / stop / time chosen on the older 60% of days, judged on the newer 40% (never used to choose)."""
    days = sorted({r["day"] for r in rows})
    if len(days) < 5 or len(rows) < min_n:
        return None
    cut = days[int(len(days) * 0.6)]
    tr, ho = [r for r in rows if r["day"] < cut], [r for r in rows if r["day"] >= cut]
    if len(tr) < min_n * 0.6 or len(ho) < 5:
        return None
    best = None
    for tgt in (None,) + UPS:
        for stop in (None,) + DOWNS:
            for hz in (30, 60, 120, "eod"):
                v = [x for x in (_exit_value(r, tgt, stop, hz) for r in tr) if x is not None]
                if len(v) < min_n * 0.6:
                    continue
                m = float(np.mean(v))
                if best is None or m > best[0]:
                    best = (m, tgt, stop, hz)
    if best is None:
        return None
    _m, tgt, stop, hz = best
    hv = [x for x in (_exit_value(r, tgt, stop, hz) for r in ho) if x is not None]
    return {"target": tgt, "stop": stop, "time": hz, "train_avg": round(best[0], 1), "train_n": len(tr),
            "hold_avg": round(float(np.mean(hv)), 1) if hv else None, "hold_n": len(hv)}


def _ctx_summary(rows: list, what: str) -> dict | None:
    ev = [e for r in rows for e in (r.get("events") or []) if e.get("what") == what and e.get("ctx")]
    if len(ev) < 5:
        return None
    pick = lambda k: [e["ctx"][k] for e in ev if k in e["ctx"]]
    pct = lambda xs, f: round(100 * sum(1 for x in xs if f(x)) / len(xs), 0) if xs else None
    r5, vw, iv, nf = pick("ret5"), pick("vwap"), pick("iv"), pick("nifty")
    return {"n": len(ev), "last5_against": pct(r5, lambda x: x < 0), "wrong_side_vwap": pct(vw, lambda x: x < 0),
            "iv_falling": pct(iv, lambda x: x < 0), "nifty_against": pct(nf, lambda x: x < 0),
            "iv_avg": round(float(np.mean(iv)), 2) if iv else None,
            "vwap_avg": round(float(np.mean(vw)), 2) if vw else None, "und_avg": round(float(np.mean(pick("und"))), 2) if pick("und") else None}


def view(data_dir, source: str = "upstox", days: int = 60, top: int = 40) -> dict:
    """For the Strategy board: per strategy (alert kind / setup), what its trades did when held to 15:15."""
    if not db_path(data_dir).exists():
        return {"ready": False, "groups": [], "days": []}
    c = connect(data_dir, ro=True)
    try:
        since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        rows = [dict(r) for r in c.execute("SELECT * FROM trade_paths WHERE source=? AND day>=?", (source, since))]
        dl = [dict(r) for r in c.execute("SELECT day, n, note FROM path_days WHERE source=? ORDER BY day DESC LIMIT 30", (source,))]
    finally:
        c.close()
    for r in rows:
        for k in ("events", "path"):
            try:
                r[k] = json.loads(r[k]) if r.get(k) else []
            except Exception:
                r[k] = []
    groups = {}
    for r in rows:
        name = r.get("name") or r["kind"]
        groups.setdefault((r["kind"], r.get("variant") or "", name), []).append(r)
    out = []
    for (kind, variant, name), g in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:top]:
        out.append({"kind": kind, "variant": variant, "name": name, **_stats(g), "best_exit": best_exit(g),
                    "at_peak": _ctx_summary(g, "peak"), "at_bottom": _ctx_summary(g, "bottom")})
    return {"ready": True, "all": _stats(rows) if rows else {"n": 0}, "groups": out, "days": dl,
            "at_peak": _ctx_summary(rows, "peak"), "at_bottom": _ctx_summary(rows, "bottom"),
            "at_drop": _ctx_summary(rows, "giveback"), "at_turn": _ctx_summary(rows, "recover"),
            "best_exit_all": best_exit(rows)}
