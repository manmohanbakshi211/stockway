"""The evening review: what happened in each paper account today, why trades won or lost, and the questions it
raises. Deterministic (numbers from the journal); an AI write-up can be added on top later, reading this same summary.
Questions go to <base>/research/hypotheses.jsonl as research ideas, never straight to live (ADR-0005).

Lessons computed from the footprints:
- "went green, closed red": losers whose option was up >= 10% at some point (exit too late / too loose);
- "never moved": losers whose option never got 3% up (entry wrong or too early);
- results by the day type at entry (trend / range / quiet) and by exit reason;
- refusals by reason (what the risk rules stopped).
"""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter, defaultdict
from pathlib import Path

from .store import Journal

GREEN_THEN_RED, NEVER_MOVED = 10.0, 3.0


def _summ(rows: list[dict]) -> dict:
    done = [r for r in rows if r.get("pnl_rs") is not None]
    pnl = sum(r["pnl_rs"] for r in done)
    wins = sum(r["pnl_rs"] > 0 for r in done)
    return {"trades": len(done), "wins": wins, "win_pct": round(wins / len(done) * 100, 1) if done else None,
            "pnl_rs": round(pnl, 2), "avg_pct": round(sum(r["pct"] for r in done) / len(done), 2) if done else None}


def daily(journal: Journal, day: str) -> dict:
    rows = journal.trades(day=day)
    out = {"day": day, "made": dt.datetime.now().isoformat(timespec="seconds"), "accounts": {}, "hypotheses": []}
    by_acc = defaultdict(list)
    for r in rows:
        by_acc[r["account"]].append(r)
    for acc, rs in sorted(by_acc.items()):
        done = [r for r in rs if r.get("pnl_rs") is not None]
        losers = [r for r in done if r["pnl_rs"] < 0]
        a = {"summary": _summ(rs),
             "by_setup": {k: _summ(v) for k, v in _group(rs, lambda r: r["setup"]).items()},
             "by_day_type": {k: _summ(v) for k, v in _group(rs, lambda r: r["state"].get("day", "?")).items()},
             "by_exit": {k: _summ(v) for k, v in _group(rs, lambda r: r["reason"] or "?").items()},
             "green_then_red": sum((r.get("mfe_pct") or 0) >= GREEN_THEN_RED for r in losers),
             "never_moved": sum((r.get("mfe_pct") or 0) < NEVER_MOVED for r in losers),
             "losers": len(losers)}
        if done:
            best, worst = max(done, key=lambda r: r["pnl_rs"]), min(done, key=lambda r: r["pnl_rs"])
            a["best"], a["worst"] = _foot(best), _foot(worst)
        out["accounts"][acc] = a
        if losers and a["green_then_red"] >= max(2, len(losers) / 3):
            out["hypotheses"].append({"account": acc, "idea": "exits give back gains",
                                      "evidence": f"{a['green_then_red']} of {len(losers)} losers were up {GREEN_THEN_RED:.0f}%+ first",
                                      "test": "a premium trail or partial exit armed at +10%"})
        if losers and a["never_moved"] >= max(2, len(losers) / 2):
            out["hypotheses"].append({"account": acc, "idea": "entries come too early or in the wrong place",
                                      "evidence": f"{a['never_moved']} of {len(losers)} losers never got {NEVER_MOVED:.0f}% up",
                                      "test": "wait for a confirmation bar / skip that situation"})
    out["blocked"] = dict(Counter(f"{b['account']}: {b['reason'].split(':')[0]}" for b in journal.blocked_rows(day)))
    return out


def _group(rows, key):
    g = defaultdict(list)
    for r in rows:
        g[key(r)].append(r)
    return g


def _foot(r: dict) -> dict:
    t = lambda x: dt.datetime.fromtimestamp(x, dt.timezone(dt.timedelta(hours=5, minutes=30))).strftime("%H:%M") if x else None
    return {"setup": r["setup"], "sym": r["sym"], "side": r["side"], "strike": r["strike"], "in": t(r["entry_t"]),
            "out": t(r["exit_t"]), "entry": round(r["entry"], 2), "exit": round(r["exit"], 2), "pnl_rs": r["pnl_rs"],
            "pct": r["pct"], "best_pct": r.get("mfe_pct"), "worst_pct": r.get("mae_pct"), "exit_reason": r["reason"],
            "day_type": r["state"].get("day")}


def markdown(rev: dict) -> str:
    L = [f"# Review {rev['day']}", ""]
    for acc, a in rev["accounts"].items():
        s = a["summary"]
        L += [f"## {acc}: {s['trades']} trades, {s['wins']} won, ₹{s['pnl_rs']:,.0f} ({s['avg_pct']}% a trade)", ""]
        if a.get("best"):
            b, w = a["best"], a["worst"]
            L.append(f"- Best: {b['setup']} {b['sym']} {b['side']} {b['in']}-{b['out']} ₹{b['pnl_rs']:,.0f} ({b['exit_reason']})")
            L.append(f"- Worst: {w['setup']} {w['sym']} {w['side']} {w['in']}-{w['out']} ₹{w['pnl_rs']:,.0f} (best point {w['best_pct']}%)")
        L.append(f"- Losers that were up 10%+ first: {a['green_then_red']} of {a['losers']}; never moved 3%: {a['never_moved']}")
        L.append("- By day type: " + ", ".join(f"{k} ₹{v['pnl_rs']:,.0f} ({v['trades']})" for k, v in a["by_day_type"].items()))
        L.append("")
    if rev["blocked"]:
        L += ["## Refused by risk rules", ""] + [f"- {k}: {v}" for k, v in rev["blocked"].items()] + [""]
    if rev["hypotheses"]:
        L += ["## Questions for research", ""] + [f"- {h['account']}: {h['idea']} ({h['evidence']}); test: {h['test']}"
                                                  for h in rev["hypotheses"]]
    return "\n".join(L)


def write(rev: dict, base: Path | str) -> Path:
    base = Path(base)
    d = base / "journal"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"review_{rev['day']}.json").write_text(json.dumps(rev, indent=1, default=str))
    p = d / f"review_{rev['day']}.md"
    p.write_text(markdown(rev))
    if rev["hypotheses"]:
        r = base / "research"
        r.mkdir(parents=True, exist_ok=True)
        with open(r / "hypotheses.jsonl", "a") as f:
            for h in rev["hypotheses"]:
                f.write(json.dumps({"day": rev["day"], **h}) + "\n")
    return p
