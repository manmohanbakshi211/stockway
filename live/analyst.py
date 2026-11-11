"""Evening AI analyst (2.65): after the close the deep AI reads the whole day and writes what worked, what failed,
what looks broken, and a few new ideas that go on trial in the multiverse.

Mandeep, 11 Nov 2026: "AI kaha kaha pure project ko better bana sakta hai ... kya kya cheej bhot jaldi pakad sakta hai".
Today's QA found by hand what this reads every evening: Dhan answering "too many retries", the scanner trading on a
holiday's frozen prices, the main account full when the Bank Nifty breakout came.

Gun: collects one compact fact sheet (paper accounts, alerts by type, the multiverse's best / walk-forward / brain, the
mass search, the news radar's scores, the day's error lines from the app log, failed self-checks) and asks the deep AI
tier (rules/ai_models.json "analyst": Opus) for JSON. Ideas are checked against the facts the multiverse knows and saved
to data/multiverse_ideas.json: the next multiverse run tests them like every other filter (on trial, nothing trades
them by being proposed). The day's report: data/analyst/<day>.json, the Strategy board, and Telegram when set.
Only the app's own numbers and log lines are sent. Paper and information only.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from pathlib import Path

IDEAS = "multiverse_ideas.json"
MAX_IDEAS = 40


def _log_problems(data_dir, day: str, limit: int = 30) -> list:
    f = Path(data_dir) / "logs" / "app.log"
    out, seen = [], set()
    try:
        lines = f.read_text(errors="ignore").splitlines()[-6000:]
    except Exception:
        return out
    for ln in lines:
        if not ln.startswith(day):
            continue
        low = ln.lower()
        if any(w in low for w in ("error", "failed", "traceback", "too many", "skipped", "not started", "refused", "expired")):
            key = re.sub(r"\d+", "#", ln[20:120])
            if key in seen:
                continue
            seen.add(key)
            out.append(ln[11:240])
    return out[-limit:]


def facts(engine, data_dir, day: str) -> dict:
    """The day on one page (numbers only)."""
    data_dir = Path(data_dir)
    out: dict = {"day": day}
    try:
        from . import books as B
        rows = B.compare(engine.store, engine.source, engine.paper.cfg if engine.paper else {}, engine.books, days=1)
        out["paper"] = [{k: r.get(k) for k in ("name", "trades", "won_pct", "net", "worst_day")} for r in rows]
    except Exception as e:
        out["paper_error"] = str(e)[:120]
    try:
        kinds: dict = {}
        for a in list(engine.alerts_mem):
            if a.get("day") == day and a.get("direction") in ("up", "down"):
                kinds[a["kind"]] = kinds.get(a["kind"], 0) + 1
        out["alerts_by_type"] = dict(sorted(kinds.items(), key=lambda x: -x[1])[:20])
    except Exception:
        pass
    try:
        mv = json.loads((data_dir / "multiverse.json").read_text())
        out["multiverse"] = {"days": mv.get("days"), "timelines": mv.get("timelines"),
                             "best": [{k: b.get(k) for k in ("words", "n", "avg", "lcb", "green_days", "days")} for b in (mv.get("best") or [])[:6]],
                             "luck_lcb": (mv.get("luck") or {}).get("lcb"), "walk_total": mv.get("walk_total"),
                             "by_regime": [{k: r.get(k) for k in ("name", "n", "avg", "lcb", "words")} for r in mv.get("by_regime") or []],
                             "brain": {k: (mv.get("brain") or {}).get(k) for k in ("walk", "trusted", "walk_top_avg", "walk_all_avg")}}
    except Exception:
        pass
    for lab in ("lab", "lab5y/lab"):
        try:
            ms = json.loads((data_dir / lab / "mass.json").read_text())
            out[f"mass_{lab.split('/')[0]}"] = {"tested": ms.get("tested"), "luck": ms.get("luck_lcb"),
                                                "walk_total": ms.get("walk_total"), "best": [b.get("rule") for b in (ms.get("best") or [])[:3]]}
        except Exception:
            pass
    try:
        out["news_radar"] = {k: v for k, v in engine.radar_news.view().items() if k in ("ai", "keywords", "ai_big", "graded")}
    except Exception:
        pass
    out["log_problems"] = _log_problems(data_dir, day)
    try:
        from .selfcheck import static_checks
        out["failed_checks"] = [c["name"] for c in static_checks() if c.get("ok") is False]
    except Exception:
        pass
    return out


def known_facts(data_dir) -> list:
    """The facts the multiverse can test (the brain's feature names, or the configured filters)."""
    try:
        import joblib
        return list(joblib.load(Path(data_dir) / "multiverse_brain.joblib")["names"])
    except Exception:
        pass
    try:
        from lab.multiverse import cfg
        return sorted({s["fact"] for s in cfg()["singles"] if "fact" in s})
    except Exception:
        return []


def prompt(f: dict, names: list) -> str:
    return ("You are the evening analyst of an Indian F&O alerts and paper-trading app (no real orders). Read today's facts "
            "and answer in strict JSON only: {\"worked\": [3 short lines], \"failed\": [3 short lines], "
            "\"bugs\": [up to 5 lines: something that looks broken in the app, with the log line or number that shows it], "
            "\"ideas\": [up to 5 objects {\"name\": short, \"fact\": one of the fact names below, \"op\": \">\" or \"<\", "
            "\"v\": number, \"why\": one line}] = new filters worth testing in the multiverse (it will test them on days they "
            "never saw; propose what today's numbers suggest, not what is already in 'best'), \"tomorrow\": one line}. "
            "Be specific with numbers. Say plainly when a few days prove nothing.\n"
            f"Fact names: {', '.join(names[:120])}\n\nToday:\n{json.dumps(f, default=str)[:14000]}")


def parse(text: str | None, names: set) -> dict | None:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    try:
        d = json.loads(m.group(0) if m else text)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    ideas = []
    for x in d.get("ideas") or []:
        try:
            if x["fact"] in names and x["op"] in (">", "<"):
                ideas.append({"name": str(x.get("name") or x["fact"])[:60], "fact": x["fact"], "op": x["op"], "v": float(x["v"]),
                              "why": str(x.get("why") or "")[:160]})
        except Exception:
            continue
    clip = lambda xs, n: [str(s)[:240] for s in (xs or [])][:n]
    return {"worked": clip(d.get("worked"), 4), "failed": clip(d.get("failed"), 4), "bugs": clip(d.get("bugs"), 5),
            "ideas": ideas[:5], "tomorrow": str(d.get("tomorrow") or "")[:240]}


def add_ideas(data_dir, ideas: list, day: str) -> int:
    """Append to data/multiverse_ideas.json (deduplicated; the oldest drop off past MAX_IDEAS)."""
    p = Path(data_dir) / IDEAS
    try:
        cur = json.loads(p.read_text()).get("singles") or []
    except Exception:
        cur = []
    keys = {(s["fact"], s["op"], round(float(s["v"]), 4)) for s in cur}
    n = 0
    for x in ideas:
        k = (x["fact"], x["op"], round(float(x["v"]), 4))
        if k in keys:
            continue
        keys.add(k)
        cur.append({"name": f"AI idea: {x['name']}", "fact": x["fact"], "op": x["op"], "v": x["v"], "why": x["why"],
                    "source": "AI analyst", "added": day})
        n += 1
    cur = cur[-MAX_IDEAS:]
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"about": "Filters proposed by the evening AI analyst (live/analyst.py); the multiverse tests "
                                        "them on days they never saw. Nothing trades them by being proposed.", "singles": cur},
                              indent=1))
    tmp.replace(p)
    return n


