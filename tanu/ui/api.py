"""The web API behind the four screens. Read-only: it only reads Tanu's own files (and legacy option files in place
for trade charts); it never writes, never reaches a broker, and binds to 127.0.0.1 (see `python -m tanu web`)."""
from __future__ import annotations

import datetime as dt
import json
import shutil
from collections import Counter
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..journal import review
from ..journal.store import Journal
from ..setups import registry

STATIC = Path(__file__).with_name("static")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _json(p: Path, default=None):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def _latest(folder: Path, pattern: str) -> Path | None:
    fs = sorted(folder.glob(pattern)) if folder.exists() else []
    return fs[-1] if fs else None


def create_app(base: Path | str, legacy_history: Path | str | None = None) -> FastAPI:
    base = Path(base)
    app = FastAPI(title="Tanu", docs_url=None, redoc_url=None)
    jpath = base / "store" / "journal.db"

    def journal() -> Journal | None:
        return Journal(jpath) if jpath.exists() else None

    def latest_report() -> dict:
        r = base / "research"
        runs = sorted([p for p in r.glob("*") if p.is_dir() and (p / "report.json").exists()]) if r.exists() else []
        out = {}
        for p in runs:                                     # newer runs overwrite older numbers for the same setup
            rep = _json(p / "report.json", {})
            for s in rep.get("setups", []):
                out[s["setup"]] = {**s, "run": p.name, "t_bar": rep.get("t_bar"), "k": rep.get("k")}
        return out

    @app.get("/api/today")
    def today():
        j = journal()
        days = sorted({t["day"] for t in j.trades()}) if j else []
        day = days[-1] if days else None
        rev = review.daily(j, day) if (j and day) else None
        setups, bad = registry.load()
        cost = _latest(base / "costs", "tradeable_*.json")
        cj = _json(cost, {}) if cost else {}
        items = [i for i in cj.get("items", []) if i.get("tradeable")]
        hyp_f = base / "research" / "hypotheses.jsonl"
        hyps = [json.loads(x) for x in hyp_f.read_text().splitlines()[-5:]] if hyp_f.exists() else []
        passed = [k for k, v in latest_report().items() if v.get("passes")]
        return {"version": __version__, "live": False,
                "headline": _headline(rev, setups, passed),
                "journal_day": day,
                "accounts": {a: x["summary"] for a, x in (rev or {}).get("accounts", {}).items()},
                "setups": dict(Counter(s.status for s in setups)), "setups_passing": passed, "bad_setups": bad,
                "tradeable": {"day": cj.get("day"), "count": len(items), "of": len(cj.get("items", [])),
                              "cheapest": [{"sym": i["sym"], "round_trip_pct": i["cost"]["total_pct"]} for i in items[:8]]},
                "questions": hyps}

    @app.get("/api/setups")
    def setups():
        good, bad = registry.load()
        res = latest_report()
        out = []
        for s in good:
            mine = {k: v for k, v in res.items() if k == s.id or k.startswith(s.id + "__")}
            best = max(mine.values(), key=lambda v: v["learn"].get("avg_pct") or -999) if mine else None
            out.append({"id": s.id, "name": s.name, "status": s.status, "why": s.why, "source": s.source,
                        "symbols": s.symbols, "window": s.window, "variants_tested": len(mine),
                        "best": None if best is None else {"variant": best["setup"], "learn": best["learn"],
                                                           "hold": best["hold"], "passes": best.get("passes"),
                                                           "why_not": best.get("why_not", []), "t_bar": best.get("t_bar"),
                                                           "run": best.get("run")}})
        led = base / "research" / "ledger.jsonl"
        ids = {json.loads(x)["id"] for x in led.read_text().splitlines() if x.strip()} if led.exists() else set()
        k = len([i for i in ids if not i.startswith("random_baseline__")])     # yardsticks are not ideas
        return {"setups": out, "bad": bad, "ideas_tried": k}

    @app.get("/api/journal")
    def journal_rows(day: str | None = None, account: str | None = None, limit: int = 200):
        j = journal()
        if not j:
            return {"days": [], "trades": []}
        all_rows = j.trades(account=account)
        days = sorted({t["day"] for t in all_rows}, reverse=True)
        day = day or (days[0] if days else None)
        rows = [r for r in all_rows if r["day"] == day][:limit]
        return {"days": days[:60], "day": day, "accounts": sorted({t["account"] for t in all_rows}), "trades": rows,
                "review": review.daily(j, day) if day else None}

    @app.get("/api/trade")
    def trade(account: str, setup: str, sym: str, day: str, d: int, entry_t: float):
        j = journal()
        if not j:
            raise HTTPException(404, "No journal yet.")
        row = next((r for r in j.trades(day=day, account=account)
                    if r["setup"] == setup and r["sym"] == sym and r["d"] == d and abs(r["entry_t"] - entry_t) < 1), None)
        if row is None:
            raise HTTPException(404, "That trade is not in the journal.")
        return {"trade": row, "underlying": _underlying(base, sym, day),
                "option": _option_path(legacy_history, row) if legacy_history else []}

    @app.get("/api/system")
    def system():
        from ..doctor import checks
        sizes = {}
        for p in sorted(base.glob("*")) if base.exists() else []:
            if p.is_dir():
                sizes[p.name] = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        logs = _latest(base / "logs", "tanu-*.jsonl")
        tail = [json.loads(x) for x in logs.read_text().splitlines()[-40:]] if logs else []
        imp = _json(base / "imports" / "legacy" / "report.json", {})
        return {"version": __version__, "checks": checks(), "sizes": sizes,
                "free_gb": round(shutil.disk_usage(base if base.exists() else Path(".")).free / 1e9, 1),
                "import": {"at": imp.get("at"), "totals": imp.get("totals")}, "logs": tail[::-1]}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.exception_handler(Exception)
    async def _err(request, exc):                       # a clear message instead of a stack trace
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)

    return app


