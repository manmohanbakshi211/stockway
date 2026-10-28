"""HTTP routes for the live scanner (mounted by webapp.server)."""
from __future__ import annotations

import datetime as dt
import html
import math
import threading

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from live.clock import IST
from radar.config import ROOT, load_config

router = APIRouter()
_engine = None
_lock = threading.Lock()


def engine():
    global _engine
    with _lock:
        if _engine is None:
            from live.engine import LiveEngine
            _engine = LiveEngine(load_config, ROOT / "data")
        return _engine


def clean(o):
    if isinstance(o, float):
        return None if math.isnan(o) or math.isinf(o) else round(o, 4)
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    return o


def _page(title, body, ok=True):
    color = "#1b7f4b" if ok else "#b42318"
    return HTMLResponse(f"""<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><body style="font-family:-apple-system,system-ui,sans-serif;max-width:560px;margin:60px auto;padding:0 16px;color:#1a1a1a">
<h2 style="color:{color}">{html.escape(title)}</h2><p>{body}</p><p><a href="/#live">Back to the app</a></p></body>""")


# ----------------------------------------------------------------- Upstox login
@router.get("/upstox/login")
def upstox_login():
    from live.upstox import login_url
    c = engine().cfg()["live"]
    if not c.get("upstox_api_key") or not c.get("upstox_api_secret"):
        return _page("Add your Upstox API key first",
                     "Open Settings → Live data and paste the API key and secret from your Upstox developer app.", ok=False)
    return RedirectResponse(login_url(c["upstox_api_key"], c["redirect_uri"]))


@router.get("/upstox/callback")
def upstox_callback(code: str | None = None, error: str | None = None):
    from live.upstox import exchange_code
    e = engine()
    if error or not code:
        return _page("Upstox login was cancelled", html.escape(error or "No login code came back."), ok=False)
    c = e.cfg()["live"]
    try:
        body = exchange_code(code, c["upstox_api_key"], c["upstox_api_secret"], c["redirect_uri"])
    except Exception as ex:
        return _page("Upstox login failed", html.escape(str(ex)) +
                     "<br><br>Check that the redirect URL in your Upstox app is exactly "
                     f"<code>{html.escape(c['redirect_uri'])}</code>", ok=False)
    e.tokens.save(body)
    e.log(f"Logged in to Upstox as {body.get('user_name') or body.get('user_id')}")
    now = dt.datetime.now(IST)
    if now.weekday() < 5 and dt.time(9, 0) <= now.time() < dt.time(15, 30) and not e.running:
        e.manual_stop_day = None
        e.start("upstox")
    elif e.status["state"] == "login_needed":
        e.status.update(state="stopped", message="Logged in. The scanner starts automatically at 9:00 on market days.")
    return RedirectResponse("/#live")


@router.post("/api/live/logout")
def upstox_logout():
    e = engine()
    if e.running and e.source == "upstox":
        e.stop()
    e.tokens.clear()
    return {"ok": True}


# ----------------------------------------------------------------- control
@router.post("/api/live/start")
async def live_start(req: Request):
    body = await req.json() if (await req.body()) else {}
    e = engine()
    source = body.get("source") or e.cfg()["live"]["source"]
    if source not in ("upstox", "sim"):
        raise HTTPException(400, "Unknown data source")
    if e.running:
        if e.source == source:
            return clean(e.status)
        e.stop(manual=False)
    if source == "upstox" and not e.tokens.load():
        raise HTTPException(400, "Log in with Upstox first.")
    e.manual_stop_day = None
    return clean(e.start(source))


@router.post("/api/live/stop")
def live_stop():
    engine().stop()
    return clean(engine().status)


# ----------------------------------------------------------------- data
@router.get("/api/live/snapshot")
def live_snapshot(since: int = 0):
    return clean(engine().snapshot(since))


@router.get("/api/live/scanner")
def live_scanner():
    return clean(engine().scanner())


@router.get("/api/live/symbol/{sym}")
def live_symbol(sym: str, expiry: str | None = None):
    e = engine()
    try:
        d = e.symbol(sym.upper(), expiry)
    except PermissionError as ex:
        raise HTTPException(401, str(ex))
    if not d:
        raise HTTPException(404, "No live data for this symbol yet. Start the live scanner first.")
    return clean(d)


@router.get("/api/live/alerts")
def live_alerts(day: str | None = None, sym: str | None = None, min_sev: int = 1, source: str | None = None):
    e = engine()
    day = day or dt.datetime.now(IST).date().isoformat()
    if day == "latest":
        days = e.store.alert_days(1)
        day = days[0]["day"] if days else None
    return clean({"day": day, "alerts": e.store.alerts(day=day, sym=sym.upper() if sym else None, min_sev=min_sev,
                                                        source=source, limit=1000),
                  "days": e.store.alert_days(60)})


@router.post("/api/live/alerts/{alert_id}/explain")
def live_explain(alert_id: int):
    e = engine()
    if not e.ai.enabled():
        raise HTTPException(400, "Add an AI key in Settings → AI to get explanations.")
    text = e.explain(alert_id)
    if not text:
        raise HTTPException(502, e.ai.last_error or "AI did not answer. Try again in a minute.")
    return {"text": text}


@router.post("/api/live/alerts/{alert_id}/paper")
def live_paper_from_alert(alert_id: int):
    e = engine()
    if not e.running or not e.paper:
        raise HTTPException(400, "Start the live scanner first.")
    a = next((x for x in e.alerts_mem if x["id"] == alert_id), None) or e.store.alert(alert_id)
    if not a or not a.get("option"):
        raise HTTPException(400, "This alert has no option idea to paper trade.")
    tid = e.paper.try_open(a, e.state, e.clock.now(), manual=True)
    if not tid:
        raise HTTPException(400, f"Already holding a paper trade in {a['sym']}, or no live price for the option.")
    return {"ok": True, "id": tid}


@router.post("/api/live/paper/{trade_id}/close")
def live_paper_close(trade_id: int):
    e = engine()
    if not e.paper:
        raise HTTPException(400, "Start the live scanner first.")
    r = e.paper.close_manual(trade_id, e.state, e.clock.now())
    if not r:
        raise HTTPException(404, "Trade not open")
    return clean(r)


@router.get("/api/live/paper")
def live_paper(source: str | None = None):
    from live.paper import PaperBook
    e = engine()
    src = source or (e.source if e.paper else e.cfg()["live"]["source"])
    book = e.paper if e.paper and e.source == src else PaperBook(e.store, src, e.cfg()["live"].get("paper"))
    return clean({**book.summary(e.state if e.paper is book else None), "source": src})


@router.get("/api/live/logs")
def live_logs():
    return {"lines": list(engine().logs)[-200:]}


@router.get("/api/live/info")
def live_info():
    e = engine()
    c = e.cfg()
    lc = c["live"]
    tok = e.tokens.load()
    return clean({"source": lc["source"], "has_keys": bool(lc.get("upstox_api_key") and lc.get("upstox_api_secret")),
                  "redirect_uri": lc["redirect_uri"], "logged_in": bool(tok), "user": tok.get("user") if tok else None,
                  "token_expires": tok.get("expires_at") if tok else None, "status": e.status, "running": e.running,
                  "telegram": e.telegram.enabled(), "ai": e.ai.enabled()})
