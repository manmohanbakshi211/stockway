"""HTTP routes for the live scanner (mounted by webapp.server)."""
from __future__ import annotations

import datetime as dt
import html
import math
import re
import threading
import time

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
        msg = str(ex)
        low = msg.lower()
        if "segment" in low or "reactivat" in low or "dormant" in low:
            hint = ("Your Upstox account itself is not active for trading yet. Open the Upstox app → Account → "
                    "Segments, activate <b>Equity</b> and <b>F&amp;O</b> (re-activation can take up to a day), then "
                    "click Log in with Upstox again. The app connection is fine.")
        elif "redirect" in low:
            hint = ("Check that the redirect URL in your Upstox app is exactly "
                    f"<code>{html.escape(c['redirect_uri'])}</code>")
        elif "client" in low or "credential" in low or "secret" in low:
            hint = "Check the API key and secret in Live setup. Copy them again from your Upstox app."
        else:
            hint = "Try Log in with Upstox once more. If it repeats, send this message to support."
        return _page("Upstox login failed", html.escape(msg) + "<br><br>" + hint, ok=False)
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


@router.get("/api/live/chart/{sym}")
def live_chart(sym: str, tf: int = 5, days: int = 3):
    e = engine()
    if tf not in (1, 3, 5, 15, 30, 60):
        raise HTTPException(400, "Timeframe must be 1, 3, 5, 15, 30 or 60 minutes")
    try:
        d = e.charts.payload(sym.upper(), tf, max(1, min(5, days)))
    except PermissionError as ex:
        raise HTTPException(401, str(ex))
    if not d:
        raise HTTPException(404, "No live data for this symbol yet. Start the live scanner (or the simulator) first.")
    return clean(d)


@router.post("/api/live/chart/{sym}/ai")
def live_chart_ai(sym: str):
    from live.notify import SYSTEM_STYLE, _compact_json, _slim_metrics
    e = engine()
    if not e.ai.enabled():
        raise HTTPException(400, "Choose an AI in Live setup (your Claude Pro plan works) to get an AI chart reading.")
    sym = sym.upper()
    d = e.charts.payload(sym, 5, 1)
    if not d:
        raise HTTPException(404, "No chart data yet.")
    c = d["candles"][-24:]
    s = d["series"]
    last = lambda k: (s[k][-1]["value"] if s.get(k) else None)
    facts = {"symbol": sym, "candles_5m_last_2h": [[x["open"], x["high"], x["low"], x["close"]] for x in c],
             "indicators": {k: last(k) for k in ("vwap", "u1", "d1", "u2", "d2", "ema9", "ema21", "ema50", "rsi", "macd", "macd_sig")},
             "supertrend": "up" if s.get("st_up") and s["st_up"][-1]["time"] == c[-1]["time"] else "down",
             "levels": {l["name"]: l["price"] for l in d["levels"]}, "rule_reading": d["read"], "setups": d["setups"],
             "regime": d.get("regime"), "metrics": _slim_metrics(e.metrics.get(sym)),
             "news": [n["title"] for n in e.news.items if sym in n["syms"]][:5]}
    prompt = (SYSTEM_STYLE + "You are reading a live 5-minute chart like a senior prop trader. In 5 short bullets: "
              "1) structure and trend, 2) where price sits vs the key levels, 3) the best trade setup now with entry, stop and "
              "target levels (or 'no trade' and why), 4) what would change your mind, 5) one risk. Use only the numbers given.\n\n"
              + _compact_json(facts))
    text = e.ai.ask(prompt, 450, auto=False)
    if not text:
        raise HTTPException(502, e.ai.last_error or "AI did not answer.")
    return {"text": text}


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
        raise HTTPException(400, "Choose an AI in Live setup (your Claude Pro plan works) to get explanations.")
    text = e.explain(alert_id, auto=False)
    if not text:
        raise HTTPException(502, e.ai.last_error or "AI did not answer. Try again in a minute.")
    return {"text": text}


@router.post("/api/live/alerts/{alert_id}/timemachine")
def live_timemachine_alert(alert_id: int):
    """Replay an alert's plan (entry now, its stop and target) 10,000 times and on the real past."""
    e = engine()
    a = next((x for x in e.alerts_mem if x["id"] == alert_id), None) or e.store.alert(alert_id)
    if not a:
        raise HTTPException(404, "Alert not found")
    d = a.get("data") or {}
    lv = d.get("setup_levels") or d.get("levels") or {}
    if a.get("direction") not in ("up", "down") or not lv.get("stop") or not lv.get("target"):
        raise HTTPException(400, "This alert has no stop and target to test.")
    m = e.metrics.get(a["sym"]) or {}
    entry = m.get("spot") or a.get("price")
    if not entry:
        raise HTTPException(400, "No current price for this symbol.")
    return clean(_tm(e, a["sym"], float(entry), float(lv["stop"]), float(lv["target"]), a["direction"]))


