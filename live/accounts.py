"""Capital accounts (gun): what real-sized paper accounts would have made from the test bench's trades.

For each capital level (rules/capital.json "test_accounts", bullets) and each way of trading (only buying, buying +
selling, only selling) a virtual account replays the bench's closed trades in time order and follows its tier's rules
(rules/capital.json tiers): risk per trade, most lots, most open positions, most trades a day, the daily loss stop and
the profit lock, one position per symbol, and only what its money can carry (premium, or premium + margin for sold
options). Profit and loss change its capital, so lots grow and shrink with it.

Two ways of choosing among the many signals:
- "all":    every signal that fits, first come, in the family's plain style (buy ATM / debit spread / sell ATM).
- "proven": only strategies (signal type x style) whose record on EARLIER days is positive after costs and its
            uncertainty (at least proven_min_n trades): a walk-forward test of following the Strategy board. Each day
            only uses what was known before it opened.

Nothing here trades: it reads the bench's paper record (data/bench.db, its own read-only connection, so it never
blocks the live engine). Lots are scaled from 1-lot results (the flat brokerage is counted once per lot, so multi-lot
results are slightly pessimistic).
"""
from __future__ import annotations

import math
import threading
import time

from . import capital as C
from .bench import DEFAULT_STYLE, FAMILIES, FIT, day_groups, db_for, style_family

POLICIES = {"all": "Every signal that fits, first come", "proven": "Only strategies proven on earlier days"}
_CACHE: dict = {}
_LOCK = threading.Lock()
_BUSY: set = set()
COLS = ("id", "ts", "day", "sym", "kind", "variant", "name", "style", "family", "sig", "exit_ts", "net", "cost_lot", "risk_lot", "need_lot")


def settings() -> dict:
    t = (C._cfg().get("test_accounts") or {})
    return {"levels": [float(x) for x in (t.get("levels") or [100000, 200000, 500000, 1000000])],
            "proven_min_n": int(t.get("proven_min_n") or 20), "days": int(t.get("days") or 60)}


def _prep(r: dict) -> dict:
    r["variant"] = r.get("variant") or ""
    r["family"] = style_family(r["style"], r.get("family"))
    r["sig"] = r.get("sig") or f"{int(r['ts'])}|{r['sym']}|{r['kind']}|{r['variant']}"
    r["key"] = (r["kind"], r["variant"], r["style"])
    return r


def load_rows(store, source, days: int = 60, styles: list | None = None, keys: set | None = None) -> list:
    """Closed bench trades, oldest first; only `styles` and / or strategy `keys` when given."""
    import datetime as _dt
    first = (_dt.date.today() - _dt.timedelta(days=days)).isoformat()
    bdb = db_for(store)
    con = bdb.reader()
    try:
        q = (f"SELECT {', '.join('t.' + c for c in COLS)} FROM bench_trades t WHERE t.source=? AND t.net IS NOT NULL "
             f"AND t.exit_ts IS NOT NULL AND t.day >= ?")
        args = [source, first]
        conds = []
        if styles:
            conds.append(f"t.style IN ({','.join('?' * len(styles))})")
            args += list(styles)
        if keys:                                  # strategies proven on some day: matched in SQL (a temporary table)
            con.execute("CREATE TEMP TABLE IF NOT EXISTS pk (kind TEXT, variant TEXT, style TEXT, PRIMARY KEY (kind, variant, style))")
            con.executemany("INSERT OR IGNORE INTO pk VALUES (?,?,?)", sorted(keys))
            conds.append("EXISTS (SELECT 1 FROM pk WHERE pk.kind = t.kind AND pk.variant = IFNULL(t.variant, '') AND pk.style = t.style)")
        if styles is not None or keys is not None:
            if not conds:
                return []
            q += " AND (" + " OR ".join(conds) + ")"
        return [_prep(dict(r)) for r in con.execute(q + " ORDER BY t.ts, t.id", args).fetchall()]
    except Exception:
        return []
    finally:
        con.close()


