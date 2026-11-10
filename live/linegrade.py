"""2.54 live grading of the smart lines (shadow): every first touch of every line the app draws on Nifty / Bank Nifty
is recorded with the situation and what the line book expected, then graded HOLD / BREAK within 60 minutes with the
same rules the book was built with (lab/levelbook.py). Read by the chart's Smart lines panel and nothing else: no alert,
paper trade or Lab result depends on it.

Also keeps your own line book fresh: once the session is over it rebuilds data/lab/level_book.json from the Mac's
1-minute history when the book is older than rules/lines.json -> rebuild_after_days. Market data only.
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np

from lab import levelbook as LB

SQL = """CREATE TABLE IF NOT EXISTS line_touches (
  source TEXT, day TEXT, sym TEXT, type TEXT, price REAL, side INTEGER, minute INTEGER, at REAL, speed_r REAL,
  day_side TEXT, verdict TEXT, p_hold REAL, random_hold REAL, matters INTEGER, result TEXT,
  PRIMARY KEY (source, day, sym, type, price));
CREATE INDEX IF NOT EXISTS line_touches_day ON line_touches(source, day);"""
SYMS = ("NIFTY", "BANKNIFTY")
LIVE_ONLY = ("oi_call", "oi_put", "max_pain", "gamma_flip")


class LineGrader:
    def __init__(self, engine, store, source: str, log=print):
        self.e, self.store, self.source, self.log = engine, store, source, log
        with store.lock:
            store.db.executescript(SQL)
            store.db.commit()
        self.first_seen: dict = {}          # (day, sym, type, price) -> minute a live-only line first appeared
        self._sum: dict = {}

    def tick(self, now: dt.datetime):
        """Grade today's line touches on Nifty / Bank Nifty from today's 1-minute bars (idempotent)."""
        e = self.e
        if e.state is None or e.charts is None:
            return
        book = LB.load(e.data_dir)
        day = now.date().isoformat()
        rows_out = []
        for sym in SYMS:
            m = (e.metrics or {}).get(sym)
            rows = e.charts.today_bars(sym)
            if not m or len(rows) < 20:
                continue
            t0 = e.charts._session_start(now.date())
            rows = [r for r in rows if t0 <= r[0] < t0 + 6.5 * 3600]
            if len(rows) < 20:
                continue
            rows_out += self._grade(sym, day, rows, m, book)
        if rows_out:
            cols = ("source", "day", "sym", "type", "price", "side", "minute", "at", "speed_r", "day_side", "verdict", "p_hold",
                    "random_hold", "matters", "result")
            with self.store.lock:
                self.store.db.executemany(f"INSERT OR REPLACE INTO line_touches({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                          [[r[c] for c in cols] for r in rows_out])
                self.store.db.commit()
            self._sum.clear()

    def _grade(self, sym, day, rows, m, book) -> list:
        c = LB.cfg()
        p = LB.params(sym, True)
        o1, h1, l1, c1 = (np.array([r[k] for r in rows], float) for k in (1, 2, 3, 4))
        d = (self.e.daily or {}).get(sym) or {}
        prev = [(float(x[2]), float(x[3]), float(x[4])) for x in (d.get("d_ohlc") or []) if str(x[0])[:10] < day]
        first = int(c.get("first_minute") or 15)
        lines = [(t, x, first) for t, x in LB.day_lines(sym, float(o1[0]), prev)]
        orr = LB.opening_range(h1, l1, 15)
        if orr:
            lines += [("or_h", orr[0], 15), ("or_l", orr[1], 15)]
        lines += [(t, x, max(first, mm)) for t, x, mm in LB.swings5(h1, l1)]
        o = m.get("opt") or {}
        g = (getattr(self.e, "gex", {}) or {}).get(sym) or {}
        now_min = len(c1) - 1
        for t, x in (("oi_call", o.get("resistance")), ("oi_put", o.get("support")), ("max_pain", o.get("max_pain")),
                     ("gamma_flip", g.get("flip"))):
            if x:
                k = (day, sym, t, round(float(x), 2))
                self.first_seen.setdefault(k, now_min)          # only touches after the line was there count
                lines.append((t, float(x), max(first, self.first_seen[k])))
        if len(self.first_seen) > 2000:
            self.first_seen = {k: v for k, v in self.first_seen.items() if k[0] == day}
        lb, thr, win = int(c.get("speed_lookback_min") or 15), float(c.get("day_move_pct") or 0.3), int(c.get("window_min") or 60)
        out, seen = [], set()
        for typ, L, start in lines:
            key = (typ, round(L, 2))
            if key in seen:
                continue
            seen.add(key)
            r = LB.first_touch(L, start, h1, l1, c1, p, win)
            if r is None:
                continue
            t, side, res = r
            if res is None and now_min - t < win:
                res = None                                      # still inside its 60 minutes
            elif res is None:
                res = "neither"
            speed_r = LB.speed_toward(c1, t, side, lb) / p["move_pct"]
            dside = LB.day_side(c1[t], float(o1[0]), side, thr)
            ev = LB.evidence(book, typ, speed_r, dside) or {}
            out.append({"source": self.source, "day": day, "sym": sym, "type": typ, "price": round(L, 2), "side": side,
                        "minute": int(t), "at": rows[t][0], "speed_r": round(speed_r, 3), "day_side": dside,
                        "verdict": ev.get("verdict") or "not tested yet", "p_hold": ev.get("hold"),
                        "random_hold": ev.get("random_hold"), "matters": int(bool(ev.get("matters"))), "result": res})
        return out

    def summary(self, sym: str | None = None, days: int = 60) -> dict:
        """The live record: how the book's calls did on real touches since 2.54 (cached a minute)."""
        k = (sym, days)
        c = self._sum.get(k)
        if c and time.time() - c[0] < 60:
            return c[1]
        since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        q = "SELECT type, verdict, p_hold, matters, result FROM line_touches WHERE source=? AND day>=?" + (" AND sym=?" if sym else "")
        with self.store.lock:
            rows = self.store.db.execute(q, (self.source, since) + ((sym,) if sym else ())).fetchall()
        dec = [r for r in rows if r[4] in ("hold", "break")]
        by_v = {}
        for v in ("likely holds", "likely breaks", "coin flip", "not tested yet"):
            x = [r for r in dec if r[1] == v]
            if x:
                held = sum(r[4] == "hold" for r in x)
                by_v[v] = {"n": len(x), "held_pct": round(held / len(x) * 100, 1),
                           "right_pct": round((held if v == "likely holds" else len(x) - held) / len(x) * 100, 1)
                           if v in ("likely holds", "likely breaks") else None}
        by_t = {}
        for r in dec:
            t = by_t.setdefault(r[0], [0, 0])
            t[0] += 1
            t[1] += r[4] == "hold"
        ps = [(r[2], r[4] == "hold") for r in dec if r[2] is not None]
        out = {"touches": len(rows), "decided": len(dec), "by_verdict": by_v,
               "by_type": {t: {"n": v[0], "held_pct": round(v[1] / v[0] * 100, 1)} for t, v in sorted(by_t.items(), key=lambda x: -x[1][0])},
               "expected_hold": round(float(np.mean([a for a, _ in ps])), 1) if ps else None,
               "actual_hold": round(float(np.mean([b for _, b in ps])) * 100, 1) if ps else None}
        with self.store.lock:
            f = self.store.db.execute("SELECT MIN(day) FROM line_touches WHERE source=?", (self.source,)).fetchone()
        out["since"] = f[0] if f else None
        self._sum[k] = (time.time(), out)
        return out

    def maybe_rebuild(self, now: dt.datetime, market_open: bool):
        maybe_rebuild(self.e.data_dir, market_open, self.log)


def maybe_rebuild(data_dir, market_open: bool, log=print) -> bool:
    """Outside market hours: rebuild your own line book when it is missing or older than rebuild_after_days
    (about 20 seconds of work in its own thread). Called by the app's watcher while the scanner is not running."""
    if market_open:
        return False
    d = Path(data_dir)
    if not (d / "history" / "NIFTY" / "minutes.parquet").exists():
        return False
    f = d / "lab" / "level_book.json"
    age_days = (time.time() - f.stat().st_mtime) / 86400 if f.exists() else 1e9
    if age_days >= float(LB.cfg().get("rebuild_after_days") or 7) and LB.rebuild_async(d, log=log):
        log("Line book: rebuilding from your 1-minute history (smart chart lines)")
        return True
    return False
