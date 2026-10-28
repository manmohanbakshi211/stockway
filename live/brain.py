"""AI market brain: every few minutes it looks at everything the app knows and writes a desk note:
headline, bias with confidence, key levels, risks, a watchlist, and what changed since the last note.
Rule-based when no AI key is set (always works); AI-written when one is."""
from __future__ import annotations

import datetime as dt
import json
import re
import time

from .clock import IST
from .notify import SYSTEM_STYLE, _compact_json


def snapshot(e) -> dict:
    """Everything worth knowing right now, compact."""
    idx = {}
    for m in e._indices()[:4]:
        o, g = m.get("opt") or {}, m.get("gex") or {}
        ctx = e.charts.context(m["sym"], m) if getattr(e, "charts", None) else {}
        idx[m["sym"]] = {"spot": m["spot"], "chg_pct": m.get("chg_pct"), "vs_vwap_pct": m.get("vs_vwap_pct"), "score": m.get("score"),
                         "reasons": m.get("reasons"), "regime": (m.get("regime") or {}).get("label"), "pcr": o.get("pcr"),
                         "support": o.get("support"), "resistance": o.get("resistance"), "max_pain": o.get("max_pain"),
                         "atm_iv": o.get("atm_iv"), "iv_chg_15m": o.get("iv_chg_15m"), "expected_move_pct": o.get("daily_move_pct"),
                         "gamma": g.get("regime"), "gamma_flip": g.get("flip"), "pdh": ctx.get("pdh"), "pdl": ctx.get("pdl"),
                         "flow_5m": (m.get("flow") or {}).get("delta_pct_5m")}
    mk = e.market or {}
    cv = e.context.view()
    fii = next((r for r in ((cv.get("participants") or {}).get("rows") or []) if r["who"] == "FII"), None)
    w = e.world.view() if getattr(e, "world", None) else {}
    hot = [{"sym": m["sym"], "chg_pct": m.get("chg_pct"), "rel_vol": m.get("rel_vol"), "buildup": m.get("buildup"), "score": m.get("score")}
           for m in (e._movers().get("hot") or [])[:6]] if e.metrics else []
    recent = [{"t": dt.datetime.fromtimestamp(a["ts"], IST).strftime("%H:%M"), "sym": a["sym"], "title": a["title"],
               "score": (a.get("data") or {}).get("score")} for a in list(e.alerts_mem)[:15] if a["severity"] >= 2]
    desk = None
    try:
        dv = e.desk.view()
        desk = {"today_pnl": dv["today_pnl"], "open": [f"{t['family']} {t['sym']}" for t in dv["today"] if t["status"] == "open"],
                "halted": dv["halted"]}
    except Exception:
        pass
    return {"time": e.clock.now().strftime("%H:%M") if e.running else dt.datetime.now(IST).strftime("%H:%M"),
            "indices": idx, "vix": e._vix() if e.state else None, "breadth": mk.get("breadth"),
            "sectors_top": [(s["sector"], s["chg"]) for s in (mk.get("sectors") or [])[:3]],
            "sectors_bottom": [(s["sector"], s["chg"]) for s in (mk.get("sectors") or [])[-3:]],
            "fii_index_futures_long_pct": fii and fii.get("fut_long_pct"), "fii_dii_cash": cv.get("fii_dii"),
            "ban": cv.get("ban"), "global_pressure": w.get("pressure"),
            "global_moves": [(r["name"], round(r["chg_pct"], 2)) for g in w.get("groups", []) for r in g["rows"]
                             if r.get("chg_pct") is not None and abs(r["chg_pct"]) >= 1][:10],
            "events_next_2h": [(x["country"], x["title"], x["mins"]) for x in (w.get("upcoming") or []) if x["impact"] == "High"],
            "results_today": [r["sym"] for r in (w.get("results") or []) if r["date"] == dt.datetime.now(IST).date().isoformat()][:15],
            "india_news": [n["title"] for n in (w.get("news") or []) if n.get("impact") in ("high", "medium")][:6]
            + [n["title"] for n in e.news.items[:4]],
            "hot_stocks": hot, "recent_alerts": recent, "desk": desk}