def proven_by_day(rows_or_groups: list, min_n: int) -> dict:
    """{day: {(kind, variant, style): rank}} using only trades from days before it (walk-forward).
    Accepts trade rows (with net) or bench.day_groups() rows (with n, s, s2)."""
    acc: dict = {}
    by_day: dict = {}
    for r in rows_or_groups:
        k = (r["kind"], r.get("variant") or "", r["style"])
        if "n" in r and "s" in r:
            add = (r["n"], r["s"] or 0.0, r["s2"] or 0.0)
        else:
            add = (1, r["net"], r["net"] ** 2)
        d = by_day.setdefault(r["day"], {})
        n, s, s2 = d.get(k, (0, 0.0, 0.0))
        d[k] = (n + add[0], s + add[1], s2 + add[2])
    out = {}
    for day in sorted(by_day):
        good = {}
        for k, (n, s, s2) in acc.items():
            if n >= min_n:
                avg = s / n
                sd = math.sqrt(max(0.0, (s2 - n * avg * avg) / (n - 1))) if n > 1 else abs(avg)
                rank = avg - sd / math.sqrt(n)
                if rank > 0:
                    good[k] = rank
        out[day] = good
        for k, (n, s, s2) in by_day[day].items():
            a = acc.get(k, (0, 0.0, 0.0))
            acc[k] = (a[0] + n, a[1] + s, a[2] + s2)
    return out


def candidates(rows: list, family: str, policy: str = "all", proven: dict | None = None) -> dict:
    """{day: [trade, ...]} one trade per signal (the best proven style of the family, or its plain style), oldest first."""
    cands: dict = {}
    for r in rows:
        if r["family"] != family:
            continue
        if policy == "all":
            if r["style"] != DEFAULT_STYLE.get(family):
                continue
            rank = 0.0
        else:
            rank = ((proven or {}).get(r["day"]) or {}).get(r["key"])
            if rank is None:
                continue
        best = cands.get(r["sig"])
        if best is None or rank > best[0]:
            cands[r["sig"]] = (rank, r)
    by_day: dict = {}
    for _, r in sorted(cands.values(), key=lambda x: (x[1]["ts"], x[1]["id"])):
        by_day.setdefault(r["day"], []).append(r)
    return by_day


