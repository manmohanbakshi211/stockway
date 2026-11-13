"""The journal: every paper trade with its full footprint, and every refused signal with its reason (SQLite, small
transactional state). Re-running the same history writes the same rows (keys make writes idempotent)."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
  account TEXT, setup TEXT, sym TEXT, day TEXT, d INTEGER, side TEXT, strike REAL,
  entry_t REAL, exit_t REAL, entry REAL, exit REAL, lots INTEGER, lot INTEGER, qty INTEGER,
  charges REAL, pnl_rs REAL, pct REAL, reason TEXT, mfe_pct REAL, mae_pct REAL, minutes REAL,
  und_entry REAL, und_exit REAL, state TEXT, kind TEXT, margin REAL,
  PRIMARY KEY (account, setup, sym, day, d, entry_t));
CREATE TABLE IF NOT EXISTS blocked (
  account TEXT, setup TEXT, sym TEXT, day TEXT, d INTEGER, t REAL, reason TEXT,
  PRIMARY KEY (account, setup, sym, day, d, t));
CREATE INDEX IF NOT EXISTS trades_day ON trades(day);
"""
COLS = ["account", "setup", "sym", "day", "d", "side", "strike", "entry_t", "exit_t", "entry", "exit", "lots", "lot",
        "qty", "charges", "pnl_rs", "pct", "reason", "mfe_pct", "mae_pct", "minutes", "und_entry", "und_exit", "state",
        "kind", "margin"]


class Journal:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.executescript(SCHEMA)
        have = {r[1] for r in self.db.execute("PRAGMA table_info(trades)")}
        for col, typ in (("kind", "TEXT"), ("margin", "REAL")):      # journals from before 0.12.0
            if col not in have:
                self.db.execute(f"ALTER TABLE trades ADD COLUMN {col} {typ}")
        self.db.commit()

    def trade(self, row: dict) -> None:
        r = dict(row)
        r["state"] = json.dumps(r.get("state") or {}, default=str)
        with self.lock:
            self.db.execute(f"INSERT OR REPLACE INTO trades ({','.join(COLS)}) VALUES ({','.join('?' * len(COLS))})",
                            [r.get(c) for c in COLS])
            self.db.commit()

    def blocked(self, account: str, setup: str, sym: str, day: str, d: int, t: float, reason: str) -> None:
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO blocked VALUES (?,?,?,?,?,?,?)", (account, setup, sym, day, d, t, reason))
            self.db.commit()

    def trades(self, day: str | None = None, account: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM trades WHERE 1=1", []
        if day:
            q, args = q + " AND day=?", args + [day]
        if account:
            q, args = q + " AND account=?", args + [account]
        with self.lock:
            cur = self.db.execute(q + " ORDER BY entry_t", args)
            names = [c[0] for c in cur.description]
            rows = [dict(zip(names, r)) for r in cur.fetchall()]
        for r in rows:
            r["state"] = json.loads(r["state"] or "{}")
            r["kind"] = r.get("kind") or "buy"
        return rows

    def blocked_rows(self, day: str | None = None) -> list[dict]:
        with self.lock:
            cur = self.db.execute("SELECT * FROM blocked" + (" WHERE day=?" if day else ""), [day] if day else [])
            names = [c[0] for c in cur.description]
            return [dict(zip(names, r)) for r in cur.fetchall()]

    def close(self) -> None:
        self.db.close()
