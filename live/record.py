"""P1: record everything, score every alert, and measure which signals work.

- Recorder: one SQLite file per day with a snapshot of every instrument each minute
- Tracker: follows each alert for 60 minutes and grades it (hit / miss / flat)
- scorecard(): hit rate by alert type, used on every new alert and on the Scorecard page
- evidence(), levels(), signal_score(): confluence, target and invalidation for an alert
"""
from __future__ import annotations

import json
import math
import sqlite3
import threading
from pathlib import Path

HORIZONS = (5, 15, 60)
OPT_TARGET, OPT_STOP = 20.0, 20.0         # option +20% before -20% within 60 min = hit


# ---------------------------------------------------------------- recorder
class Recorder:
    def __init__(self, data_dir: Path, source: str, day: str):
        d = Path(data_dir) / "recordings"
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / f"{source}_{day}.sqlite"
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS minutes (ts INTEGER, key TEXT, ltp REAL, vol REAL, oi REAL, iv REAL,
              bid REAL, ask REAL, tbq REAL, tsq REAL, cvd REAL);
            CREATE INDEX IF NOT EXISTS minutes_key ON minutes(key, ts);
            CREATE TABLE IF NOT EXISTS metrics (ts INTEGER, sym TEXT, data TEXT);
            CREATE INDEX IF NOT EXISTS metrics_sym ON metrics(sym, ts);""")
            self.db.commit()
        self.rows = 0

    def snapshot(self, ts: int, state, metrics: dict, slim):
        with state.lock:
            rows = [(ts, i.key, i.ltp, i.vol, i.oi, i.iv, i.bid, i.ask, i.tbq, i.tsq, i.cvd)
                    for i in state.insts.values() if i.ltp]
        mrows = [(ts, s, json.dumps(slim(m), default=str)) for s, m in metrics.items()]
        with self.lock:
            self.db.executemany("INSERT INTO minutes VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
            self.db.executemany("INSERT INTO metrics VALUES (?,?,?)", mrows)
            self.db.commit()
        self.rows += len(rows)

    def finalize(self, keep_days: int = 365, log=print):
        """End of day: squeeze the day into compressed Parquet files (about 10x smaller) and drop old days."""
        try:
            import pandas as pd
            with self.lock:
                mins = pd.read_sql("SELECT * FROM minutes", self.db)
                mets = pd.read_sql("SELECT * FROM metrics", self.db)
            if len(mins):
                base = self.path.with_suffix("")
                mins.to_parquet(f"{base}_minutes.parquet", compression="zstd", index=False)
                mets.to_parquet(f"{base}_metrics.parquet", compression="zstd", index=False)
                self.close()
                self.path.unlink(missing_ok=True)
                log(f"Saved today's recording ({len(mins):,} rows)")
        except Exception as e:
            log(f"Compressing the recording failed (kept as is): {e}")
        import datetime as _dt
        cutoff = (_dt.date.today() - _dt.timedelta(days=keep_days)).isoformat()
        for f in self.path.parent.glob("*"):
            day = f.stem.split("_")[1] if "_" in f.stem else ""
            if day and day < cutoff:
                f.unlink(missing_ok=True)

    def last_metrics(self) -> dict:
        with self.lock:
            r = self.db.execute("SELECT sym, data FROM metrics WHERE ts = (SELECT MAX(ts) FROM metrics)").fetchall()
        return {s: json.loads(d) for s, d in r}

    def close(self):
        with self.lock:
            self.db.close()


def size_mb(data_dir: Path) -> float:
    d = Path(data_dir) / "recordings"
    return round(sum(f.stat().st_size for f in d.glob("*") if f.is_file()) / 1e6, 1) if d.exists() else 0.0


# ---------------------------------------------------------------- outcome tracking
OUTCOME_SQL = """
CREATE TABLE IF NOT EXISTS outcomes (
  alert_id INTEGER PRIMARY KEY, source TEXT, day TEXT, ts REAL, sym TEXT, kind TEXT, direction TEXT,
  severity INTEGER, is_index INTEGER, p0 REAL, o0 REAL, opt_key TEXT, thr REAL,
  r5 REAL, r15 REAL, r60 REAL, o5 REAL, o15 REAL, o60 REAL, mfe REAL, mae REAL, omax REAL, omin REAL,
  result TEXT, resolved_ts REAL, features TEXT);
