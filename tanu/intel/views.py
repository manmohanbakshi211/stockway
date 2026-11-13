"""What each tab of the Market page shows, composed from the cached sources (pure: reads caches, never fetches)."""
from __future__ import annotations

import datetime as dt
import re
import time
from collections import defaultdict

from . import parsers as P
from .collect import IST, Collector


def _status(c: Collector, ids) -> dict:
    out = {}
    now = c.now()
    for sid in ids:
        x = c.cached(sid)
        every = c.spec["sources"].get(sid, {}).get("every_s", 600)
        out[sid] = {"name": c.spec["sources"].get(sid, {}).get("name", sid), "ok": x.get("ok"),
                    "at": x.get("at"), "age_s": round(now - x["at"]) if x.get("at") else None,
                    "stale": bool(x.get("at")) and now - x["at"] > 3 * every, "error": x.get("error"),
                    "busy": sid in c.busy}
    return out


def _data(c: Collector, sid: str) -> dict:
    return c.cached(sid).get("data") or {}


def _world_rows(c: Collector) -> dict:
    return {r["ticker"]: r for g in _data(c, "world").get("groups", []) for r in g["rows"]}


def _today(c: Collector) -> dt.date:
    return dt.datetime.fromtimestamp(c.now(), IST).date()


def context(c: Collector, live: dict | None = None) -> dict:
    rows = _world_rows(c)
    cues = [{k: r.get(k) for k in ("name", "ticker", "last", "chg_pct", "stale")}
            for t in c.world_spec.get("context_cues", []) if (r := rows.get(t))]
    today = _today(c)
    week = (today + dt.timedelta(days=7)).isoformat()
    fo = c.fo_symbols()
    ev = [e for e in _data(c, "events").get("events", []) if today.isoformat() <= e["date"] <= week and e["results"]
          and (not fo or e["sym"] in fo)]
    soon = []
    for e in _data(c, "calendar").get("events", []):
        try:
            mins = (dt.datetime.fromisoformat(e["time"]) - dt.datetime.fromtimestamp(c.now(), IST)).total_seconds() / 60
        except ValueError:
            continue
        if -60 <= mins <= 24 * 60 and e["impact"] == "High":
            soon.append({**e, "mins": round(mins)})
    ban = _data(c, "ban")
    return {"gift_nifty": _data(c, "gift_nifty") or None, "cues": cues, "fii_dii": _data(c, "fii_dii") or None,
            "participant_oi": _data(c, "participant_oi") or None,
            "ban": {"date": ban.get("date"), "ban": ban.get("ban", []), "near": ban.get("near", [])[:15]} if ban else None,
            "results_week": ev[:40], "calendar_soon": soon[:12],
            "filings": [x for x in _data(c, "filings").get("items", []) if x["severity"] >= 2][:15],
            "live_spots": (live or {}).get("spots") if live else None}


def world(c: Collector) -> dict:
    w = _data(c, "world")
    return {"groups": w.get("groups", []), "adr": w.get("adr", []), "answered": w.get("answered"), "of": w.get("of"),
            "calendar": _data(c, "calendar").get("events", [])[:60],
            "news": _data(c, "news_global").get("items", [])[:40]}


def insiders(c: Collector, days: int = 30) -> dict:
    ins = _data(c, "insiders")
    lim = ins.get("show_min_rs", 5e6)
    since = (_today(c) - dt.timedelta(days=days)).isoformat()
    rows = [x for x in ins.get("rows", []) if P.meaningful_insider(x, lim) and (x.get("traded") or x.get("filed") or "") >= since]
    net = defaultdict(lambda: {"buy": 0.0, "sell": 0.0, "n": 0})
    for x in rows:
        n = net[x["sym"]]
        n["buy" if x["side"] == "Buy" else "sell"] += x["value"]
        n["n"] += 1
    top = sorted(({"sym": s, "net_cr": round((v["buy"] - v["sell"]) / 1e7, 2), "buy_cr": round(v["buy"] / 1e7, 2),
                   "sell_cr": round(v["sell"] / 1e7, 2), "trades": v["n"]} for s, v in net.items()),
                 key=lambda r: -abs(r["net_cr"]))
    return {"fii_dii": _data(c, "fii_dii") or None, "participant_oi": _data(c, "participant_oi") or None,
            "deals": _data(c, "deals").get("deals", [])[:60],
            "insiders": rows[:150], "insider_net": top[:15], "insider_days": days, "insider_left": ins.get("left", 0)}


def watchlist(c: Collector) -> dict:
    syms = c.watch_symbols()
    quotes = _data(c, "watchlist").get("quotes", {})
    ban = _data(c, "ban").get("symbols", {})
    today = _today(c).isoformat()
    ev = defaultdict(list)
    for e in _data(c, "events").get("events", []):
        if e["date"] >= today:
            ev[e["sym"]].append(e)
    since = (_today(c) - dt.timedelta(days=30)).isoformat()
    ins = defaultdict(float)
    for x in _data(c, "insiders").get("rows", []):
        if P.meaningful_insider(x) and (x.get("traded") or "") >= since:
            ins[x["sym"]] += x["value"] if x["side"] == "Buy" else -x["value"]
    fil = defaultdict(list)
    for x in _data(c, "filings").get("items", []):
        fil[x["sym"]].append(x)
    news = _data(c, "news_india").get("items", [])
    out = []
    for s in syms:
        q = quotes.get(s) or {}
        word = re.compile(r"(?<![A-Za-z])" + re.escape(s) + r"(?![A-Za-z])", re.I)
        out.append({"sym": s, "last": q.get("last"), "chg_pct": q.get("chg_pct"), "day_h": q.get("day_h"),
                    "day_l": q.get("day_l"), "line": q.get("line", [])[-60:], "hi52": q.get("hi52"), "lo52": q.get("lo52"),
                    "mwpl_pct": (ban.get(s) or {}).get("pct"), "ban": (ban.get(s) or {}).get("ban", False),
                    "next_event": (ev.get(s) or [None])[0], "insider_net_cr": round(ins[s] / 1e7, 2) if s in ins else None,
                    "filing": (fil.get(s) or [None])[0],
                    "news": [n for n in news if word.search(n["title"])][:3]})
    return {"symbols": syms, "rows": out}


def news(c: Collector) -> dict:
    n = _data(c, "news_india")
    return {"items": n.get("items", [])[:120], "errors": n.get("errors", {}),
            "filings": _data(c, "filings").get("items", [])[:80]}


VIEWS = {"context": context, "world": world, "insiders": insiders, "watchlist": watchlist, "news": news}


def tab(c: Collector, name: str, live: dict | None = None, refresh: bool = True) -> dict:
    ids = c.spec["tabs"].get(name, [])
    started = c.refresh_async(ids) if refresh else []
    body = VIEWS[name](c, live) if name == "context" else VIEWS[name](c)
    return {"tab": name, "at": time.time(), "sources": _status(c, ids), "reading": started, **body}