@router.post("/api/timemachine")
async def timemachine_plan(req: Request):
    b = await req.json()
    e = engine()
    try:
        start = (str(b.get("start") or "").strip() or None)
        if start and not re.fullmatch(r"\d{1,2}:\d{2}", start):
            raise HTTPException(400, "Start time must look like 10:30")
        return clean(_tm(e, str(b["sym"]).upper().strip(), float(b["entry"]), float(b["stop"]), float(b["target"]),
                         b.get("direction") or ("up" if float(b["target"]) > float(b["entry"]) else "down"), start))
    except KeyError as ex:
        raise HTTPException(400, f"Missing {ex}")
    except (TypeError, ValueError):
        raise HTTPException(400, "Entry, stop and target must be numbers.")


@router.get("/api/timemachine/setup")
def timemachine_setup():
    """What the Time Machine page needs: symbols with a live price, and today's alerts that carry a plan."""
    e = engine()
    syms = {}
    for sym, m in (e.metrics or {}).items():
        if m.get("spot"):
            syms[sym] = round(float(m["spot"]), 2)
    names = set(syms)
    if e.uni:
        names |= set(e.uni.underlyings)
    if e.context and (e.context.mwpl or {}).get("symbols"):
        names |= set(e.context.mwpl["symbols"])
    names |= {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}
    idx = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
    order = idx + sorted(n for n in names if n not in idx)
    plans = []
    for a in list(e.alerts_mem)[:300]:
        d = a.get("data") or {}
        lv = d.get("setup_levels") or d.get("levels") or {}
        if a.get("direction") in ("up", "down") and lv.get("stop") and lv.get("target") and a.get("price"):
            plans.append({"id": a["id"], "ts": a.get("ts"), "sym": a["sym"], "title": a.get("title"), "direction": a["direction"],
                          "entry": a.get("price"), "stop": lv["stop"], "target": lv["target"], "rr": lv.get("rr")})
    return clean({"symbols": [{"sym": n, "ltp": syms.get(n)} for n in order], "alerts": plans[:40],
                  "now": e.clock.now().strftime("%H:%M") if getattr(e, "clock", None) else None,
                  "running": bool(e.running), "source": e.source})


def _tm(e, sym, entry, stop, target, direction, start=None):
    try:
        return e.tm.check(sym, entry=entry, stop=stop, target=target, direction=direction, start=start)
    except PermissionError as ex:
        raise HTTPException(401, str(ex))
    except ValueError as ex:
        raise HTTPException(400, str(ex))


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


@router.get("/api/live/context")
def live_context():
    e = engine()
    c = e.context.view()
    rows = getattr(e.world, "rows", {}) or {}
    g = []
    for x in c.get("global") or []:          # fresher prices from the World monitor when it has them
        r = rows.get(x.get("symbol"))
        if r and r.get("live_ts"):
            x = {**x, "last": r["last"], "chg_pct": r["chg_pct"],
                 "asof": dt.datetime.fromtimestamp(r["live_ts"], IST).strftime("%d %b %H:%M")}
        g.append(x)
    c["global"] = g
    c["server_ts"] = time.time()
    return clean(c)


@router.post("/api/live/context/refresh")
def live_context_refresh():
    e = engine()
    e.context.refresh_daily()
    e.context.refresh_global()
    e.context.poll_filings()
    return clean(e.context.view())


@router.get("/api/live/scorecard")
def live_scorecard(source: str | None = None):
    return clean(engine().scorecard_view(source))


@router.get("/api/live/briefs")
def live_briefs(day: str | None = None):
    return clean(engine().briefs_view(day))


@router.post("/api/live/briefs/{kind}")
def live_make_brief(kind: str):
    if kind not in ("pre", "post"):
        raise HTTPException(400, "Unknown brief")
    e = engine()
    if kind == "post" and not e.metrics:
        raise HTTPException(400, "Run the live scanner (or the simulator) first: the review needs a trading day.")
    if kind == "pre":
        e.context.refresh_daily()
        e.context.refresh_global()
    return clean(e.make_brief(kind, send=False))


def _world_with_india(e) -> dict:
    """World view with Indian prices replaced by the live feed when it is running."""
    w = e.world.view()
    if e.running and e.metrics:
        n = e.metrics.get("NIFTY")
        if n and n.get("spot"):
            w["nifty"] = {"last": n["spot"], "chg_pct": n.get("chg_pct"), "live": True, "source": e.source}
        for a in w.get("adr") or []:
            m = e.metrics.get(a["nse"])
            if m and m.get("spot") and a.get("implied_inr"):
                a["nse_inr"] = m["spot"]
                a["premium_pct"] = (a["implied_inr"] / m["spot"] - 1) * 100
                a["nse_live"] = True
    w["server_ts"] = time.time()
    return w


