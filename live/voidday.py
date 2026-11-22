"""Trades on a day NSE had no session are void (gun, 2.87).

Mandeep's status check (22 Nov 2026): 10 Nov 2026 was an NSE holiday, yet the main paper account shows trades that
day (-₹22,753, before 2.48 checked the NSE calendar). Those prices were stale, so the trades are not real results.

What this does, once at every start (idempotent): a paper trade of the real market whose day had no NSE session
(live/session.py: weekends, holidays, minus special sessions) gets status "void" and a note in its reason; a test-bench
trade of such a day moves to the source "<source>|void". Nothing is deleted. Every money figure counts only closed /
open trades of its own source, so void ones drop out of the accounts, the bench, the strict test and the multiverse.
Practice sources (sim, replay) are left alone: they are not the real market anyway.
Paper only: nothing here can place an order.
"""
from __future__ import annotations

import datetime as dt

from . import session as SESSION
from .practice import PRACTICE

NOTE = "void: no NSE session that day"


def no_session(day: str) -> bool:
    try:
        return SESSION.session(dt.date.fromisoformat(str(day)[:10])) is None
    except Exception:
        return False


def _practice(source: str | None) -> bool:
    return str(source or "").split("#")[0].split("|")[0] in PRACTICE


def run(store, log=print, bench_db=None) -> dict:
    out = {"paper": 0, "bench": 0, "days": []}
    with store.lock:
        rows = store.db.execute("SELECT id, day, source, reason FROM paper WHERE status IN ('open', 'closed')").fetchall()
    bad = {}
    for r in rows:
        d = r["day"]
        if d not in bad:
            bad[d] = no_session(d)
        if bad[d] and not _practice(r["source"]):
            store.update_trade(r["id"], status="void", reason=(f"{r['reason']} · " if r["reason"] else "") + NOTE)
            out["paper"] += 1
    if bench_db is not None:
        with bench_db.lock:
            pairs = bench_db.db.execute("SELECT DISTINCT source, day FROM bench_trades WHERE source NOT LIKE '%|void'").fetchall()
            for src, day in pairs:
                if _practice(src):
                    continue
                if day not in bad:
                    bad[day] = no_session(day)
                if bad[day]:
                    n = bench_db.db.execute("UPDATE bench_trades SET source=? WHERE source=? AND day=?",
                                            (f"{src}|void", src, day)).rowcount
                    out["bench"] += int(n or 0)
            bench_db.db.commit()
    out["days"] = sorted(d for d, v in bad.items() if v)
    if out["paper"] or out["bench"]:
        log(f"Void (no NSE session that day: {', '.join(out['days'])}): {out['paper']} paper trade(s) and "
            f"{out['bench']} test-bench trade(s) kept but no longer counted")
    return out