CREATE INDEX IF NOT EXISTS outcomes_kind ON outcomes(source, kind);
"""


class Tracker:
    """Grades each directional alert over the next 60 minutes.
    With an option idea: hit = option +20% before -20%. Without: price moves `thr`% in the alert's
    direction before the same against it. Neither within 60 minutes = flat."""

    def __init__(self, store, source):
        self.store = store
        self.source = source
        with store.lock:
            store.db.executescript(OUTCOME_SQL)
            store.db.commit()
        self.open: dict[int, dict] = {}

    def add(self, a: dict, m: dict, state, now_ts: float, features: dict):
        if a["direction"] not in ("up", "down") or not a.get("price"):
            return
        o = a.get("option")
        oi = state.get(o["key"]) if o else None
        sig = (m or {}).get("sigma_1m") or 0.05
        thr = max(0.15 if a.get("is_index") else 0.3, sig * math.sqrt(30))
        rec = {"alert_id": a["id"], "source": self.source, "day": a["day"], "ts": now_ts, "sym": a["sym"],
               "kind": a["kind"], "direction": a["direction"], "severity": a["severity"],
               "is_index": int(bool(a.get("is_index"))), "p0": a["price"], "o0": oi.ltp if oi and oi.ltp else None,
               "opt_key": o["key"] if o else None, "thr": round(thr, 3), "mfe": 0.0, "mae": 0.0,
               "omax": 0.0, "omin": 0.0, "result": None, "features": json.dumps(features, default=str)}
        cols = list(rec)
        with self.store.lock:
            self.store.db.execute(f"INSERT OR REPLACE INTO outcomes({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                  [rec[c] for c in cols])
            self.store.db.commit()
        rec["und_key"] = a.get("und_key")
        self.open[a["id"]] = rec

    def update(self, state, now_ts: float, force=False):
        done = []
        for aid, r in list(self.open.items()):
            u = state.get(r["und_key"]) if r.get("und_key") else None
            px = u.ltp if u and u.ltp else None
            sgn = 1 if r["direction"] == "up" else -1
            mins = (now_ts - r["ts"]) / 60
            ch = {}
            if px:
                mv = (px / r["p0"] - 1) * 100 * sgn
                r["mfe"], r["mae"] = max(r["mfe"], mv), min(r["mae"], mv)
                r["_mv"] = mv
            if r["o0"]:
                oi = state.get(r["opt_key"])
                if oi and oi.ltp:
                    om = (oi.ltp / r["o0"] - 1) * 100
                    r["omax"], r["omin"] = max(r["omax"], om), min(r["omin"], om)
                    r["_om"] = om
            for h in HORIZONS:
                if mins >= h and r.get(f"r{h}") is None:
                    r[f"r{h}"] = round(r.get("_mv", 0.0), 3)
                    r[f"o{h}"] = round(r["_om"], 2) if r.get("_om") is not None else None
                    ch[f"r{h}"], ch[f"o{h}"] = r[f"r{h}"], r[f"o{h}"]
            if r["result"] is None:
                if r["o0"]:
                    if r["omax"] >= OPT_TARGET:
                        r["result"] = "hit"
                    elif r["omin"] <= -OPT_STOP:
                        r["result"] = "miss"
                else:
                    if r["mfe"] >= r["thr"]:
                        r["result"] = "hit"
                    elif r["mae"] <= -r["thr"]:
                        r["result"] = "miss"
                if r["result"]:
                    r["resolved_ts"] = now_ts
                    ch.update(result=r["result"], resolved_ts=now_ts)
            final = mins >= 60 or force
            if final and r["result"] is None:
                r["result"], r["resolved_ts"] = "flat", now_ts
                ch.update(result="flat", resolved_ts=now_ts)
            ch.update(mfe=round(r["mfe"], 3), mae=round(r["mae"], 3), omax=round(r["omax"], 2), omin=round(r["omin"], 2))
            if final:
                for h in HORIZONS:          # session ended early: fill what we have
                    if r.get(f"r{h}") is None:
                        ch[f"r{h}"] = round(r.get("_mv", 0.0), 3)
                        ch[f"o{h}"] = round(r["_om"], 2) if r.get("_om") is not None else None
                done.append(aid)
            with self.store.lock:
                self.store.db.execute(f"UPDATE outcomes SET {', '.join(k + '=?' for k in ch)} WHERE alert_id=?",
                                      [*ch.values(), aid])
        if self.open:
            with self.store.lock:
                self.store.db.commit()
        for aid in done:
            self.open.pop(aid, None)
        return done


def outcomes(store, source=None, day=None, limit=5000) -> list:
    q, args = "SELECT * FROM outcomes WHERE 1=1", []
    if source:
        q += " AND source=?"
        args.append(source)
    if day:
        q += " AND day=?"
        args.append(day)
    q += " ORDER BY ts DESC LIMIT ?"
    args.append(limit)
    with store.lock:
        try:
            return [dict(r) for r in store.db.execute(q, args).fetchall()]
        except sqlite3.OperationalError:
            return []


def scorecard(store, source) -> dict:
    """Hit rates by alert type. Keys: (kind, is_index) and (kind, is_index, severity)."""
    rows = [r for r in outcomes(store, source) if r["result"]]
    groups = {}
    for r in rows:
        for k in ((r["kind"], r["is_index"]), (r["kind"], r["is_index"], r["severity"]), (r["kind"],)):
            g = groups.setdefault(k, {"n": 0, "hits": 0, "misses": 0, "o15": [], "o60": [], "r15": [], "recent": []})
            g["n"] += 1
            g["hits"] += r["result"] == "hit"
            g["misses"] += r["result"] == "miss"
            for f in ("o15", "o60", "r15"):
                if r.get(f) is not None:
                    g[f].append(r[f])
            if len(g["recent"]) < 20 and r["result"] in ("hit", "miss"):
                g["recent"].append(r["result"] == "hit")
    out = {}
    for k, g in groups.items():
        avg = lambda xs: round(sum(xs) / len(xs), 2) if xs else None
        dec = g["hits"] + g["misses"]
        # hit rate among decided alerts (option +20% first vs -20% first); flat hours are not counted as failures
        out[k] = {"n": g["n"], "decided": dec, "hits": g["hits"], "misses": g["misses"],
                  "flat_pct": round((g["n"] - dec) / g["n"] * 100) if g["n"] else None,
                  "hit_rate": round(g["hits"] / dec * 100) if dec else None,
                  "avg_o15": avg(g["o15"]), "avg_o60": avg(g["o60"]), "avg_r15": avg(g["r15"]),
                  "recent_rate": round(sum(g["recent"]) / len(g["recent"]) * 100) if g["recent"] else None,
                  "recent_n": len(g["recent"])}
    return out


# ---------------------------------------------------------------- confluence, levels, score
def evidence(m: dict, direction: str, market: dict | None = None) -> list:
    """Independent facts that agree (+1) or disagree (-1) with the alert direction."""
    if direction not in ("up", "down") or not m:
        return []
    s = 1 if direction == "up" else -1
    ev = []
    def add(val, label_up, label_down, neutral_band=0.0):
        if val is None or abs(val) <= neutral_band:
            return
        agree = (val > 0) == (s > 0)
        ev.append({"label": label_up if val > 0 else label_down, "agree": agree})
    add(m.get("vs_vwap_pct"), "above VWAP", "below VWAP", 0.05)
    f = m.get("fut") or {}
    if f.get("buildup_sign"):
        add(f["buildup_sign"], (f.get("buildup_label") or "").lower(), (f.get("buildup_label") or "").lower())
    add(m.get("ret_15m"), "rising 15 min", "falling 15 min", 0.1)
    if m.get("or_high") and m.get("spot"):
        if m["spot"] > m["or_high"]:
            add(1, "above opening range", "")
        elif m["spot"] < m["or_low"]:
            add(-1, "", "below opening range")
    fl = m.get("flow") or {}
    add(fl.get("delta_pct_5m"), "buyers aggressive (order flow)", "sellers aggressive (order flow)", 15)
    un = [u for u in (m.get("opt") or {}).get("unusual") or []]
    if un:
        d = sum(1 if u["direction"] == "up" else -1 for u in un)
        add(d, "option flow bullish", "option flow bearish")
    if market and not m.get("is_index") and market.get("ret_15m") is not None:
        add(market["ret_15m"], "market rising", "market falling", 0.08)
    if (m.get("rel_vol") or 0) >= 1.5:
        ev.append({"label": f"volume {m['rel_vol']:.1f}× normal", "agree": True})
    return ev


def levels(m: dict, direction: str, state) -> dict:
    """Invalidation (where the idea is wrong) and a first target, from real levels on the chart."""
    spot = m.get("spot")
    if not spot or direction not in ("up", "down"):
        return {}
    sig = (m.get("sigma_1m") or 0.05) / 100
    o = m.get("opt") or {}
    bars = state.bars.get(m["sym"])
    recent = list(bars.bars)[-15:] if bars else []
    lo15 = min(b[3] for b in recent) if recent else None
    hi15 = max(b[2] for b in recent) if recent else None
    gap = 0.0015
    if direction == "up":
        below = [x for x in (m.get("vwap"), m.get("or_high") if (m.get("or_high") or 1e18) < spot else None,
                             o.get("support"), lo15) if x and x < spot * (1 - gap)]
        stop = max(below) if below else spot * (1 - sig * math.sqrt(30))
        above = [x for x in (o.get("resistance"), m.get("day_h")) if x and x > spot * (1 + 0.002)]
        target = min(above) if above else spot * (1 + 1.5 * sig * math.sqrt(60))
    else:
        above = [x for x in (m.get("vwap"), m.get("or_low") if (m.get("or_low") or 0) > spot else None,
                             o.get("resistance"), hi15) if x and x > spot * (1 + gap)]
        stop = min(above) if above else spot * (1 + sig * math.sqrt(30))
        below = [x for x in (o.get("support"), m.get("day_l")) if x and x < spot * (1 - 0.002)]
        target = max(below) if below else spot * (1 - 1.5 * sig * math.sqrt(60))
    # sanity: a stop inside the noise of the next 15 minutes is hit by chance, and a target beyond what the rest of
    # the session can usually travel is a wish. Both made reward:risk numbers like 18 that meant nothing.
    d = 1 if direction == "up" else -1
    min_stop = max(0.003, sig * math.sqrt(15))
    max_tgt = max(0.004, 1.5 * sig * math.sqrt(120))
    if abs(spot - stop) / spot < min_stop:
        stop = spot * (1 - d * min_stop)
    elif abs(spot - stop) / spot > max_tgt:          # the nearest level is too far: use a volatility stop instead
        stop = spot * (1 - d * max(min_stop, sig * math.sqrt(30)))
    if abs(target - spot) / spot > max_tgt:
        target = spot * (1 + d * max_tgt)
    risk, reward = abs(spot - stop), abs(target - spot)
    return {"stop": round(stop, 2), "target": round(target, 2), "rr": round(reward / risk, 2) if risk else None,
            "stop_pct": round(risk / spot * 100, 2), "target_pct": round(reward / spot * 100, 2)}


def signal_score(sev: int, ev: list, stats: dict | None, regime_adj: int = 0, model_p: float | None = None) -> int:
    agree = sum(e["agree"] for e in ev)
    against = sum(not e["agree"] for e in ev)
    s = 35 + 8 * sev + 7 * agree - 9 * against + regime_adj
    if stats and stats.get("decided", 0) >= 15 and stats.get("hit_rate") is not None:
        s += (stats["hit_rate"] - 55) * 0.6
    if model_p is not None:
        s = 0.5 * s + 0.5 * model_p * 100
    return int(max(1, min(99, round(s))))