@router.get("/api/world")
def world_view():
    return clean(_world_with_india(engine()))


@router.post("/api/world/refresh")
def world_refresh():
    e = engine()
    for fn in (e.world.refresh_prices, e.world.refresh_live, e.world.refresh_news, e._world_calendar, e._world_results):
        try:
            fn()
        except Exception as ex:
            e.world.errors[fn.__name__] = str(ex)[:160]
    return clean(_world_with_india(e))


@router.get("/api/scenarios/status")
def scenarios_status():
    return clean(engine().scenarios.status())


@router.post("/api/scenarios/refresh")
def scenarios_refresh():
    e = engine()
    if not e.tokens.load():
        raise HTTPException(401, "Log in to Upstox first: the history comes from Upstox.")
    if e.scenarios.busy:
        return {"started": False, "note": "Already running."}
    threading.Thread(target=e._scenario_refresh, kwargs={"force": True}, daemon=True).start()
    return {"started": True}


@router.get("/api/insiders")
def insiders_view(days: int = 60, all: int = 0):
    return clean(engine().insiders.view(days=max(1, min(400, days)), all_rows=bool(all)))


@router.post("/api/insiders/refresh")
def insiders_refresh():
    e = engine()
    if e.insiders.running:
        return {"started": False}
    threading.Thread(target=e._insiders_refresh, daemon=True).start()
    return {"started": True}


@router.get("/api/health")
def health():
    """Open files and threads: if these keep climbing, something is leaking."""
    import threading as _t
    e = engine()
    return {**e.open_files(), "threads": _t.active_count(), "running": bool(e.running)}


@router.post("/api/ai/review")
async def ai_review(req: Request):
    """Claude's research review of a Time Machine test, the Strategy desk or the Scorecard."""
    from live.review import review
    b = await req.json()
    e = engine()
    try:
        return clean(await _run_blocking(review, e, str(b.get("topic") or ""), b.get("data"), ROOT / "data"))
    except PermissionError as ex:
        raise HTTPException(400, str(ex))
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    except RuntimeError as ex:
        raise HTTPException(502, str(ex))


@router.post("/api/ask")
async def ask_stockway(req: Request):
    """Ask Stockway: a question answered from the app's live data by the AI chosen in Live setup."""
    from live.ask import answer
    b = await req.json()
    e = engine()
    try:
        return clean(await _run_blocking(answer, e, str(b.get("q") or ""), b.get("history") or []))
    except PermissionError as ex:
        raise HTTPException(400, str(ex))
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    except RuntimeError as ex:
        raise HTTPException(502, str(ex))


@router.get("/api/ai/status")
def ai_status():
    """Which AI is set, whether Claude Code (your Pro plan) is installed on this Mac, and today's use."""
    from live.notify import find_claude_cli
    e = engine()
    c = e.cfg().get("ai") or {}
    return {"provider": e.ai.provider(), "label": e.ai.label(), "enabled": e.ai.enabled(), "claude_cli": find_claude_cli(),
            "model": c.get("pro_model") if e.ai.provider() == "claude_pro" else c.get("model"),
            "used": e.ai.used, "used_auto": e.ai.used_auto, "daily_limit": c.get("daily_limit"),
            "pro_auto_per_day": c.get("pro_auto_per_day") or 12, "last_error": e.ai.last_error}


@router.post("/api/ai/test")
async def ai_test():
    e = engine()
    if not e.ai.enabled():
        raise HTTPException(400, "Claude Code was not found on this Mac." if e.ai.provider() == "claude_pro" else "Add an API key first.")
    text = await _run_blocking(e.ai.ask, "Reply with exactly: Stockway connected", 20, False)
    if not text:
        raise HTTPException(502, e.ai.last_error or "No answer.")
    return {"ok": True, "text": text[:200], "by": e.ai.label()}


async def _run_blocking(fn, *args):
    import asyncio
    return await asyncio.get_running_loop().run_in_executor(None, lambda: fn(*args))


@router.get("/api/company/{sym}")
def company(sym: str):
    e = engine()
    sym = sym.upper()
    out = {"sym": sym, "insiders": e.insiders.for_symbol(sym)}
    try:
        out["results"] = e.insiders.results(sym)
    except Exception as ex:
        out["results"] = {"error": f"NSE did not answer: {str(ex)[:80]}"}
    return clean(out)


@router.get("/api/brain")
def brain_view():
    e = engine()
    h = e.brain.history()
    return clean({"latest": h[-1] if h else None, "history": list(reversed(h[-30:])), "ai": e.ai.enabled()})


@router.post("/api/brain/run")
def brain_run():
    e = engine()
    try:
        return clean(e.brain.run())
    except Exception as ex:
        raise HTTPException(500, f"Market brain failed: {ex}")


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