def run(engine, data_dir, day: str | None = None, log=print) -> dict:
    data_dir = Path(data_dir)
    day = day or dt.date.today().isoformat()
    f = facts(engine, data_dir, day)
    names = known_facts(data_dir)
    rep = None
    if engine.ai and engine.ai.enabled():
        rep = parse(engine.ai.ask(prompt(f, names), 1800, auto=False, task="analyst"), set(names))
    out = {"day": day, "made": time.time(), "facts": f, "report": rep,
           "note": None if rep else "No AI answer (AI off, limit reached or unreadable reply): the fact sheet is kept."}
    if rep and rep["ideas"]:
        out["ideas_added"] = add_ideas(data_dir, rep["ideas"], day)
    d = data_dir / "analyst"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{day}.json").write_text(json.dumps(out, default=str, indent=1))
    log(f"Evening analyst: {'report written' if rep else 'no AI report'}; {out.get('ideas_added', 0)} idea(s) on trial; "
        f"{len(f.get('log_problems') or [])} problem line(s) in the log")
    if rep:
        try:
            lines = ["🧠 <b>Stockway evening analyst</b>"] + [f"✅ {x}" for x in rep["worked"][:2]] + [f"❌ {x}" for x in rep["failed"][:2]] + \
                    [f"🐞 {x}" for x in rep["bugs"][:3]] + ([f"➡️ {rep['tomorrow']}"] if rep["tomorrow"] else [])
            engine.telegram.send("\n".join(lines))
        except Exception:
            pass
    return out


def latest(data_dir) -> dict:
    d = Path(data_dir) / "analyst"
    fs = sorted(d.glob("*.json")) if d.exists() else []
    if not fs:
        return {"note": "The evening analyst writes its first report after today's close (after the multiverse)."}
    try:
        return json.loads(fs[-1].read_text())
    except Exception as e:
        return {"note": f"Could not read the last report: {e}"}