def rules(s: dict, prev: dict | None) -> dict:
    n = s["indices"].get("NIFTY") or next(iter(s["indices"].values()), {}) if s["indices"] else {}
    pts, why = 0, []
    def add(cond, p, txt):
        nonlocal pts
        if cond:
            pts += p
            why.append(txt)
    sc = [v.get("score") or 0 for v in s["indices"].values()]
    avg = sum(sc) / len(sc) if sc else 0
    add(avg >= 25, 2, "index signals lean up"); add(avg <= -25, -2, "index signals lean down")
    b = s.get("breadth") or {}
    if b.get("n"):
        r = b["adv"] / max(1, b["adv"] + b["dec"])
        add(r >= 0.65, 1, f"broad buying ({b['adv']} up vs {b['dec']} down)"); add(r <= 0.35, -1, f"broad selling ({b['dec']} down vs {b['adv']} up)")
    gp = (s.get("global_pressure") or {}).get("implied_pct")
    add(gp is not None and gp >= 0.15, 1, "global markets supportive"); add(gp is not None and gp <= -0.15, -1, "global markets a drag")
    v = s.get("vix") or {}
    add((v.get("chg_pct") or 0) >= 5, -1, "VIX jumping (fear rising)"); add((v.get("chg_pct") or 0) <= -5, 1, "VIX easing")
    f = s.get("fii_index_futures_long_pct")
    add(f is not None and f < 25, -1, f"FIIs heavily short index futures ({f:.0f}% long)" if f is not None else "")
    add(f is not None and f > 60, 1, f"FIIs long index futures ({f:.0f}% long)" if f is not None else "")
    bias = "bullish" if pts >= 2 else "bearish" if pts <= -2 else "neutral"
    conf = min(85, 40 + 10 * abs(pts))
    lv = {k: {"support": v.get("support") or v.get("pdl"), "resistance": v.get("resistance") or v.get("pdh"),
              "gamma_flip": v.get("gamma_flip")} for k, v in s["indices"].items()}
    risks = []
    for c, t, mins in s.get("events_next_2h") or []:
        risks.append(f"{c} {t} in {mins} min")
    if s.get("ban"):
        risks.append("F&O ban: " + ", ".join(s["ban"][:5]))
    for k, v in s["indices"].items():
        if v.get("gamma") == "accelerating":
            risks.append(f"{k}: dealers in accelerating mode below {v.get('gamma_flip') or 'the flip'}: moves can run")
        if (v.get("iv_chg_15m") or 0) >= 1.5:
            risks.append(f"{k}: option IV rising fast")
    watch = [{"sym": h["sym"], "why": f"{h['chg_pct']:+.2f}%, {h.get('buildup') or 'active'}" if h.get("chg_pct") is not None else "active", "level": None}
             for h in s.get("hot_stocks") or []][:5]
    changed = []
    if prev:
        for k, v in s["indices"].items():
            pv = (prev.get("indices") or {}).get(k) or {}
            if pv.get("spot") and v.get("spot"):
                d = (v["spot"] / pv["spot"] - 1) * 100
                if abs(d) >= 0.1:
                    changed.append(f"{k} {d:+.2f}% since the last note")
            if pv.get("regime") and v.get("regime") and pv["regime"] != v["regime"]:
                changed.append(f"{k} day type: {pv['regime']} → {v['regime']}")
        pg, cg = (prev.get("global_pressure") or {}).get("label"), (s.get("global_pressure") or {}).get("label")
        if pg and cg and pg != cg:
            changed.append(f"Global mood: {pg} → {cg}")
    head = f"{'Bullish' if bias == 'bullish' else 'Bearish' if bias == 'bearish' else 'Neutral'} tilt"
    if n.get("spot"):
        head += f": Nifty {n['spot']:,.0f} ({(n.get('chg_pct') or 0):+.2f}%), {(n.get('regime') or 'opening').lower()}"
    plan = ("Favour longs on dips to support with stops below it." if bias == "bullish" else
            "Favour shorts on bounces into resistance with stops above it." if bias == "bearish" else
            "No edge from the big picture: trade only clean setups, smaller size.")
    return {"headline": head, "bias": bias, "confidence": conf, "summary": why or ["Signals are mixed."], "levels": lv,
            "risks": risks[:6], "watchlist": watch, "changed": changed or (["First note of the session."] if not prev else ["Little has changed."]),
            "plan": plan, "by": "rules"}


def ai_note(ai, s: dict, prev_note: dict | None) -> dict | None:
    prompt = (SYSTEM_STYLE + "You run the market desk. From this live snapshot, write a desk note as JSON ONLY with keys: "
              "headline (max 14 words), bias (bullish|bearish|neutral), confidence (0-100, be honest; mixed evidence = 40-55), "
              "summary (3-5 bullets, each under 22 words, cite numbers), levels ({INDEX: {support, resistance}} from the data), "
              "risks (2-4 bullets), watchlist (up to 5 {sym, why, level}), changed (what changed vs the previous note, 1-3 bullets), "
              "plan (one sentence: what a disciplined intraday trader should do now). Use only numbers from the data.\n\n"
              f"PREVIOUS NOTE: {_compact_json({k: prev_note.get(k) for k in ('headline', 'bias', 'confidence')}) if prev_note else 'none'}\n"
              f"SNAPSHOT: {_compact_json(s)}")
    text = ai.ask(prompt, 900)
    if not text:
        return None
    try:
        m = re.search(r"\{.*\}", text, re.S)
        d = json.loads(m.group(0))
        if d.get("bias") not in ("bullish", "bearish", "neutral"):
            return None
        d["by"] = "AI"
        return d
    except Exception:
        return None


class Brain:
    def __init__(self, engine):
        self.e = engine
        self.last_snap = None
        self.running = False

    def history(self, day=None) -> list:
        day = day or dt.datetime.now(IST).date().isoformat()
        return self.e.store.get(f"brain:{day}", []) or []

    def latest(self):
        h = self.history()
        return h[-1] if h else None

    def run(self, force_rules=False) -> dict:
        if self.running:
            return self.latest() or {}
        self.running = True
        try:
            s = snapshot(self.e)
            prev = self.latest()
            note = None
            if not force_rules and self.e.ai.enabled():
                note = ai_note(self.e.ai, s, prev)
            if not note:
                note = rules(s, self.last_snap)
            note["ts"] = time.time()
            note["time"] = s["time"]
            self.last_snap = s
            day = dt.datetime.now(IST).date().isoformat()
            h = self.history(day) + [note]
            self.e.store.put(f"brain:{day}", h[-80:])
            return note
        finally:
            self.running = False
