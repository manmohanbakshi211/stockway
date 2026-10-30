"""Watchlist: symbols you add by hand plus smart picks the system finds, with everything worth seeing on one row.

Smart picks (refreshed with every view, never more than 8): symbols with a Focus alert in the last 90 minutes, then
the most active F&O stocks right now (move x volume pace), skipping ones you already watch. Each pick says why.
Market data only; trades from here are paper trades.
"""
from __future__ import annotations

import time

KEY = "watchlist:manual"
MAX_MANUAL = 40


def manual(store) -> list:
    return list(store.get(KEY, []) or [])


def change(store, add: str | None = None, remove: str | None = None) -> list:
    cur = manual(store)
    if add:
        s = add.strip().upper()
        if s and s not in cur:
            cur = ([s] + cur)[:MAX_MANUAL]
    if remove:
        cur = [x for x in cur if x != remove.strip().upper()]
    store.put(KEY, cur)
    return cur


def _row(e, sym: str) -> dict:
    m = (e.metrics or {}).get(sym)
    if not m:
        return {"sym": sym, "live": False}
    x = e.slim(m)
    o = m.get("opt") or {}
    x.update(live=True, vwap=m.get("vwap"), support=o.get("support"), resistance=o.get("resistance"),
             max_pain=o.get("max_pain"), exp_move_pct=o.get("exp_move_pct"),
             regime=(m.get("regime") or {}).get("label"), reasons=(m.get("reasons") or [])[:3])
    al = [a for a in e.alerts_mem if a["sym"] == sym]
    if al:
        a = al[0]
        x["last_alert"] = {"id": a["id"], "ts": a["ts"], "title": a["title"], "direction": a["direction"],
                           "focus": bool((a.get("data") or {}).get("focus")), "risk": ((a.get("data") or {}).get("risk") or {}).get("verdict")}
    x["alerts_today"] = len(al)
    x["paper_open"] = any(t["sym"] == sym for t in (e.paper.open_trades() if e.paper else []))
    return x


def picks(e, exclude: set, n=8) -> list:
    out, seen = [], set(exclude)
    now = e.clock.now().timestamp() if getattr(e, "clock", None) else time.time()
    for a in e.alerts_mem:                                  # newest first
        if len(out) >= n:
            break
        d = a.get("data") or {}
        if a["sym"] in seen or not d.get("focus") or a["direction"] not in ("up", "down") or now - a["ts"] > 90 * 60:
            continue
        seen.add(a["sym"])
        out.append({"sym": a["sym"], "why": f"Focus alert {time.strftime('%H:%M', time.localtime(a['ts']))}: {a['title']}"})
    stocks = [m for m in (e.metrics or {}).values() if not m.get("is_index") and m["sym"] not in seen
              and m.get("chg_pct") is not None and (m.get("rel_vol") or 0) > 0]
    stocks.sort(key=lambda m: abs(m["chg_pct"]) * min(m.get("rel_vol") or 0, 5), reverse=True)
    for m in stocks[: max(0, n - len(out))]:
        out.append({"sym": m["sym"], "why": f"Active: {m['chg_pct']:+.2f}% today on {m.get('rel_vol') or 0:.1f}x normal volume"})
    return out


def view(e) -> dict:
    mine = manual(e.store)
    pk = picks(e, set(mine)) if e.metrics else []
    return {"running": bool(e.running), "manual": [_row(e, s) for s in mine],
            "picks": [{**_row(e, p["sym"]), "why": p["why"]} for p in pk], "updated": time.time()}
