"""Trial (shadow) alerts: new bullets tested live before you ever see them.

A kind marked "shadow": True in live/registry.py never reaches the alert feed, Telegram, Focus, the AI notes, the live
scorecard, the conviction model or the paper account. The engine hands it here instead. Each trial is recorded with its
entry, stop and target on the underlying and followed tick by tick: target first = hit, stop first = miss, otherwise
its result in R (risk units) when the time limit ends. Kept in its own table, so nothing that reads alerts sees it.

The Lab tests the same ideas on 5 years of history; this is the live check (champion vs challenger on graded live
alerts). A kind is promoted by removing its "shadow" flag in the registry once it beats the current alerts.
Market data only: nothing is traded.
"""
from __future__ import annotations

import json
import time

SQL = """
CREATE TABLE IF NOT EXISTS trial_alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day TEXT, source TEXT, sym TEXT, kind TEXT, direction TEXT,
  und TEXT, entry REAL, stop REAL, target REAL, title TEXT, detail TEXT, data TEXT,
  result TEXT, reason TEXT, r_exit REAL, r15 REAL, mfe REAL, mae REAL, exit_ts REAL);
CREATE INDEX IF NOT EXISTS trial_kind ON trial_alerts(source, kind);
"""
HOLD_MIN = 60                  # a trial not decided by its stop or target is closed after this many minutes


class Trials:
    def __init__(self, store, source, log=print):
        self.store, self.source, self.log = store, source, log
        self.open: list[dict] = []
        self.cool: dict = {}
        with store.lock:
            store.db.executescript(SQL)
            if source == "sim":
                store.db.execute("DELETE FROM trial_alerts WHERE source='sim'")
            store.db.commit()
        self._reload()

    def _reload(self):
        """After a restart: keep following today's trials that were still open (if their time limit hasn't passed)."""
        try:
            with self.store.lock:
                rows = self.store.db.execute(
                    "SELECT id, ts, sym, kind, direction, und, entry, stop, target FROM trial_alerts "
                    "WHERE source=? AND result IS NULL AND ts > ?", (self.source, time.time() - HOLD_MIN * 60)).fetchall()
            for r in rows:
                r = dict(r)
                r["d"] = 1 if r["direction"] == "up" else -1
                r["R"] = abs(r["entry"] - r["stop"])
                if r["R"] > 0:
                    self.open.append(r)
        except Exception as e:
            self.log(f"Trial alerts reload skipped: {e}")

    def add(self, a: dict, und_key: str | None, now_ts: float, cooldown_min: float) -> int | None:
        """Record one trial alert. Returns its id, or None when the same trial fired within its cooldown."""
        d = {"up": 1, "down": -1}.get(a.get("direction"))
        lv = (a.get("data") or {}).get("setup_levels") or {}
        entry, stop, target = lv.get("entry") or a.get("price"), lv.get("stop"), lv.get("target")
        key = (a["sym"], a["kind"], a.get("direction"))
        last = self.cool.get(key)
        if last and now_ts - last < cooldown_min * 60:
            return None
        self.cool[key] = now_ts
        gradable = bool(d and entry and stop and und_key and abs(entry - stop) > 0)
        with self.store.lock:
            cur = self.store.db.execute(
                "INSERT INTO trial_alerts(ts, day, source, sym, kind, direction, und, entry, stop, target, title, detail, data, result) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (now_ts, a.get("day"), self.source, a["sym"], a["kind"], a.get("direction"), und_key, entry, stop, target,
                 a.get("title"), a.get("detail"), json.dumps(a.get("data") or {}, default=str), None if gradable else "ungraded"))
            self.store.db.commit()
            tid = cur.lastrowid
        if gradable:
            self.open.append({"id": tid, "ts": now_ts, "sym": a["sym"], "kind": a["kind"], "d": d, "und": und_key,
                              "entry": float(entry), "stop": float(stop), "target": float(target) if target else None,
                              "R": abs(float(entry) - float(stop))})
        return tid

    def update(self, state, now_ts: float, force=False):
        if not self.open:
            return
        keep, done = [], []
        for r in self.open:
            i = state.get(r["und"]) if state is not None and r.get("und") else None
            spot = i.ltp if i and i.ltp else r.get("_last")
            if not spot:
                keep.append(r)
                continue
            r["_last"] = spot
            x = r["d"] * (spot - r["entry"]) / r["R"]
            r["mfe"] = max(r.get("mfe") or 0.0, x)
            r["mae"] = min(r.get("mae") or 0.0, x)
            age = (now_ts - r["ts"]) / 60
            if r.get("r15") is None and age >= 15:
                r["r15"] = round(x, 2)
            why = None
            if r["d"] * (spot - r["stop"]) <= 0:
                why, res = "stop", "miss"
            elif r.get("target") and r["d"] * (spot - r["target"]) >= 0:
                why, res = "target", "hit"
            elif age >= HOLD_MIN or force:
                why, res = ("time" if age >= HOLD_MIN else "session end"), "time"
            if not why:
                keep.append(r)
                continue
            if res == "hit":                                  # a target is a resting order: no credit beyond it
                x = r["d"] * (r["target"] - r["entry"]) / r["R"]
            r.update(result=res, reason=why, r_exit=round(x, 2), exit_ts=now_ts)   # a stop counts where price really was
            done.append(r)
        self.open = keep
        if done:
            with self.store.lock:
                self.store.db.executemany(
                    "UPDATE trial_alerts SET result=?, reason=?, r_exit=?, r15=?, mfe=?, mae=?, exit_ts=? WHERE id=?",
                    [(r["result"], r["reason"], r["r_exit"], r.get("r15"), round(r.get("mfe") or 0, 2),
                      round(r.get("mae") or 0, 2), r["exit_ts"], r["id"]) for r in done])
                self.store.db.commit()


