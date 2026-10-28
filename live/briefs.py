"""P5: pre-market brief (8:45) and post-market review (15:40). Rule-based always; AI-written when a key is set."""
from __future__ import annotations

import datetime as dt
import json

from .clock import IST
from .notify import SYSTEM_STYLE, _compact_json


def _crore(x):
    return f"₹{x:,.0f} cr" if x is not None else "–"


def premarket_facts(ctx_view: dict, last_levels: dict) -> dict:
    return {"global": [{k: g[k] for k in ("name", "last", "chg_pct")} for g in ctx_view.get("global") or []],
            "fii_dii_cash": ctx_view.get("fii_dii"), "participants": (ctx_view.get("participants") or {}).get("rows"),
            "ban_list": ctx_view.get("ban"), "near_ban": [x["sym"] for x in ctx_view.get("near_ban") or []][:8],
            "filings": [{k: f.get(k) for k in ("sym", "label", "desc")} for f in (ctx_view.get("filings") or [])
                        if f.get("label")][:12],
            "big_deals": [{k: d[k] for k in ("sym", "client", "side", "value_cr")} for d in ctx_view.get("deals_fo") or []][:8],
            "yesterday": last_levels}


def premarket_rules(f: dict) -> list:
    out = []
    g = {x["name"]: x for x in f.get("global") or []}
    us = g.get("US S&P 500 fut")
    asia = [g[n] for n in ("Nikkei", "Hang Seng", "Kospi") if n in g]
    if us or asia:
        parts = []
        if us:
            parts.append(f"US futures {us['chg_pct']:+.2f}%")
        if asia:
            avg = sum(a["chg_pct"] for a in asia) / len(asia)
            parts.append(f"Asia {avg:+.2f}% on average")
        tone = (us["chg_pct"] if us else 0) + (sum(a["chg_pct"] for a in asia) / len(asia) if asia else 0)
        out.append(f"Global cues {'positive' if tone > 0.3 else 'negative' if tone < -0.3 else 'mixed'}: " + ", ".join(parts) + ".")
    cr = [g[n] for n in ("Brent crude", "USD/INR", "US VIX") if n in g]
    if cr:
        out.append("Watch: " + ", ".join(f"{x['name']} {x['chg_pct']:+.2f}%" for x in cr) + ".")
    fd = f.get("fii_dii_cash") or {}
    if fd.get("FII"):
        out.append(f"FIIs {'bought' if fd['FII']['net'] > 0 else 'sold'} {_crore(abs(fd['FII']['net']))} in cash, "
                   f"DIIs {'bought' if fd.get('DII', {}).get('net', 0) > 0 else 'sold'} {_crore(abs(fd.get('DII', {}).get('net', 0)))} ({fd['FII']['date']}).")
    for r in f.get("participants") or []:
        if r["who"] == "FII" and r.get("fut_long_pct") is not None:
            chg = r.get("fut_net_chg")
            mood = "heavily short" if r["fut_long_pct"] < 25 else "short-leaning" if r["fut_long_pct"] < 45 else \
                "long-leaning" if r["fut_long_pct"] < 65 else "heavily long"
            out.append(f"FII index futures {mood}: {r['fut_long_pct']:.0f}% long"
                       + (f", net {'added' if chg > 0 else 'cut'} {abs(chg):,.0f} contracts yesterday." if chg else "."))
    if f.get("ban_list"):
        out.append("In F&O ban today: " + ", ".join(f["ban_list"]) + ". Near ban: " + (", ".join(f.get("near_ban") or []) or "none") + ".")
    if f.get("filings"):
        out.append("Company news overnight: " + "; ".join(f"{x['sym']} {x['label'].lower()}" for x in f["filings"][:6]) + ".")
    for sym, lv in (f.get("yesterday") or {}).items():
        o = lv.get("opt") or {}
        if lv.get("spot"):
            f = lambda v: f"{v:,.0f}" if isinstance(v, (int, float)) else "–"
            pcr = f"{o['pcr']:.2f}" if isinstance(o.get("pcr"), (int, float)) else "–"
            out.append(f"{lv.get('name', sym)} last {lv['spot']:,.0f}. Option walls: support {f(o.get('support'))}, "
                       f"resistance {f(o.get('resistance'))}, max pain {f(o.get('max_pain'))}, PCR {pcr}.")
    return out or ["Not enough data yet for a pre-market brief."]


