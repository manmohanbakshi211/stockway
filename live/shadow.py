"""Shadow grading of exits on live alerts: champion vs challengers on the same trades.

For every directional alert with an option, each exit style (lab/exits.py) plus the app's current exit (a fixed % of the
premium, the champion) is followed tick by tick as if that option had been bought. Nothing is traded and nothing the
user sees changes: the results only say, on real live alerts, which way of setting stop-loss and target works best.
"""
from __future__ import annotations

from lab.exits import STYLES, LivePlan

SQL = """
CREATE TABLE IF NOT EXISTS plan_outcomes (
  alert_id INTEGER, style TEXT, source TEXT, day TEXT, sym TEXT, kind TEXT, ts REAL, o0 REAL, s0 REAL, d INTEGER, R REAL,
  cost REAL, fits40 INTEGER, exit_ts REAL, reason TEXT, o_exit REAL, opt_pct REAL, r_mult REAL,
  PRIMARY KEY (alert_id, style));
"""
CHAMPION = "current"


class PremiumExit:
    """The current exit: +target% / −stop% on the premium, or the time limit."""

    def __init__(self, entry, t0, tgt, stp, hold):
        self.entry, self.t0, self.tgt, self.stp, self.hold = entry, t0, tgt, stp, hold

    def check(self, spot, opt, now_ts):
        if opt:
            g = (opt / self.entry - 1) * 100
            if g >= self.tgt:
                return "target"
            if g <= -self.stp:
                return "stop"
        return "time" if (now_ts - self.t0) / 60 >= self.hold else None


class ShadowExits:
    def __init__(self, store, source, log=print):
        self.store, self.source, self.log = store, source, log
        with store.lock:
            store.db.executescript(SQL)
            store.db.commit()
        self.open: list[dict] = []

    def add(self, a: dict, s0: float, R: dict, champion: tuple, now_ts: float, small_max: float = 10000):
        """R: style -> risk in points; champion: (target %, stop %, minutes)."""
        o = a.get("option") or {}
        o0 = o.get("ltp")
        if not (o0 and s0 and a.get("direction") in ("up", "down")):
            return
        d = 1 if a["direction"] == "up" else -1
        cost = (o.get("ask") or o0) * (o.get("lot") or 1)
        base = {"alert_id": a["id"], "source": self.source, "day": a["day"], "sym": a["sym"], "kind": a["kind"], "ts": now_ts,
                "o0": o0, "s0": s0, "d": d, "cost": round(cost, 0), "fits40": int(cost <= small_max)}
        exits = {CHAMPION: (PremiumExit(o0, now_ts, *champion), None)}
        for st, r in R.items():
            if r and r > 0:
                exits[st] = (LivePlan(st, s0, d, r, o0, now_ts), r)
        for st, (ex, r) in exits.items():
            self.open.append({**base, "style": st, "R": r, "_ex": ex, "_opt": o.get("key"), "_und": a.get("und_key")})

    def update(self, state, now_ts: float, force=False):
        if not self.open:
            return
        keep, done = [], []
        for r in self.open:
            u, oi = state.get(r["_und"]) if r["_und"] else None, state.get(r["_opt"]) if r["_opt"] else None
            spot = u.ltp if u and u.ltp else None
            opt = oi.ltp if oi and oi.ltp else None
            if opt:
                r["_last_opt"] = opt
            if spot:
                r["_last_spot"] = spot
            why = r["_ex"].check(spot, opt, now_ts) or ("session end" if force else None)
            if not why:
                keep.append(r)
                continue
            ox, sx = r.get("_last_opt") or r["o0"], r.get("_last_spot") or r["s0"]
            r.update(exit_ts=now_ts, reason=why, o_exit=ox, opt_pct=round((ox / r["o0"] - 1) * 100, 2),
                     r_mult=round(r["d"] * (sx - r["s0"]) / r["R"], 2) if r["R"] else None)
            done.append(r)
        self.open = keep
        if done:
            cols = ["alert_id", "style", "source", "day", "sym", "kind", "ts", "o0", "s0", "d", "R", "cost", "fits40",
                    "exit_ts", "reason", "o_exit", "opt_pct", "r_mult"]
            with self.store.lock:
                self.store.db.executemany(f"INSERT OR REPLACE INTO plan_outcomes({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                          [[r.get(c) for c in cols] for r in done])
                self.store.db.commit()


def board(store, source, days: int = 60) -> dict:
    """Per exit style on live alerts: trades, average option return, wins, and the ₹40K subset."""
    with store.lock:
        rows = store.db.execute("SELECT style, opt_pct, cost, fits40, day FROM plan_outcomes WHERE source=? "
                                "AND day >= date('now', ?)", (source, f"-{days} day")).fetchall()
    out = {}
    for st, pct, cost, f40, day in rows:
        b = out.setdefault(st, {"n": 0, "sum": 0.0, "wins": 0, "rs": 0.0, "n40": 0, "rs40": 0.0, "days": set()})
        b["n"] += 1
        b["sum"] += pct or 0
        b["wins"] += (pct or 0) > 0
        rs = (pct or 0) / 100 * (cost or 0)
        b["rs"] += rs
        b["days"].add(day)
        if f40:
            b["n40"] += 1
            b["rs40"] += rs
    res = []
    for st, b in out.items():
        res.append({"style": st, "who": "current exit (premium %)" if st == CHAMPION else STYLES.get(st, {}).get("who", st),
                    "n": b["n"], "days": len(b["days"]), "avg_pct": round(b["sum"] / b["n"], 2), "win_pct": round(b["wins"] / b["n"] * 100, 1),
                    "avg_rs": round(b["rs"] / b["n"]), "n40": b["n40"], "avg_rs40": round(b["rs40"] / b["n40"]) if b["n40"] else None})
    res.sort(key=lambda x: -x["avg_rs"])
    return {"styles": res, "champion": CHAMPION, "note": "Shadow only: every directional alert's option followed under each exit. "
            "Before charges. Needs a few weeks of alerts before a difference means anything."}
