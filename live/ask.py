"""Ask Stockway: questions in plain English or Hinglish, answered from the app's own live data.

The answer comes from the AI you chose in Live setup (Claude through your Pro plan, the Claude API, or Gemini).
The AI sees a compact snapshot of what the app knows right now (indices, positioning, alerts, paper trades, the
stock you asked about, insider trades, world cues), never your keys, files or account. Stockway places no orders,
so the AI is told to talk about alerts and paper trades only.
"""
from __future__ import annotations

import re
import time

from .notify import _compact_json, _slim_metrics

ALIASES = {"BANK NIFTY": "BANKNIFTY", "NIFTY BANK": "BANKNIFTY", "FIN NIFTY": "FINNIFTY", "MIDCAP NIFTY": "MIDCPNIFTY",
           "NIFTY 50": "NIFTY", "NIFTY50": "NIFTY", "BNF": "BANKNIFTY"}
INDICES = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY")

RULES = ("You are Stockway's market analyst for an Indian F&O trader. Answer only from the DATA below, which is the app's "
         "live state right now. Use exact numbers and levels from it. If the data does not contain something, say so plainly "
         "instead of guessing. Reply in the language of the question (Hinglish if the question is in Hinglish). Keep it short "
         "and scannable: a direct answer first, then up to 5 bullets. Stockway never places real orders: when you suggest "
         "an action, phrase it as an alert to watch, a paper trade, or a Time Machine test, with entry, stop and target. "
         "Never promise returns; say what would prove the idea wrong.")


def symbols_in(question: str, known: set) -> list:
    q = " " + question.upper() + " "
    out = []
    for k, v in ALIASES.items():
        if k in q and v not in out:
            out.append(v)
            q = q.replace(k, " ")
    for w in re.findall(r"[A-Z][A-Z0-9&\-]{1,19}", q):
        if w in known and w not in out:
            out.append(w)
    if "NIFTY" in q.split() and "NIFTY" not in out:
        out.append("NIFTY")
    return out[:3]


def context(e, question: str) -> dict:
    known = set(e.metrics or {})
    if e.uni:
        known |= set(e.uni.underlyings)
    syms = symbols_in(question, known)
    now = e.clock.now() if getattr(e, "clock", None) else None
    d = {"time_ist": now.strftime("%a %d %b %Y %H:%M") if now else None,
         "scanner": {"running": bool(e.running), "source": e.source, "status": (e.status or {}).get("message")}}
    try:
        d["indices"] = [_slim_metrics(m) for m in e._indices()] if e.running else None
        d["vix"] = e._vix() if e.running else None
        d["day_type"] = {k: (v or {}).get("label") for k, v in (e.regimes or {}).items() if k in INDICES}
        d["breadth"] = (e.market or {}).get("breadth")
        d["gamma"] = {s: {k: g.get(k) for k in ("net_cr", "flip", "regime", "call_wall", "put_wall")} for s, g in (e.gex or {}).items()}
        d["top_movers"] = [{k: m.get(k) for k in ("sym", "chg_pct", "rel_vol", "score")} for m in e._movers()["hot"][:8]] if e.running else None
    except Exception:
        pass
    try:
        cv = e.context.view()
        d["positioning"] = {"participants": cv.get("participants"), "cash_flows": cv.get("fii_dii"), "fo_ban": cv.get("ban"),
                            "near_ban": [x["sym"] for x in (cv.get("near_ban") or [])[:8]]}
    except Exception:
        pass
    try:
        d["world"] = e.world.pressure()
        d["events_next_2h"] = e.world.upcoming(120)
    except Exception:
        pass
    d["recent_alerts"] = [{"t": time.strftime("%H:%M", time.localtime(a.get("ts") or 0)), "sym": a["sym"], "title": a["title"],
                           "score": (a.get("data") or {}).get("score"), "risk": ((a.get("data") or {}).get("risk") or {}).get("verdict")}
                          for a in list(e.alerts_mem)[:15]]
    try:
        p = e.paper.summary(e.state) if e.paper else None
        if p:
            d["paper"] = {"closed_trades": p.get("n"), "net_after_charges": p.get("net"), "win_rate_pct": p.get("win_rate"),
                          "open": [{k: t.get(k) for k in ("label", "entry", "last", "unreal")} for t in (p.get("open") or [])[:6]],
                          "unrealised": p.get("unreal"), "today": {k: (p.get("guard") or {}).get(k) for k in ("booked_today", "trades_today", "blocked")}}
    except Exception:
        pass
    try:
        b = e.brain.latest()
        if b:
            d["brain_note"] = {k: b.get(k) for k in ("headline", "bias", "confidence", "plan", "risks", "levels") if b.get(k)}
    except Exception:
        pass
    focus = {}
    for s in syms:
        m = e.metrics.get(s) if e.metrics else None
        f = {"metrics": _slim_metrics(m) if m else None,
             "alerts_today": [a["title"] for a in list(e.alerts_mem) if a["sym"] == s][:6],
             "news": [n["title"] for n in (e.news.items if e.news else []) if s in n.get("syms", [])][:5]}
        try:
            f["insider_trades"] = [{k: x.get(k) for k in ("filed", "person", "category", "side", "value", "mode")}
                                   for x in e.insiders.for_symbol(s, 180)[:5]]
        except Exception:
            pass
        focus[s] = f
    if focus:
        d["asked_about"] = focus
    return d


def answer(e, question: str, history: list | None = None) -> dict:
    question = (question or "").strip()[:800]
    if not question:
        raise ValueError("Type a question.")
    if not e.ai.enabled():
        raise PermissionError("Choose an AI in Live setup → AI first (your Claude Pro plan works there).")
    ctx = context(e, question)
    past = "".join(f"\nEarlier Q: {h.get('q', '')[:300]}\nEarlier A: {h.get('a', '')[:600]}" for h in (history or [])[-3:])
    prompt = f"{RULES}\n\nDATA:\n{_compact_json(ctx)}\n{past}\n\nQUESTION: {question}"
    t0 = time.time()
    text = e.ai.ask(prompt, 700, auto=False)
    if not text:
        raise RuntimeError(e.ai.last_error or "The AI did not answer. Try again in a minute.")
    return {"answer": text, "by": e.ai.label(), "secs": round(time.time() - t0, 1),
            "used": sorted((ctx.get("asked_about") or {}).keys())}
