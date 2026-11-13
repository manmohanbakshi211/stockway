"""The web API behind the four screens, on 127.0.0.1 only. Screens are read-only: they read Tanu's own files (and
legacy option files in place for trade charts). The only writes are the live setup on the System page: the Upstox app
key / secret, the daily Upstox login and the Telegram details, saved as secrets on this machine (never shown back).
Those routes refuse requests from other web pages (Origin / Host / X-Tanu checks) and the Upstox login answers only
after cutover (settings `live`, ADR-0009)."""
from __future__ import annotations

import datetime as dt
import json
import shutil
from collections import Counter
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..core.secrets import Secrets
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


def create_app(base: Path | str, legacy_history: Path | str | None = None, *, settings=None, keeper=None,
               telegram=None, supervisor=None) -> FastAPI:
    from ..brokers.upstox.auth import TokenKeeper, redirect_uri
    from ..core import config
    from ..notify.telegram import Telegram
    base = Path(base)
    app = FastAPI(title="Tanu", docs_url=None, redoc_url=None)
    jpath = base / "store" / "journal.db"
    s = settings or config.load()
    sec = Secrets(base)
    keeper = keeper or TokenKeeper(sec)
    telegram = telegram or Telegram(sec)
    hosts = {f"127.0.0.1:{s.web_port}", f"localhost:{s.web_port}"}

    def guard(request: Request, write: bool = True) -> None:
        """Only this machine's own app page may use the setup routes (no other web page, no DNS tricks)."""
        if request.headers.get("host") not in hosts:
            raise HTTPException(403, "Open Tanu at http://127.0.0.1:%d" % s.web_port)
        origin = request.headers.get("origin")
        if origin and origin.split("://", 1)[-1] not in hosts:
            raise HTTPException(403, "Request from another web page refused.")
        if write and request.headers.get("x-tanu") != "1":
            raise HTTPException(403, "Request refused.")

    def journal() -> Journal | None:
        return Journal(jpath) if jpath.exists() else None

    def account_info() -> list[dict]:
        from ..paper.accounts import SPEC
        spec = _json(SPEC, {}) or {}
        return [{"id": a["id"], "name": a.get("name", a["id"]), "kind": a.get("kind", "buy"), "capital": a.get("capital")}
                for a in spec.get("accounts", [])]

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
        summ = {a: x["summary"] for a, x in (rev or {}).get("accounts", {}).items()}
        accts = []
        for a in account_info():
            sm = summ.get(a["id"]) or {"trades": 0, "pnl_rs": 0.0, "win_pct": None, "avg_pct": None}
            accts.append({**a, **sm, "return_pct": round(sm["pnl_rs"] / a["capital"] * 100, 2) if a["capital"] else None})
        return {"version": __version__, "live": s.live,
                "headline": _headline(rev, setups, passed),
                "journal_day": day,
                "accounts": summ, "account_list": accts,
                "setups": dict(Counter(s.status for s in setups)), "setups_passing": passed, "bad_setups": bad,
                "tradeable": {"day": cj.get("day"), "count": len(items), "of": len(cj.get("items", [])),
                              "cheapest": [{"sym": i["sym"], "round_trip_pct": i["cost"]["total_pct"]} for i in items[:8]]},
                "questions": hyps}

    @app.get("/api/setups")
    def setups():
        good, bad = registry.load()
        res = latest_report()
        out = []
        def pick(rows):
            b = max(rows.values(), key=lambda v: v["learn"].get("avg_pct") or -999) if rows else None
            return None if b is None else {"variant": b["setup"], "learn": b["learn"], "hold": b["hold"],
                                           "passes": b.get("passes"), "why_not": b.get("why_not", []),
                                           "t_bar": b.get("t_bar"), "run": b.get("run")}
        for s in good:
            mine = {k: v for k, v in res.items() if k == s.id or k.startswith(s.id + "__")}
            buy = {k: v for k, v in mine.items() if not k.endswith("@sell")}
            sell = {k: v for k, v in mine.items() if k.endswith("@sell")} | ({s.id + "@sell": res[s.id + "@sell"]}
                                                                           if s.id + "@sell" in res else {})
            out.append({"id": s.id, "name": s.name, "status": s.status, "why": s.why, "source": s.source,
                        "symbols": s.symbols, "window": s.window, "variants_tested": len(buy),
                        "sell_variants_tested": len(sell), "best": pick(buy), "best_sell": pick(sell),
                        "sell_plan": s.sell.model_dump()})
        led = base / "research" / "ledger.jsonl"
        ids = {json.loads(x)["id"] for x in led.read_text().splitlines() if x.strip()} if led.exists() else set()
        from ..evaluate.search import pretest_ids
        ids |= pretest_ids()
        k = len([i for i in ids if not (i.startswith("random_baseline__") or i == "random_baseline@sell")])  # yardsticks
        return {"setups": out, "bad": bad, "ideas_tried": k}

    @app.get("/api/journal")
    def journal_rows(day: str | None = None, account: str | None = None, kind: str = "buy", limit: int = 200):
        """kind: "buy" (bought options) or "sell" (the option-selling section)."""
        j = journal()
        info = account_info()
        mine = [a["id"] for a in info if a["kind"] == kind]
        if not j:
            return {"days": [], "trades": [], "kind": kind, "accounts": mine, "account_info": info}
        all_rows = [r for r in j.trades(account=account) if r.get("kind", "buy") == kind]
        days = sorted({t["day"] for t in all_rows}, reverse=True)
        day = day or (days[0] if days else None)
        rows = [r for r in all_rows if r["day"] == day][:limit]
        rev = review.daily(j, day) if day else None
        if rev:
            rev = {**rev, "accounts": {a: x for a, x in rev.get("accounts", {}).items() if a in mine}}
        return {"days": days[:60], "day": day, "kind": kind, "accounts": mine, "account_info": info, "trades": rows,
                "review": rev}

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

    @app.get("/api/live")
    def live():
        st = _json(base / "live" / "status.json", None)
        fresh = bool(st) and (dt.datetime.now(IST) - dt.datetime.fromisoformat(st["at"])).total_seconds() < 120
        return {"switch": s.live, "status": st, "fresh": fresh,
                "supervisor": getattr(supervisor, "state", None) if supervisor else None}

    @app.get("/api/setup")
    def setup(request: Request):
        guard(request, write=False)
        from ..live.app import port_in_use
        legacy = s.web_port != s.legacy_port and port_in_use(s.legacy_port)
        up = keeper.status()
        return {"switch": s.live, "web_port": s.web_port, "legacy_running": legacy,
                "redirect_uri": redirect_uri(s.legacy_port), "upstox": up,
                "telegram": {"saved": telegram.configured(), "sent": telegram.sent, "failed": telegram.failed,
                             "last_error": telegram.last_error},
                "mode": getattr(supervisor, "state", None) if supervisor else None}

    @app.post("/api/setup/upstox")
    async def setup_upstox(request: Request):
        guard(request)
        b = await request.json()
        try:
            keeper.save_app(b.get("api_key", ""), b.get("api_secret", ""))
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "upstox": keeper.status()}

    @app.post("/api/setup/telegram")
    async def setup_telegram(request: Request):
        guard(request)
        b = await request.json()
        try:
            telegram.save(b.get("bot_token", ""), b.get("chat_id", ""))
        except ValueError as e:
            raise HTTPException(400, str(e))
        ok, why = telegram.send_now("Tanu is connected. Alerts for setups on trial or live will come here.")
        return {"ok": ok, "message": "Test message sent." if ok else f"Saved, but the test message failed: {why}"}

    @app.get("/upstox/login")
    def upstox_login(request: Request):
        guard(request, write=False)
        if not s.live:
            raise HTTPException(409, "Upstox login opens only after the switch-over (cutover).")
        try:
            return RedirectResponse(keeper.start_login(s.legacy_port), status_code=302)
        except PermissionError as e:
            raise HTTPException(400, str(e))

    @app.get("/upstox/callback")
    def upstox_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
        guard(request, write=False)
        if not s.live:
            raise HTTPException(409, "Upstox login opens only after the switch-over (cutover).")
        if error or not code:
            return RedirectResponse("/#system?login=failed", status_code=302)
        try:
            keeper.finish_login(code, state or "", s.legacy_port)
        except PermissionError:
            return RedirectResponse("/#system?login=failed", status_code=302)
        return RedirectResponse("/#system?login=ok", status_code=302)

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
    ss = rev["accounts"].get("shadow_sell", {}).get("summary")
    if sh and sh["trades"]:
        parts.append(f"shadow graded {sh['trades']} bought options ({sh['win_pct']}% won)"
                     + (f" and {ss['trades']} sold ({ss['win_pct']}% won)" if ss and ss["trades"] else ""))
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