def board(store, source, days: int = 90, recent: int = 40) -> dict:
    """Per trial kind: how many, hits (target first), misses (stop first), timed out, average R. Plus the latest trials."""
    from . import registry
    try:
        with store.lock:
            rows = [dict(r) for r in store.db.execute(
                "SELECT kind, sym, result, r_exit, r15, day FROM trial_alerts WHERE source=? AND day >= date('now', ?)",
                (source, f"-{days} day")).fetchall()]
            last = [dict(r) for r in store.db.execute(
                "SELECT id, ts, day, sym, kind, direction, entry, stop, target, title, result, reason, r_exit FROM trial_alerts "
                "WHERE source=? ORDER BY id DESC LIMIT ?", (source, recent)).fetchall()]
    except Exception:
        rows, last = [], []
    by = {}
    for r in rows:
        b = by.setdefault(r["kind"], {"n": 0, "hits": 0, "misses": 0, "timed": 0, "open": 0, "rs": [], "days": set(), "idx": 0})
        b["n"] += 1
        b["days"].add(r["day"])
        b["idx"] += r["sym"] in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX")
        if r["result"] is None:
            b["open"] += 1
        elif r["result"] == "hit":
            b["hits"] += 1
        elif r["result"] == "miss":
            b["misses"] += 1
        elif r["result"] == "time":
            b["timed"] += 1
        if r["r_exit"] is not None:
            b["rs"].append(r["r_exit"])
    kinds = []
    for k, b in by.items():
        dec = b["hits"] + b["misses"]
        kinds.append({"kind": k, "label": registry.label(k), "n": b["n"], "days": len(b["days"]), "on_indices": b["idx"],
                      "hits": b["hits"], "misses": b["misses"], "timed": b["timed"], "open": b["open"],
                      "hit_rate": round(b["hits"] / dec * 100) if dec else None,
                      "avg_r": round(sum(b["rs"]) / len(b["rs"]), 2) if b["rs"] else None,
                      "graded": len(b["rs"])})
    kinds.sort(key=lambda x: -(x["avg_r"] if x["avg_r"] is not None else -99))
    for x in last:
        x["label"] = registry.label(x["kind"])
    on_trial = [{"kind": k, "label": registry.label(k)} for k in registry.shadow_kinds()]
    return {"kinds": kinds, "recent": last, "on_trial": on_trial, "hold_min": HOLD_MIN,
            "note": "On trial: recorded and graded only. Not in your alerts, Telegram, Focus, scorecard or paper trades. "
                    "Target = 2R, stop beyond the pattern; R = entry to stop on the index / stock price. Before charges. "
                    "A kind is promoted only when it beats the current alerts on enough days."}
