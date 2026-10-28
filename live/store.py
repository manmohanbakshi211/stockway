"""SQLite storage for alerts, paper trades and small cached values."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, day TEXT, source TEXT,
  sym TEXT, kind TEXT, direction TEXT, severity INTEGER, title TEXT, detail TEXT,
  price REAL, data TEXT, option TEXT, ai TEXT);
CREATE INDEX IF NOT EXISTS alerts_day ON alerts(day);
CREATE TABLE IF NOT EXISTS paper (
  id INTEGER PRIMARY KEY AUTOINCREMENT, alert_id INTEGER, source TEXT, day TEXT,
  sym TEXT, kind TEXT, opt_key TEXT, label TEXT, side TEXT, lot INTEGER, lots INTEGER,
  entry_ts REAL, entry REAL, target REAL, stop REAL, peak REAL, last REAL,
  exit_ts REAL, exit REAL, reason TEXT, status TEXT, gross REAL, costs REAL, net REAL, manual INTEGER);
CREATE INDEX IF NOT EXISTS paper_status ON paper(status);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""

ALERT_COLS = ("id", "ts", "day", "source", "sym", "kind", "direction", "severity", "title", "detail",
              "price", "data", "option", "ai", "story")


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)
            cols = {r[1] for r in self.db.execute("PRAGMA table_info(alerts)").fetchall()}
            if "story" not in cols:
                self.db.execute("ALTER TABLE alerts ADD COLUMN story INTEGER")
            self.db.commit()

    # ---------------- kv
    def get(self, k, default=None):
        with self.lock:
            r = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else default

    def put(self, k, v):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO kv(k, v) VALUES (?, ?)", (k, json.dumps(v)))
            self.db.commit()

    # ---------------- alerts
    def add_alert(self, a: dict) -> int:
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO alerts(ts, day, source, sym, kind, direction, severity, title, detail, price, data, option) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (a["ts"], a["day"], a.get("source"), a["sym"], a["kind"], a["direction"], a["severity"], a["title"],
                 a["detail"], a.get("price"), json.dumps(a.get("data") or {}, default=str),
                 json.dumps(a.get("option"), default=str) if a.get("option") else None))
            self.db.commit()
            return cur.lastrowid

    def set_alert_story(self, alert_id: int, story: int):
        with self.lock:
            self.db.execute("UPDATE alerts SET story=? WHERE id=?", (story, alert_id))
            self.db.commit()

    def set_alert_ai(self, alert_id: int, text: str):
        with self.lock:
            self.db.execute("UPDATE alerts SET ai=? WHERE id=?", (text, alert_id))
            self.db.commit()

    def _alert(self, r):
        d = dict(r)
        d["data"] = json.loads(d["data"]) if d.get("data") else {}
        d["option"] = json.loads(d["option"]) if d.get("option") else None
        return d

    def alerts(self, day=None, sym=None, since_id=0, limit=300, min_sev=1, source=None):
        q, args = "SELECT * FROM alerts WHERE id > ? AND severity >= ?", [since_id, min_sev]
        if day:
            q += " AND day = ?"
            args.append(day)
        if sym:
            q += " AND sym = ?"
            args.append(sym)
        if source:
            q += " AND source = ?"
            args.append(source)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self.lock:
            rows = self.db.execute(q, args).fetchall()
        return [self._alert(r) for r in rows]

    def alert(self, alert_id):
        with self.lock:
            r = self.db.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
        return self._alert(r) if r else None

    def alert_days(self, limit=30):
        with self.lock:
            rows = self.db.execute("SELECT day, COUNT(*) n FROM alerts GROUP BY day ORDER BY day DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ---------------- paper trades
    def add_trade(self, t: dict) -> int:
        cols = [c for c in t if c != "id"]
        with self.lock:
            cur = self.db.execute(f"INSERT INTO paper({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                  [t[c] for c in cols])
            self.db.commit()
            return cur.lastrowid

    def update_trade(self, tid: int, **f):
        with self.lock:
            self.db.execute(f"UPDATE paper SET {', '.join(k + '=?' for k in f)} WHERE id=?", [*f.values(), tid])
            self.db.commit()

    def trades(self, status=None, limit=500, source=None):
        q, args = "SELECT * FROM paper WHERE 1=1", []
        if status:
            q += " AND status=?"
            args.append(status)
        if source:
            q += " AND source=?"
            args.append(source)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self.lock:
            return [dict(r) for r in self.db.execute(q, args).fetchall()]

    def clear_source(self, source):
        """Remove simulator data so it never mixes with real results."""
        with self.lock:
            self.db.execute("DELETE FROM alerts WHERE source=?", (source,))
            self.db.execute("DELETE FROM paper WHERE source=?", (source,))
            self.db.commit()