def _headline(rev, setups, passed) -> str:
    live = [s for s in setups if s.status in ("trial", "live")]
    if not rev:
        return "No paper trades recorded yet."
    sn = rev["accounts"].get("sniper", {}).get("summary")
    sh = rev["accounts"].get("shadow", {}).get("summary")
    parts = []
    if sn and sn["trades"]:
        parts.append(f"Sniper made ₹{sn['pnl_rs']:,.0f} on {sn['trades']} trades")
    elif not live:
        parts.append("Sniper is waiting: no setup has passed the tests yet")
    else:
        parts.append("Sniper took no trades")
    if sh and sh["trades"]:
        parts.append(f"shadow graded {sh['trades']} signals ({sh['win_pct']}% won)")
    return "; ".join(parts) + "."


def _underlying(base: Path, sym: str, day: str) -> list:
    a = dt.datetime.fromisoformat(day + "T09:15:00+05:30").timestamp()
    for p in (base / "bars" / "upstox" / f"{day}.parquet", base / "history" / f"{sym}.parquet"):
        if not p.exists():
            continue
        df = pd.read_parquet(p)
        if "key" in df.columns:
            df = df[df["key"] == sym]
        df = df[(df["t"] >= a) & (df["t"] < a + 22500)]
        if len(df):
            return [[int(t), round(float(c), 2)] for t, c in zip(df["t"], df["c"])]
    return []


def _option_path(legacy_history, row) -> list:
    from ..engine.options import OptionHistory
    oh = OptionHistory(legacy_history)
    out = []
    t0, t1 = int(row["entry_t"]) - 600, int(row["exit_t"] or row["entry_t"]) + 600
    for t in range(t0 - t0 % 60, t1 + 60, 60):
        b = oh.bar(row["sym"], row["day"], row["strike"], row["side"], t)
        if b:
            out.append([t, round(float(b[3]), 2)])
    return out
