"""End-of-day report: what happened today in paper trading and alerts, and what to change tomorrow.

Built only from the app's own records (paper trades, alerts, their 60-minute grading), so it can be rebuilt for any
past day. It answers the questions a trading desk asks every evening:
  - P&L: net after charges, wins/losses, how trades exited, best and worst.
  - Discipline: trades taken against the risk check (overrides, or blocked ones taken by hand on older versions),
    entries outside the 09:20-14:45 window, and what they cost.
  - Alerts: how many, how many were in Focus, which types worked (decided win rate and option move after an hour),
    and whether Focus beat the full feed.
  - Lessons: short rules for tomorrow, from the numbers above.
Paper only: nothing here places an order.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from collections import defaultdict

from .clock import IST

WINDOW = ("09:20", "14:45")


def _rs(x) -> str:
    x = round(float(x or 0))
    return f"{'−' if x < 0 else ''}₹{abs(x):,}"


def _hm(ts) -> str:
    return dt.datetime.fromtimestamp(ts, IST).strftime("%H:%M") if ts else "–"


def _grade(rows: list) -> dict:
    h = sum(r["result"] == "hit" for r in rows)
    m = sum(r["result"] == "miss" for r in rows)
    o = [r["o60"] for r in rows if r.get("o60") is not None]
    return {"n": len(rows), "hits": h, "misses": m, "win_pct": round(h / (h + m) * 100) if h + m else None,
            "avg_o60": round(sum(o) / len(o), 2) if o else None}


def _focus(a: dict) -> bool:
    d = a.get("data") or {}
    if "focus" in d:
        return bool(d["focus"])
    return a["severity"] >= 3 and not d.get("muted")


def build(store, source: str, day: str, indices: list | None = None) -> dict:
    db = store.db
    with store.lock:
        paper = [dict(zip([c[0] for c in cur.description], r)) for cur in [db.execute(
            "SELECT * FROM paper WHERE day=? AND source=? ORDER BY entry_ts", (day, source))] for r in cur.fetchall()]
        alerts = [dict(zip([c[0] for c in cur.description], r)) for cur in [db.execute(
            "SELECT id, ts, sym, kind, direction, severity, data FROM alerts WHERE day=? AND source=?", (day, source))]
            for r in cur.fetchall()]
        try:
            outs = [dict(zip([c[0] for c in cur.description], r)) for cur in [db.execute(
                "SELECT alert_id, kind, result, o60 FROM outcomes WHERE day=? AND source=? AND result IS NOT NULL", (day, source))]
                for r in cur.fetchall()]
        except Exception:                 # no alert has been graded yet (table not created)
            outs = []
    for a in alerts:
        try:
            a["data"] = json.loads(a["data"] or "{}")
        except Exception:
            a["data"] = {}
    amap = {a["id"]: a for a in alerts}

    # ---- paper trades
    closed = [t for t in paper if t["status"] == "closed"]
    for t in closed:
        a = amap.get(t["alert_id"]) or {}
        v = ((a.get("data") or {}).get("risk") or {}).get("verdict")
        hm = _hm(t["entry_ts"])
        t["gate"] = "override" if t.get("manual") == 2 else ("blocked" if v and v != "PASS" else "pass")
        t["outside_window"] = not (WINDOW[0] <= hm < WINDOW[1])
        t["by"] = "auto" if not t.get("manual") else "you"
    net = round(sum(t["net"] or 0 for t in closed), 2)
    wins = [t for t in closed if (t["net"] or 0) > 0]
    agg = lambda key: [{"key": k, "n": len(v), "net": round(sum(x["net"] or 0 for x in v), 0)}
                       for k, v in sorted(_group(closed, key).items(), key=lambda kv: sum(x["net"] or 0 for x in kv[1]))]
    slim = lambda t: {"time": f"{_hm(t['entry_ts'])}–{_hm(t['exit_ts'])}", "label": t["label"], "kind": t["kind"],
                      "net": round(t["net"] or 0), "exit": t["reason"], "gate": t["gate"]}
    by_gate = {g: {"n": len(v), "net": round(sum(x["net"] or 0 for x in v), 0)} for g, v in _group(closed, "gate").items()}
    off_window = [t for t in closed if t["outside_window"]]
    p = {"n": len(closed), "open": len(paper) - len(closed), "wins": len(wins), "losses": len(closed) - len(wins),
         "net": net, "costs": round(sum(t["costs"] or 0 for t in closed), 0),
         "win_rate": round(len(wins) / len(closed) * 100) if closed else None,
         "gross_win": round(sum(t["net"] for t in wins), 0), "gross_loss": round(sum(t["net"] or 0 for t in closed if t not in wins), 0),
         "by_exit": agg("reason"), "by_kind": agg("kind"), "by_gate": by_gate,
         "by_who": {g: {"n": len(v), "net": round(sum(x["net"] or 0 for x in v), 0)} for g, v in _group(closed, "by").items()},
         "outside_window": {"n": len(off_window), "net": round(sum(t["net"] or 0 for t in off_window), 0)},
         "best": [slim(t) for t in sorted(closed, key=lambda t: -(t["net"] or 0))[:3]],
         "worst": [slim(t) for t in sorted(closed, key=lambda t: (t["net"] or 0))[:3]]}

    # ---- alerts and their grading
    og = {o["alert_id"]: o for o in outs}
    graded = [og[a["id"]] for a in alerts if a["id"] in og]
    focus_ids = {a["id"] for a in alerts if _focus(a)}
    kinds = []
    for k, rows in _group(outs, "kind").items():
        g = _grade(rows)
        if g["hits"] + g["misses"] >= 8:
            kinds.append({"kind": k, **g})
    kinds.sort(key=lambda r: ((r["win_pct"] or 0), r["avg_o60"] or 0), reverse=True)
    hours = defaultdict(int)
    for a in alerts:
        hours[_hm(a["ts"])[:2]] += 1
    al = {"total": len(alerts), "symbols": len({a["sym"] for a in alerts}), "focus": len(focus_ids),
          "per_hour": dict(sorted(hours.items())), "graded": _grade(graded),
          "focus_graded": _grade([og[i] for i in focus_ids if i in og]),
          "act_now": _grade([og[a["id"]] for a in alerts if a["severity"] >= 3 and a["id"] in og]),
          "best_kinds": kinds[:4], "worst_kinds": [k for k in kinds[::-1] if (k["win_pct"] or 0) < 50][:4]}

    rep = {"day": day, "source": source, "generated": dt.datetime.now(IST).isoformat(timespec="seconds"),
           "indices": [{k: m.get(k) for k in ("name", "spot", "chg_pct", "day_h", "day_l")} for m in (indices or [])][:4],
           "paper": p, "alerts": al}
    rep["lessons"] = lessons(rep)
    return rep


def _group(rows, key) -> dict:
    out = defaultdict(list)
    for r in rows:
        out[r.get(key)].append(r)
    return out


def lessons(r: dict) -> list:
    p, a = r["paper"], r["alerts"]
    out = []
    g = p["by_gate"]
    bad = {k: v for k, v in g.items() if k in ("blocked", "override")}
    if bad:
        n = sum(v["n"] for v in bad.values())
        net = sum(v["net"] for v in bad.values())
        ok = g.get("pass", {"n": 0, "net": 0})
        out.append(f"{n} paper trade{'s' if n > 1 else ''} went against the risk check: net {_rs(net)}. "
                   f"The {ok['n']} it passed: net {_rs(ok['net'])}. Take only PASS ideas.")
    if p["outside_window"]["n"]:
        out.append(f"{p['outside_window']['n']} trade(s) outside 09:20–14:45: net {_rs(p['outside_window']['net'])}. "
                   "Pre-open and late entries are now refused.")
    stops = next((x for x in p["by_exit"] if x["key"] == "stop loss"), None)
    if stops and p["net"] < 0 and stops["net"] <= p["net"]:
        out.append(f"Stop losses cost {_rs(-stops['net'])} on {stops['n']} trades: more than the whole day's loss. "
                   "Fewer, better entries matter more than exits.")
    if p["n"] >= 15:
        out.append(f"{p['n']} trades in a day is over-trading for one account; the guard allows fewer by design.")
    if a["total"] > 600:
        out.append(f"{a['total']:,} alerts on {a['symbols']} symbols is noise; watch Focus ({a['focus']} today).")
    fg, gg = a["focus_graded"], a["graded"]
    if fg.get("win_pct") is not None and gg.get("win_pct") is not None and (fg["hits"] + fg["misses"]) >= 10:
        better = (fg["avg_o60"] or 0) > (gg["avg_o60"] or 0)
        out.append(f"Focus alerts worked {fg['win_pct']}% (option {fg['avg_o60'] or 0:+.1f}% after an hour) vs all alerts "
                   f"{gg['win_pct']}% ({gg['avg_o60'] or 0:+.1f}%)" + (": stick to Focus." if better else ": no edge from Focus today."))
    if a["best_kinds"]:
        b = a["best_kinds"][0]
        out.append(f"Best alert type today: {b['kind'].replace('_', ' ')} ({b['win_pct']}% of {b['hits'] + b['misses']} decided). "
                   "One day is not evidence: the Scorecard decides after 20+ decided cases.")
    return out[:7]


def text(r: dict) -> list:
    """Short lines for the post-market brief and Telegram."""
    p, a = r["paper"], r["alerts"]
    out = []
    if p["n"]:
        out.append(f"Paper: {p['n']} trades, {p['wins']} won / {p['losses']} lost, net {_rs(p['net'])} after {_rs(p['costs'])} charges.")
    out.append(f"Alerts: {a['total']:,} ({a['focus']} in Focus); decided win rate all {a['graded']['win_pct'] or '–'}%, "
               f"Focus {a['focus_graded']['win_pct'] or '–'}%.")
    return out + r["lessons"][:3]
