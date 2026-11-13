"""The snapshot the AI is given: a compact JSON of Tanu's own data right now. Built only from Tanu's data folder (live
status, journal, research reports, market sources, alert book); it never opens the secrets folder, and keys named
like secrets are dropped as a second guard."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from ..detect.book import AlertBook
from ..intel import views
from ..intel.collect import Collector
from ..journal import review
from ..journal.store import Journal

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
ALIASES = {"BANK NIFTY": "BANKNIFTY", "NIFTY BANK": "BANKNIFTY", "BNF": "BANKNIFTY", "FIN NIFTY": "FINNIFTY",
           "NIFTY 50": "NIFTY", "NIFTY50": "NIFTY", "MIDCAP NIFTY": "MIDCPNIFTY"}
SECRETISH = re.compile(r"token|secret|api_?key|password|pin|totp|chat_id", re.I)
MAX_CHARS = 14000

RULES = ("You are Tanu's market desk analyst for Mandeep, an Indian F&O trader. Answer only from the DATA below, which "
         "is Tanu's own data right now. Use exact numbers from it; if the data does not hold something, say so plainly "
         "instead of guessing. Reply in the language of the question (Hinglish if the question is in Hinglish). Keep it "
         "short and scannable: the direct answer first, then at most 5 bullets. Tanu is paper only and never places "
         "real orders: talk about alerts, paper trades and tests, and never tell him to buy or sell with real money. "
         "When you raise an idea, say how Tanu would test it (entry, stop, target, on days it never saw, after costs) "
         "and what would prove it wrong. Never promise returns. Results are in rupees and % per trade after costs.")


def _clean(x):
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items() if not SECRETISH.search(str(k))}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    if isinstance(x, float):
        return round(x, 2)
    return x


def _json(p: Path, default=None):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def symbols_in(text: str, known: set[str]) -> list[str]:
    q = " " + text.upper() + " "
    out = []
    for k, v in ALIASES.items():
        if k in q and v not in out:
            out.append(v)
            q = q.replace(k, " ")
    for w in re.findall(r"[A-Z][A-Z0-9&\-]{1,19}", q):
        if w in known and w not in out:
            out.append(w)
    return out[:3]


def research_summary(base: Path) -> dict:
    r = base / "research"
    runs = sorted(p for p in r.glob("*") if p.is_dir() and (p / "report.json").exists()) if r.exists() else []
    best: dict[str, dict] = {}
    structs: dict[str, dict] = {}
    for p in runs:
        rep = _json(p / "report.json", {}) or {}
        for s in rep.get("setups", []):
            row = {"id": s["setup"], "passes": s.get("passes"), "unseen_avg_pct": (s.get("hold") or {}).get("avg_pct"),
                   "unseen_trades": (s.get("hold") or {}).get("n"), "rs_per_trade": s.get("rs_per_trade"),
                   "why_not": (s.get("why_not") or [])[:2], "run": p.name}
            (structs if p.name.startswith("structures_") else best)[s["setup"]] = row
    rows = sorted(best.values(), key=lambda x: -(x["unseen_avg_pct"] if x["unseen_avg_pct"] is not None else -1e9))
    return {"setups_tested": len(rows), "passing": [x["id"] for x in rows if x["passes"]], "best_on_unseen_days": rows[:6],
            "structures": list(structs.values())[:6]}


def paper_summary(base: Path) -> dict | None:
    jp = base / "store" / "journal.db"
    if not jp.exists():
        return None
    j = Journal(jp)
    days = sorted({t["day"] for t in j.trades()})
    if not days:
        return None
    rev = review.daily(j, days[-1])
    return {"day": days[-1], "accounts": {a: x["summary"] for a, x in rev["accounts"].items()},
            "hypotheses": rev.get("hypotheses", [])[:4]}


def snapshot(base: Path | str, collector: Collector, question: str = "", book: AlertBook | None = None,
             now: float | None = None) -> dict:
    base = Path(base)
    t = dt.datetime.fromtimestamp(now if now is not None else collector.now(), IST)
    live = _json(base / "live" / "status.json", None)
    ctx = views.context(collector, live)
    poi = (ctx.get("participant_oi") or {}).get("rows") or []
    d = {"time_ist": t.strftime("%a %d %b %Y %H:%M"),
         "live_day": {k: live.get(k) for k in ("day", "minute", "of", "done", "spots")} | {
             "signals": (live.get("signals") or [])[-10:], "open_paper_trades": live.get("open", [])}
         if live else "not live (before the switch-over, or no market today)",
         "market": {"gift_nifty": ctx["gift_nifty"], "world_cues": ctx["cues"], "fii_dii_cash_cr": ctx["fii_dii"],
                    "index_positions": [r for r in poi if r["who"] in ("FII", "Pro", "Client")],
                    "fo_ban": ctx["ban"], "results_this_week": ctx["results_week"][:12],
                    "high_impact_events_24h": ctx["calendar_soon"][:6],
                    "important_filings": [{k: x.get(k) for k in ("sym", "label", "desc", "time")} for x in ctx["filings"][:6]]},
         "paper": paper_summary(base), "research": research_summary(base)}
    if book is not None:
        v = book.view(min_sev=2, limit=15)
        d["alerts_today"] = {"day": v["day"], "counts": v["counts"]["by_kind"],
                             "latest": [{k: a[k] for k in ("at", "sym", "title", "severity")} for a in v["alerts"]]}
    known = set(collector.fo_symbols()) | set(collector.watch_symbols()) | {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}
    syms = symbols_in(question, known)
    if syms:
        w = views.watchlist(collector)
        rows = {r["sym"]: r for r in w["rows"]}
        ins = [x for x in views.insiders(collector)["insiders"] if x["sym"] in syms][:6]
        d["asked_about"] = {s: {k: v for k, v in (rows.get(s) or {"sym": s, "note": "not in the watchlist: no quote"}).items()
                                if k != "line"} for s in syms}
        d["asked_about_insiders"] = [{k: x.get(k) for k in ("sym", "side", "value", "person", "category", "traded")} for x in ins]
    out = _clean(d)
    s = json.dumps(out, default=str, separators=(",", ":"))
    while len(s) > MAX_CHARS and out["market"]["results_this_week"]:
        out["market"]["results_this_week"] = out["market"]["results_this_week"][:-3]
        s = json.dumps(out, default=str, separators=(",", ":"))
    return out


def ask_prompt(question: str, snap: dict) -> str:
    return (RULES + "\n\nDATA (JSON):\n" + json.dumps(snap, default=str, separators=(",", ":"))[:MAX_CHARS * 2]
            + "\n\nQUESTION:\n" + question.strip()[:1000])