def run(rows, cap0: float, family: str, policy: str = "all", proven: dict | None = None, by_day: dict | None = None) -> dict:
    """Replay one account. rows: load_rows() output (any order), or pass by_day from candidates()."""
    t = C.tier(cap0)
    risk_pct, max_lots = float(t["risk_pct"]), int(t["max_lots"])
    max_open, max_day = int(t["max_open"]), int(t["max_trades_day"])
    loss_pct, lock_pct = float(t.get("max_daily_loss_pct") or 0), float(t.get("profit_lock_pct") or 0)
    share = FIT["max_lot_share"] if family == "buy" else FIT["max_margin_share"]
    if by_day is None:
        by_day = candidates(rows, family, policy, proven)
    eq = peak = cap0
    max_dd = 0.0
    trades, days = [], {}
    n_cands = 0
    for day in sorted(by_day):
        start = eq
        realized, n_day, locked = 0.0, 0, False
        opened: list = []                    # [exit_ts, pnl, money, sym]
        n_cands += len(by_day[day])

        def close_until(ts):
            nonlocal eq, peak, max_dd, realized
            for p in sorted([p for p in opened if p[0] <= ts], key=lambda p: p[0]):
                opened.remove(p)
                eq += p[1]
                realized += p[1]
                peak = max(peak, eq)
                max_dd = max(max_dd, (peak - eq) / peak * 100 if peak > 0 else 0)
        for r in by_day[day]:
            if n_day >= max_day or locked:
                break                            # the day is done for this account
            close_until(r["ts"])
            if (loss_pct and realized <= -start * loss_pct / 100) or (lock_pct and realized >= start * lock_pct / 100):
                locked = True
                break
            if len(opened) >= max_open or r["sym"] in {p[3] for p in opened}:
                continue
            per_lot = r["need_lot"] if (family != "buy" and r.get("need_lot")) else r["cost_lot"]
            risk = r["risk_lot"] or 0
            if not per_lot or per_lot <= 0 or eq <= 0:
                continue
            free = eq - sum(p[2] for p in opened)
            lots = min(max_lots, int(eq * risk_pct / 100 // risk) if risk > 0 else max_lots, int(free * share // per_lot))
            if lots < 1:
                continue
            pnl = r["net"] * lots
            opened.append([r["exit_ts"], pnl, per_lot * lots, r["sym"]])
            n_day += 1
            trades.append({"ts": r["ts"], "day": day, "sym": r["sym"], "kind": r["kind"], "variant": r["variant"],
                           "name": r.get("name"), "style": r["style"], "lots": lots, "net": round(pnl, 2)})
        close_until(float("inf"))
        days[day] = {"day": day, "pnl": round(eq - start), "equity": round(eq), "trades": n_day, "locked": locked}
    nets = [x["net"] for x in trades]
    wins = [x for x in nets if x > 0]
    by_kind: dict = {}
    for x in trades:
        k = x["name"] or (x["kind"] + (f" {x['variant']}" if x["variant"] else ""))
        g = by_kind.setdefault(k, {"label": k, "n": 0, "net": 0.0})
        g["n"] += 1
        g["net"] += x["net"]
    top = sorted(by_kind.values(), key=lambda g: -g["net"])
    dv = list(days.values())
    return {"capital": cap0, "family": family, "family_label": FAMILIES.get(family, family), "policy": policy,
            "policy_label": POLICIES.get(policy, policy), "tier": t.get("name"), "end": round(eq), "net": round(eq - cap0),
            "return_pct": round((eq / cap0 - 1) * 100, 1) if cap0 else None, "n": len(trades),
            "win_pct": round(len(wins) / len(nets) * 100) if nets else None, "avg": round(sum(nets) / len(nets)) if nets else None,
            "max_dd_pct": round(max_dd, 1), "days": len(dv), "green_days": sum(1 for d in dv if d["pnl"] > 0),
            "worst_day": min((d["pnl"] for d in dv), default=None), "best_day": max((d["pnl"] for d in dv), default=None),
            "curve": dv[-60:], "best_kinds": [dict(g, net=round(g["net"])) for g in top[:3] if g["net"] > 0],
            "worst_kinds": [dict(g, net=round(g["net"])) for g in top[::-1][:3] if g["net"] < 0],
            "candidates": n_cands}


def _compute(store, source, levels, days) -> dict:
    st = settings()
    t0 = time.time()
    bdb = db_for(store)
    groups = day_groups(bdb, source, days)
    proven = proven_by_day(groups, st["proven_min_n"])
    keys = set().union(*proven.values()) if proven else set()
    rows = load_rows(store, source, days, styles=sorted(set(DEFAULT_STYLE.values())), keys=keys)
    cells = []
    for fam in FAMILIES:
        plain = candidates(rows, fam, "all")
        picked = candidates(rows, fam, "proven", proven)
        for lv in levels:
            cells.append(run(None, lv, fam, "all", by_day=plain))
            cells.append(run(None, lv, fam, "proven", by_day=picked))
    cells.sort(key=lambda c: (levels.index(c["capital"]), list(FAMILIES).index(c["family"]), list(POLICIES).index(c["policy"])))
    today = max(proven) if proven else None
    return {"levels": levels, "families": [{"family": f, "label": l} for f, l in FAMILIES.items()],
            "policies": [{"policy": p, "label": l} for p, l in POLICIES.items()], "cells": cells,
            "n_trades": sum(g["n"] for g in groups), "days": days, "proven_min_n": st["proven_min_n"],
            "proven_today": len((proven or {}).get(today) or {}) if today else 0, "secs": round(time.time() - t0, 2),
            "at": time.time(),
            "note": "Virtual paper accounts replaying the test bench's trades with each capital's own rules (risk per trade, "
                    "lots, open positions, trades a day, daily loss stop, profit lock, money for premium and margin). "
                    "'Proven' accounts only take strategies that were positive after costs on earlier days: an honest "
                    "walk-forward test. Paper only; margin for sold options is an estimate."}


def grid(store, source, levels: list | None = None, days: int | None = None, max_age: float = 300.0, wait: bool = True) -> dict:
    """Every capital level x way of trading x choosing rule. Cached; a stale result is returned at once and refreshed
    in the background (the first one is computed while you wait, unless wait=False)."""
    st = settings()
    levels = [float(x) for x in (levels or st["levels"])]
    days = int(days or st["days"])
    key = (str(db_for(store).path), source, tuple(levels), days)
    with _LOCK:
        hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < max_age:
        return hit[1]

    def work():
        try:
            out = _compute(store, source, levels, days)
            with _LOCK:
                if len(_CACHE) > 20:
                    _CACHE.clear()
                _CACHE[key] = (time.time(), out)
            return out
        finally:
            with _LOCK:
                _BUSY.discard(key)
    with _LOCK:
        busy = key in _BUSY
        if not busy:
            _BUSY.add(key)
    if hit or not wait:
        if not busy:
            threading.Thread(target=work, daemon=True, name="capital-accounts").start()
        return dict(hit[1], refreshing=True) if hit else {"levels": levels, "cells": [], "refreshing": True, "days": days}
    if busy:
        for _ in range(600):
            time.sleep(0.1)
            with _LOCK:
                if key not in _BUSY:
                    break
        with _LOCK:
            hit = _CACHE.get(key)
        return hit[1] if hit else {"levels": levels, "cells": [], "refreshing": True, "days": days}
    return work()