def postmarket_facts(indices: list, regime: dict, outs: list, paper: dict, movers: dict, alerts: list) -> dict:
    graded = [o for o in outs if o.get("result")]
    hits = sum(o["result"] == "hit" for o in graded)
    by = {}
    for o in graded:
        b = by.setdefault(o["kind"], [0, 0])
        b[0] += 1
        b[1] += o["result"] == "hit"
    return {"indices": [{k: m.get(k) for k in ("name", "spot", "chg_pct", "day_h", "day_l")} for m in indices],
            "regime": regime.get("label") if regime else None,
            "alerts": {"total": len(alerts), "graded": len(graded), "hits": hits,
                       "by_kind": {k: f"{v[1]}/{v[0]}" for k, v in by.items()}},
            "best": [{k: o.get(k) for k in ("sym", "kind", "direction", "result", "omax", "mfe")}
                     for o in sorted(graded, key=lambda o: -(o.get("omax") or o.get("mfe") or 0))[:3]],
            "paper": {k: paper.get(k) for k in ("n", "net", "win_rate", "costs")} if paper else None,
            "gainers": [m["sym"] for m in (movers or {}).get("gainers", [])[:5]],
            "losers": [m["sym"] for m in (movers or {}).get("losers", [])[:5]]}


def postmarket_rules(f: dict) -> list:
    out = []
    for m in f["indices"][:2]:
        if m.get("spot"):
            out.append(f"{m['name']} closed {m['spot']:,.0f} ({(m.get('chg_pct') or 0):+.2f}%), range {m.get('day_l') or 0:,.0f}–{m.get('day_h') or 0:,.0f}.")
    if f.get("regime"):
        out.append(f"Day type: {f['regime']}.")
    a = f["alerts"]
    if a["graded"]:
        out.append(f"Alerts: {a['total']} fired, {a['hits']} of {a['graded']} graded ones worked ({a['hits'] / a['graded'] * 100:.0f}%). "
                   + ", ".join(f"{k} {v}" for k, v in list(a["by_kind"].items())[:6]) + ".")
    p = f.get("paper")
    if p and p.get("n"):
        out.append(f"Paper trading: {p['n']} trades, net ₹{p['net']:,.0f} after ₹{p['costs']:,.0f} charges, win rate {p['win_rate']}%.")
    if f.get("gainers"):
        out.append("Top gainers: " + ", ".join(f["gainers"]) + ". Top losers: " + ", ".join(f["losers"]) + ".")
    return out


def ai_prompt(kind: str, facts: dict) -> str:
    if kind == "pre":
        task = ("Write a pre-market brief for an Indian F&O trader in 6 short bullet points: global mood, flows and "
                "positioning (FII futures/options, cash), stocks to watch today (filings, ban list, big deals), Nifty plan "
                "with levels, Bank Nifty plan with levels, and the one biggest risk today.")
    else:
        task = ("Write a post-market review for an Indian F&O trader in 5 short bullet points: what the market did and "
                "why, which alerts worked and which didn't, the lesson for tomorrow, levels to watch tomorrow, and paper "
                "trading result in one line.")
    return SYSTEM_STYLE + task + " Each bullet under 30 words.\n\n" + _compact_json(facts)


def make(kind: str, facts: dict, ai=None) -> dict:
    lines = premarket_rules(facts) if kind == "pre" else postmarket_rules(facts)
    text = ai.ask(ai_prompt(kind, facts), 500) if ai and ai.enabled() else None
    return {"kind": kind, "lines": lines, "ai": text, "ts": dt.datetime.now(IST).isoformat(),
            "facts": json.loads(json.dumps(facts, default=str))}


def telegram_text(b: dict) -> str:
    title = "🌅 Pre-market brief" if b["kind"] == "pre" else "🌇 Post-market review"
    body = b["ai"] or "\n".join("• " + l for l in b["lines"])
    import html as _h
    return f"<b>{title}</b>\n{_h.escape(body)}"
