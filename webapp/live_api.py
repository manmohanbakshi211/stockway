"""HTTP routes for the live scanner (mounted by webapp.server)."""
from __future__ import annotations

import datetime as dt
import html
import math
import re
import threading
import time

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from live.clock import IST
from radar.config import ROOT, load_config

from webapp.fastjson import FastJSONRoute     # noqa: E402  3.00: JSON made off the event loop

router = APIRouter(route_class=FastJSONRoute)
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
    from live import session as SESSION                # 2.48: the NSE calendar (holidays, special sessions)
    if SESSION.scanner_window(now) and not e.running:
        e.manual_stop_day = None
        e.start("upstox")
    elif e.status["state"] == "login_needed":
        e.status.update(state="stopped", message="Logged in. The scanner starts by itself before every NSE session.")
    return RedirectResponse("/#live")


# ----------------------------------------------------------------- Kite login (2.52: market data only, backup feed)
@router.get("/kite/login")
def kite_login():
    from live.kite import login_url
    c = engine().cfg()["live"]
    if not c.get("kite_api_key") or not c.get("kite_api_secret"):
        return _page("Add your Kite API key first",
                     "Open Live setup → Kite and paste the API key and secret from your Kite Connect app.", ok=False)
    return RedirectResponse(login_url(c["kite_api_key"]))


@router.get("/kite/callback")
def kite_callback(request_token: str | None = None, status: str | None = None, action: str | None = None):
    from live.kite import exchange_token
    e = engine()
    if status != "success" or not request_token:
        return _page("Kite login was cancelled", html.escape(status or "No login token came back."), ok=False)
    c = e.cfg()["live"]
    try:
        data = exchange_token(request_token, c["kite_api_key"], c["kite_api_secret"])
    except Exception as ex:
        msg = str(ex)
        hint = ("Check the API key and secret in Live setup (copy them again from developers.kite.trade), and that your "
                f"Kite app's redirect URL is exactly <code>{html.escape(c.get('kite_redirect_uri') or '')}</code>. "
                "Kite Connect needs the paid plan (₹500 / month) for live prices.")
        return _page("Kite login failed", html.escape(msg) + "<br><br>" + hint, ok=False)
    e.kite_tokens.save(data)
    e.log(f"Logged in to Kite as {data.get('user_name') or data.get('user_id')} (market data only: backup + cross-check)")
    if e.running and hasattr(e.feed, "_start_backup") and getattr(e.feed, "backup", None) is None:
        import threading
        threading.Thread(target=e.feed._start_backup, daemon=True, name="kite-start").start()
    return RedirectResponse("/#/connect")


@router.post("/api/kite/logout")
def kite_logout():
    e = engine()
    e.kite_tokens.clear()
    if getattr(e, "feed", None) is not None and getattr(e.feed, "backup", None) is not None:
        try:
            e.feed.backup.stop()
        except Exception:
            pass
        e.feed.backup = None
        e.feed.backup_status, e.feed.backup_msg = "login_needed", "Logged out of Kite"
    return {"ok": True}


@router.get("/api/live/feeds")
def live_feeds():
    """2.52: the main feed, the Kite backup, failover and the cross-check."""
    from live import feeds as F
    e = engine()
    lc = e.cfg()["live"]
    tok = e.kite_tokens.load()
    out = {"kite_keys": bool(lc.get("kite_api_key") and lc.get("kite_api_secret")), "kite_logged_in": bool(tok),
           "kite_user": tok.get("user") if tok else None, "kite_expires": tok.get("expires_at") if tok else None,
           "kite_redirect_uri": lc.get("kite_redirect_uri"), "failover": lc.get("feeds_failover") or F.cfg().get("failover"),
           "running": e.running}
    if getattr(e, "feed", None) is not None and hasattr(e.feed, "view"):
        out["router"] = e.feed.view()
    return clean(out)


@router.post("/api/live/logout")
def upstox_logout():
    e = engine()
    if e.running and e.source == "upstox":
        e.stop()
    e.tokens.clear()
    return {"ok": True}


# ----------------------------------------------------------------- control
@router.get("/api/strict")
def strict_view(top: int = 80):
    """2.77 Strict track: the strict test's latest result (live/strict.py) and the head-to-head since it began: the main
    account (Explore: every alert it takes), the Strict account (only alert types that passed) and the coin flips."""
    import json as _json
    from live import books as BK, strict as ST
    from live.bench import db_for
    e = engine()
    c = ST.load(e.data_dir)
    rep = c.get("rep") or {}
    first = None
    try:
        with open(e.data_dir / "strict_runs.jsonl") as f:
            first = (_json.loads(f.readline() or "{}").get("made") or "")[:10] or None
    except OSError:
        pass
    since = first or dt.datetime.now(IST).date().isoformat()
    lc = e.cfg()["live"]
    main_cap = float((lc.get("paper") or {}).get("capital") or 500000)
    bk = (getattr(e, "books", None).books.get("strict") if getattr(e, "books", None) else None)
    s_cap = float((bk.cfg.get("capital") if bk else None) or main_cap)
    pick = lambda src: [t for t in e.store.trades(source=src, limit=20000) if t["day"] >= since]
    rows = [{"id": "explore", "name": "Explore (main account)", **BK.stats(pick("upstox"), main_cap)},
            {"id": "strict", "name": "Strict account", **BK.stats(pick("upstox#strict"), s_cap)}]
    coin = {"n": 0}
    try:
        con = db_for(e.store).reader()
        try:
            r = con.execute("SELECT COUNT(*), SUM(net > 0), SUM(net), AVG(r_mult) FROM bench_trades WHERE source='upstox#coin' "
                            "AND style=? AND net IS NOT NULL AND day >= ?", (ST.cfg()["paper_style"], since)).fetchone()
            coin = {"n": r[0] or 0, "won_pct": round(r[1] / r[0] * 100, 1) if r[0] else None, "net": round(r[2] or 0, 2),
                    "avg": round((r[2] or 0) / r[0], 2) if r[0] else None, "avg_r": round(r[3], 3) if r[3] is not None else None}
        finally:
            con.close()
    except Exception:
        pass
    today = dt.datetime.now(IST).date().isoformat()
    strict_today = [a for a in list(getattr(e, "alerts_mem", []) or []) if (a.get("data") or {}).get("strict") and a.get("day") == today]
    setups = rep.get("setups") or []
    return clean({"made": rep.get("made"), "days": rep.get("days"), "first": rep.get("first"), "last": rep.get("last"),
                  "holdout_from": rep.get("holdout_from"), "t_bar": rep.get("t_bar"), "k": rep.get("k"), "bar": rep.get("bar"),
                  "yardstick": rep.get("yardstick"), "passing": rep.get("passing") or [], "n_setups": len(setups),
                  "setups": setups[:max(1, min(int(top), 500))], "since": since, "accounts": rows, "coin": coin,
                  "strict_today": len(strict_today), "rules": ST.cfg(), "running": bool(getattr(e, "_strict_running", False))})


@router.post("/api/strict/run")
def strict_run():
    """2.77: run the strict test now (it also runs by itself every evening after the close)."""
    e = engine()
    if getattr(e, "_strict_running", False):
        raise HTTPException(409, "The strict test is already running.")
    e._strict_running = True
    try:
        out = e.strict_run()
    finally:
        e._strict_running = False
    if out.get("error"):
        raise HTTPException(500, out["error"])
    return clean({"passing": out.get("passing"), "k": out.get("k"), "t_bar": out.get("t_bar"), "days": out.get("days")})


@router.get("/api/live/replay/days")
def replay_days():
    """2.76: the recorded days a replay can play, newest first."""
    from live import replay as RP
    e = engine()
    return clean({"days": RP.days(e.data_dir), "speeds": list(RP.SPEEDS), "default_speed": RP.DEFAULT_SPEED,
                  "running": e.source == "replay" and e.running, "replay": getattr(e, "replay", None)})


@router.post("/api/live/start")
async def live_start(req: Request):
    body = await req.json() if (await req.body()) else {}
    e = engine()
    source = body.get("source") or e.cfg()["live"]["source"]
    if source not in ("upstox", "sim", "replay"):
        raise HTTPException(400, "Unknown data source")
    if source == "replay":                           # 2.76: a recorded day through the same scanner (live/replay.py)
        from live import replay as RP
        day = str(body.get("day") or "")
        if day not in {d["day"] for d in RP.days(e.data_dir)}:
            raise HTTPException(400, "No finished recording of that day on this computer.")
        if e.running and e.source == "upstox":
            raise HTTPException(400, "The live scanner is on the real market now. Replay a day when it is off "
                                     "(after the close, or after Pause for today).")
        if e.running:
            e.stop(manual=False)
        e.replay_req = {"day": day, "speed": RP.speed_of(body.get("speed"))}
        return clean(e.start("replay"))
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
    t0 = time.perf_counter()
    e = engine()
    out = clean(e.snapshot(since))
    try:                                         # 2.72: how long the pages' main refresh takes (System & logins)
        ms = (time.perf_counter() - t0) * 1000
        st = getattr(e, "status", None)
        if isinstance(st, dict):
            prev = st.get("snapshot_ms")
            st["snapshot_ms"] = round(ms if prev is None else 0.8 * prev + 0.2 * ms)
    except Exception:
        pass
    return out


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
    if not d:                                    # 2.42: an answer, not a 404 (the page shows it; no console error)
        return {"closed": True, "message": "No live data for this symbol yet: the live scanner is not running."}
    return clean(d)


@router.get("/api/live/chart/{sym}")
def live_chart(sym: str, tf: int = 5, days: int = 3):
    e = engine()
    if tf not in (1, 3, 5, 15, 30, 60):
        raise HTTPException(400, "Timeframe must be 1, 3, 5, 15, 30 or 60 minutes")
    key = (sym.upper(), tf, max(1, min(10, days)))
    hit = _CHART_MEMO.get(key)
    if hit and not e.running and time.time() - hit[0] < 120:      # market closed: nothing changes, answer at once
        return hit[1]
    try:
        d = e.charts.payload(sym.upper(), tf, max(1, min(10, days)))
    except PermissionError as ex:
        raise HTTPException(401, str(ex))
    if not d:
        raise HTTPException(404, "No price history found for this symbol yet (Yahoo or Upstox didn't answer).")
    out = clean(d)
    if not e.running:
        if len(_CHART_MEMO) > 40:
            _CHART_MEMO.clear()
        _CHART_MEMO[key] = (time.time(), out)
    return out


_CHART_MEMO: dict = {}


@router.get("/api/live/rider/{sym}")
def live_rider(sym: str):
    """2.86: Trend Rider's readings for one symbol (live/rider.py): one row per closed 5-minute bar for the chart's
    sub-charts (time on the chart's IST axis) and the latest reading with the decision for the side card. The card
    and the sub-charts read the same numbers. Information only; the setup is on trial."""
    e = engine()
    rd = getattr(e, "rider", None)
    if rd is None:
        return {"ready": False, "message": "Trend Rider is not loaded."}
    try:
        v = rd.view(sym.upper())
    except Exception as ex:
        return {"ready": False, "message": f"Trend Rider reading failed: {ex}"}
    from live.chartdata import IST_SHIFT
    rows = [{**r, "time": int(r["t"]) + IST_SHIFT} for r in (v.get("series") or []) if r.get("t") is not None]
    c = v.get("config") or {}
    out = {"ready": bool(rows or v.get("read")), "sym": sym.upper(), "series": rows, "read": v.get("read"),
           "limits": {"trend_day": (c.get("trend_day") or {}).get("min"), "breakout": (c.get("breakout") or {}).get("min_quality"),
                      "chase": (c.get("chase") or {}).get("max"), "families": (c.get("agreement") or {}).get("min_families"),
                      "mvd": (c.get("move_vs_decay") or {}).get("min"), "trad": (c.get("tradability") or {}).get("min"),
                      "windows": c.get("windows"),
                      "ema_counted": bool((c.get("ema_videos") or {}).get("count_in_agreement")),
                      "ema_trigger": bool((c.get("ema_videos") or {}).get("as_trigger")),
                      "conf_min": (c.get("confidence") or {}).get("min"), "conf_strong": (c.get("confidence") or {}).get("strong"),
                      "conf_used": bool((c.get("confidence") or {}).get("use_in_decision"))},
           "words": __import__("lab.rider", fromlist=["FACTOR_WORDS"]).FACTOR_WORDS,
           "running": bool(getattr(e, "running", False))}
    if not out["ready"]:
        out["message"] = ("Builds at each closed 5-minute bar while the live scanner runs (from 09:30)." if out["running"]
                          else "The live scanner is not running: Trend Rider reads every closed 5-minute bar once it is.")
    return clean(out)


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
    try:                                       # all timeframes: trend per timeframe and where they agree
        mv = _mtf().view(sym)
        facts["timeframes"] = {"trend": {t["tf"]: [t["trend"], t["structure"]] for t in mv["trend"]}, "summary": mv["summary"],
                               "confluence_zones": [{k: z[k] for k in ("lo", "hi", "tfs", "dist_pct")} for z in mv["confluence"][:4]]}
    except Exception:
        pass
    prompt = (SYSTEM_STYLE + "You are reading a live 5-minute chart like a senior prop trader, with the weekly, daily, "
              "4-hour, 1-hour and 15-minute trends and the multi-timeframe confluence zones. In 5 short bullets: "
              "1) structure and trend across timeframes, 2) where price sits vs the key levels and zones, 3) the best trade setup now with entry, stop and "
              "target levels (or 'no trade' and why), 4) what would change your mind, 5) one risk. Use only the numbers given.\n\n"
              + _compact_json(facts))
    text = e.ai.ask(prompt, 450, auto=False, task="chart_read")
    if not text:
        raise HTTPException(502, e.ai.last_error or "AI did not answer.")
    return {"text": text}


def _mtf():
    e = engine()
    if getattr(e, "mtf", None) is None:
        from live.mtf import MTF
        e.mtf = MTF(e)
    return e.mtf


def _fortune():
    e = engine()
    if getattr(e, "fortune", None) is None:
        from live.fortune import Fortune
        e.fortune = Fortune(e)
    return e.fortune


@router.get("/api/live/newsradar")
def live_newsradar():
    """2.64: the fast AI's read of every headline, graded 30 minutes later against the keyword tone (shadow)."""
    r = getattr(engine(), "radar_news", None)
    return clean(r.view() if r else {"calls": 0, "note": "Starts with the live scanner."})


@router.get("/api/live/eval/{sym}")
def live_eval(sym: str, minutes: int = 45):
    """2.61 engine evaluation: the symbol's alerts of the last `minutes` with the multiverse's verdict on each (signals
    like it: how they did on the recorded days, the best way to trade them), and the strongest proven side now."""
    import time as _t
    e = engine()
    sym = sym.upper()
    now = _t.time()
    items = []
    for a in list(getattr(e, "alerts_mem", []) or []):
        if a.get("sym") != sym or a.get("direction") not in ("up", "down") or now - float(a.get("ts") or 0) > minutes * 60:
            continue
        mv = (a.get("data") or {}).get("mv")
        items.append({"id": a.get("id"), "ts": a.get("ts"), "kind": a.get("kind"), "title": a.get("title"),
                      "direction": a["direction"], "mv": mv})
    side = {}
    for d in ("up", "down"):
        best = [x["mv"]["best"] for x in items if x["direction"] == d and x.get("mv") and x["mv"].get("best")]
        if best:
            side[d] = max(best, key=lambda b: b.get("lcb") if b.get("lcb") is not None else -1e9)
    lean = None
    up, dn = (side.get("up") or {}).get("lcb"), (side.get("down") or {}).get("lcb")
    if up is not None and up > 0 and (dn is None or up > dn):
        lean = "up"
    elif dn is not None and dn > 0 and (up is None or dn > up):
        lean = "down"
    return clean({"sym": sym, "minutes": minutes, "items": items[:20], "side": side, "lean": lean})


@router.get("/api/live/fortune/{sym}")
async def live_fortune(sym: str, tf: str = "15m"):
    """2.36 Fortune chart: ranges for the forming candle and the next few of a timeframe, from the symbol's own history
    (every past candle cut at the same point and compared with how it ended), checked live. Information only."""
    from live.mtf import MAIN_TFS
    if tf not in MAIN_TFS:
        raise HTTPException(400, f"Timeframe must be one of {', '.join(MAIN_TFS)}")
    out = await _run_blocking(_fortune().compute, _mtf(), sym.upper(), tf)
    try:                                       # 2.39: the shadow next candle from the smaller ones + the smart report
        _mtf()
        out["next"] = engine().next_candle().view(sym.upper(), tf)
    except Exception as ex:
        out["next"] = {"cascade": False, "error": str(ex)[:160]}
    return clean(out)


@router.get("/api/live/mtf/{sym}")
async def live_mtf(sym: str, tfs: str | None = None):
    """All timeframes of one symbol: candles, markings, higher-timeframe levels, confluence and the trend strip."""
    m = _mtf()
    return clean(await _run_blocking(m.view, sym.upper(), tfs.split(",") if tfs else None))


def _mind():
    e = engine()
    if getattr(e, "mind", None) is None:
        from live.mindgame import MindGame
        e.mind = MindGame(e, getattr(e, "store", None), getattr(e, "source", None) or "upstox", getattr(e, "log", print))
    return e.mind


@router.get("/api/live/mind/{sym}")
async def live_mind(sym: str):
    """2.70 MindGame (on trial): the buyer / seller scene on 1m / 3m / 5m, the bigger timeframes' scenes (from the
    timeframe charts), and one short combined message: AI (fast tier, own allowance) when ready, else the system's."""
    return clean(await _run_blocking(_mind().view, sym.upper()))


@router.get("/api/live/mind_record")
def live_mind_record(days: int = 60):
    """Every MindGame bullet with its record (graded right / wrong / flat), per timeframe; none sends an alert."""
    from live import mindgame as MG
    mg = _mind()
    rows = mg.book.table(max(1, min(365, days))) if mg.book is not None else []
    return clean({"rows": rows, "trust": MG.cfg()["trust"], "days": days,
                  "note": "On trial: a bullet joins the alerts only when its record is positive (Mandeep, 12 Nov 2026)."})


@router.get("/api/live/candles/{sym}")
async def live_candles(sym: str, tf: str = "5m", before: float | None = None, tail: int | None = None):
    """Main chart bars (2.29): long history per timeframe; `before` (epoch seconds) loads older bars for scroll-back."""
    from live.mtf import MAIN_TFS
    from live.secbars import SB, SEC_TFS
    if tf in SEC_TFS:                                   # 2.69: seconds candles (today, from the app's own prices)
        if before:
            return {"sym": sym.upper(), "tf": tf, "candles": [], "volume": [], "more": False, "has_volume": False}
        SB.watch[sym.upper()] = time.time()            # 2.70.1: opening the chart starts this symbol's per-second prices
        try:                                            # (only the indices were kept by default: a stock's seconds chart
            from live.stream import frame               # stayed empty, as its stream never opened on an empty chart)
            frame(engine(), sym.upper(), tf)            # its current price, now
        except Exception:
            pass
        d = SB.candles(sym.upper(), tf)
        if tail:
            d["candles"] = d["candles"][-int(tail):]
        if not d["candles"] and not tail:
            d["message"] = ("No seconds prices yet: they are kept while the live scanner runs (today only); this symbol's "
                            "collection starts now.")
        return clean(d)
    if tf not in MAIN_TFS:
        raise HTTPException(400, f"Timeframe must be one of {', '.join(list(SEC_TFS) + list(MAIN_TFS))}")
    d = await _run_blocking(_mtf().candles, sym.upper(), tf, before, 1500, tail)
    if not d["candles"] and not before and not tail:
        raise HTTPException(404, "No price history for this symbol yet (Dhan, Yahoo and the app's own history had none).")
    return clean(d)


@router.websocket("/ws/ticks")
async def ws_ticks(ws: WebSocket):
    """2.51: an open chart's live stream (live/stream.py). The browser sends {"sym", "tf"} (again to switch); the app sends
    each new price (with its exchange time and the forming candle) as soon as it has it. Reads the app's own prices only."""
    import asyncio
    import json as _json
    import time as _t
    from live import stream as ST
    c = ST.cfg()
    await ws.accept()
    if ST.OPEN["n"] >= int(c["max_streams"]):
        await ws.send_json({"error": f"too many open charts ({ST.OPEN['n']}); this one refreshes every few seconds instead"})
        await ws.close()
        return
    ST.OPEN["n"] += 1
    sub = {"sym": None, "tf": "5m"}

    async def reader():
        while True:
            try:
                m = _json.loads(await ws.receive_text())
            except (ValueError, TypeError):
                continue
            if isinstance(m, dict):
                sub["sym"] = str(m.get("sym") or "").upper()[:30] or None
                sub["tf"] = str(m.get("tf") or "5m")[:4]
    task = asyncio.create_task(reader())
    last, beat = None, 0.0
    try:
        while not task.done():
            await asyncio.sleep(max(0.05, float(c["every_ms"]) / 1000))
            e = engine()
            try:
                f = ST.frame(e, sub["sym"], sub["tf"])
            except Exception:
                f = None
            now = _t.time()
            if f:
                sig = (f["sym"], f["tf"], f["p"], f.get("tt"), _json.dumps(f.get("candle"), sort_keys=True))
                if sig != last:
                    await ws.send_json(f)
                    ST.OPEN["served"] += 1
                    last, beat = sig, now
            if now - beat >= float(c["heartbeat_s"]):
                await ws.send_json({"hb": now, "running": bool(getattr(e, "running", False)), "sym": sub["sym"]})
                beat = now
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        task.cancel()
        ST.OPEN["n"] = max(0, ST.OPEN["n"] - 1)


@router.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    """2.72 (Mandeep: no delays anywhere): every open page gets new alerts and paper trade changes pushed at once
    (~0.2 s) instead of waiting for its next refresh (1-4 s plus the snapshot's own time). The browser sends
    {"since": last alert id}. Reads the app's own memory only."""
    import asyncio
    import json as _json
    import time as _t
    from live import paper as PP
    from live import stream as ST
    c = ST.cfg()
    await ws.accept()
    if ST.OPEN.get("e", 0) >= int(c.get("max_event_streams") or 8):
        await ws.send_json({"error": "too many open pages"})
        await ws.close()
        return
    ST.OPEN["e"] = ST.OPEN.get("e", 0) + 1
    sub = {"since": None}

    async def reader():
        while True:
            try:
                m = _json.loads(await ws.receive_text())
            except (ValueError, TypeError):
                continue
            if isinstance(m, dict) and isinstance(m.get("since"), (int, float)):
                sub["since"] = int(m["since"])
    task = asyncio.create_task(reader())
    pv, beat = PP.VER[0], 0.0
    try:
        while not task.done():
            await asyncio.sleep(max(0.1, float(c.get("events_ms") or 200) / 1000))
            out = {}
            try:
                mem = list(getattr(engine(), "alerts_mem", None) or ())[:50]
            except Exception:
                mem = []
            if sub["since"] is None:
                sub["since"] = mem[0]["id"] if mem else 0
            new = [a for a in mem if a.get("id", 0) > sub["since"]][:20]
            if new:
                out["alerts"] = clean(new)
                sub["since"] = max(a["id"] for a in new)
            if PP.VER[0] != pv:
                pv = out["pv"] = PP.VER[0]
            now = _t.time()
            if out:
                await ws.send_json(out)
                beat = now
            elif now - beat >= float(c["heartbeat_s"]):
                await ws.send_json({"hb": now})
                beat = now
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        task.cancel()
        ST.OPEN["e"] = max(0, ST.OPEN.get("e", 1) - 1)


@router.websocket("/ws/quotes")
async def ws_quotes(ws: WebSocket):
    """2.71: live quotes of any instrument keys (the browser sends {"keys": [...]}: open paper trades' options, the option
    chain's streamed strikes); the app sends [ltp, bid, ask, oi, time] of those that changed, ~every 0.2 s. Reads the
    app's own live prices only; nothing new connects to a broker."""
    import asyncio
    import json as _json
    import time as _t
    from live import stream as ST
    c = ST.cfg()
    await ws.accept()
    if ST.OPEN.get("q", 0) >= int(c.get("max_quote_streams") or 6):
        await ws.send_json({"error": "too many open pages streaming quotes"})
        await ws.close()
        return
    ST.OPEN["q"] = ST.OPEN.get("q", 0) + 1
    sub = {"keys": []}

    async def reader():
        while True:
            try:
                m = _json.loads(await ws.receive_text())
            except (ValueError, TypeError):
                continue
            if isinstance(m, dict) and isinstance(m.get("keys"), list):
                sub["keys"] = [str(x)[:60] for x in m["keys"]][: int(c.get("max_quote_keys") or 300)]
    task = asyncio.create_task(reader())
    last, beat = {}, 0.0
    try:
        while not task.done():
            await asyncio.sleep(max(0.1, float(c.get("quotes_ms") or 200) / 1000))
            try:
                q = ST.quotes(engine(), sub["keys"])
            except Exception:
                q = {}
            ch = {k: v for k, v in q.items() if last.get(k) != v}
            now = _t.time()
            if ch:
                await ws.send_json({"q": ch})
                last.update(ch)
                beat = now
            elif now - beat >= float(c["heartbeat_s"]):
                await ws.send_json({"hb": now})
                beat = now
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        task.cancel()
        ST.OPEN["q"] = max(0, ST.OPEN.get("q", 1) - 1)


@router.websocket("/ws/prices")
async def ws_prices(ws: WebSocket):
    """2.69: the Live page's prices as fast as the chart's (the browser sends {"syms": [...]}; the app sends the prices
    that changed, ~every 0.2 s). Reads the app's own prices only; nothing new connects to a broker."""
    import asyncio
    import json as _json
    import time as _t
    from live import stream as ST
    c = ST.cfg()
    await ws.accept()
    if ST.OPEN.get("p", 0) >= int(c.get("max_price_streams") or 6):
        await ws.send_json({"error": "too many open Live pages"})
        await ws.close()
        return
    ST.OPEN["p"] = ST.OPEN.get("p", 0) + 1
    sub = {"syms": []}

    async def reader():
        while True:
            try:
                m = _json.loads(await ws.receive_text())
            except (ValueError, TypeError):
                continue
            if isinstance(m, dict) and isinstance(m.get("syms"), list):
                sub["syms"] = [str(x).upper()[:30] for x in m["syms"]][:250]
    task = asyncio.create_task(reader())
    last, beat = {}, 0.0
    try:
        while not task.done():
            await asyncio.sleep(max(0.1, float(c.get("prices_ms") or 200) / 1000))
            try:
                px = ST.prices(engine(), sub["syms"])
            except Exception:
                px = {}
            ch = {k: v for k, v in px.items() if last.get(k) != v}
            now = _t.time()
            if ch:
                await ws.send_json({"px": ch})
                last.update(ch)
                beat = now
            elif now - beat >= float(c["heartbeat_s"]):
                await ws.send_json({"hb": now})
                beat = now
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        task.cancel()
        ST.OPEN["p"] = max(0, ST.OPEN.get("p", 1) - 1)


@router.get("/api/live/mtf/{sym}/overlays")
def live_mtf_overlays(sym: str):
    return {"overlays": _mtf().overlays(sym.upper())}


@router.get("/api/live/mtf/{sym}/overlay")
async def live_mtf_overlay(sym: str, id: str, days: int = 10):
    """Where a strategy would have fired on the last `days` sessions (apply on the chart; remove = just stop drawing)."""
    if ":" not in id:
        raise HTTPException(400, "Unknown strategy")
    return clean(await _run_blocking(_mtf().overlay, sym.upper(), id, max(1, min(20, days))))


def _lens():
    e = engine()
    if getattr(e, "lens", None) is None:
        from live.lens import Lens
        e.lens = Lens(e)
    return e.lens


@router.get("/api/live/lens/{sym}")
async def live_lens(sym: str):
    """Options lens for one symbol: straddle, call vs put premium, IV, PCR, PE-CE OI change, OI by strike.
    2.48: a failure is shown on the page as a message (was 'Request failed (500)')."""
    try:
        return clean(await _run_blocking(_lens().symbol, sym.upper()))
    except Exception as ex:
        engine().log(f"Options lens {sym}: {ex}")
        return {"sym": sym.upper(), "series": {}, "strikes": [], "empty": f"Options lens could not read the recording: {ex}"}


@router.get("/api/live/lens_market")
async def live_lens_market():
    """Market lens: breadth through the day, futures buildup heatmap, IV grid, sectors, contributors, FII / DII."""
    try:
        return clean(await _run_blocking(_lens().market))
    except Exception as ex:
        engine().log(f"Market lens: {ex}")
        return {"session": None, "breadth": [], "buildup": {}, "iv_grid": [], "sectors": [], "contributors": {}, "flows": {},
                "error": f"Market lens could not read the recording: {ex}"}


_ALERTS_DAY: dict = {}          # 2.99: the Alerts page's day of alerts, until a new alert arrives (20 s at most)
_SHADOW_IDS: dict = {}          # 2.99: today's shadow Focus picks, until a new alert arrives (60 s at most)


def _alerts_fp(e, day):
    try:
        r = e.store._read("SELECT MAX(id), COUNT(*) FROM alerts WHERE day=?", (day,), one=True)
        return (r[0], r[1]) if r else None
    except Exception:
        return None


@router.get("/api/live/alerts")
def live_alerts(day: str | None = None, sym: str | None = None, min_sev: int = 1, source: str | None = None, limit: int = 1000,
                focus: bool = False, view: str | None = None, repeats: bool = False):
    """view (2.29, the Alerts page): act = act-now alerts; focus = Focus plus the other important ones (severity 2+,
    not demoted); all = everything. Counts for all three come back with any of them."""
    e = engine()
    day = day or dt.datetime.now(IST).date().isoformat()
    if day == "latest":
        days = e.store.alert_days(1)
        day = days[0]["day"] if days else None
    if view in ("act", "focus", "all", "shadow"):
        from live.dayreport import _focus
        from webapp import perf as _perf
        mark = _perf.Marks("alerts")                     # 2.95: which piece of the Alerts page is slow
        fp = _alerts_fp(e, day) if day else None
        key = (day, sym.upper() if sym else None, source, fp)
        hit = _ALERTS_DAY.get("v")
        if fp is not None and hit and hit[0] == key and time.time() - hit[1] < 20:
            al = hit[2]                                  # 2.99: ~5,000 alerts parsed once per new alert, not per call
        else:
            al = e.store.alerts(day=day, sym=sym.upper() if sym else None, min_sev=1, source=source, limit=6000)
            if fp is not None:
                _ALERTS_DAY["v"] = (key, time.time(), al)
        mark("read the day's alerts")
        pick = {"act": lambda a: a["severity"] >= 3,
                "focus": lambda a: _focus(a) or (a["severity"] >= 2 and not (a.get("data") or {}).get("muted")),
                "all": lambda a: True}
        from live import repeats as _rep                 # 3.05: one alert per stock per 30 min shown (all still saved)
        rep = _rep.mark(al) if not sym else set()       # one symbol asked for: show its every alert
        hidden = {k: sum(1 for a in al if a["id"] in rep and f(a)) for k, f in pick.items()}
        if not repeats and rep:
            pick = {k: (lambda a, f=f: f(a) and a["id"] not in rep) for k, f in pick.items()}
        mark("repeats")
        shadow = None
        try:                                     # 2.37: what the main shadow Focus version picked (live) or would have (replayed)
            from live import focuslab
            fl = focuslab.load()
            if fl["main"] and day:
                src = source or e.source or e.cfg()["live"].get("source") or "upstox"
                sk = (src, day, fl["main"], fp)
                sh = _SHADOW_IDS.get("v")
                if fp is not None and sh and sh[0] == sk and time.time() - sh[1] < 60:
                    ids = sh[2]                          # 2.99: was worked out again on every call (~2.4 s)
                else:
                    ids = focuslab.picks(e.store, src, day, fl["main"], e.cfg().get("live") or {},
                                         today=e.clock.now().date().isoformat() if getattr(e, "clock", None) else None)
                    if fp is not None:
                        _SHADOW_IDS["v"] = (sk, time.time(), ids)
                pick["shadow"] = lambda a: a["id"] in ids
                shadow = {"version": fl["main"], "label": fl["versions"][fl["main"]]["label"]}
        except Exception:
            shadow = None
        mark("shadow Focus picks")
        if view == "shadow" and "shadow" not in pick:
            view = "focus"
        counts = {k: sum(1 for a in al if f(a)) for k, f in pick.items()}
        out = [a for a in al if pick[view](a)][:max(1, min(int(limit), 2000))]
        for a in out:                              # 2.50 (speed): the shadow Focus challengers' notes are read by nothing here
            if isinstance(a.get("data"), dict) and "focus_ch" in a["data"]:
                a["data"] = {k: v for k, v in a["data"].items() if k != "focus_ch"}
        days_ = e.store.alert_days(60)
        mark("days list")
        res = clean({"day": day, "alerts": out, "total": len(al), "counts": counts, "view": view, "days": days_, "shadow": shadow,
                     "repeats": {"shown": bool(repeats), "hidden": hidden.get(view, 0), "window_min": _rep.cfg().get("window_min")}})
        mark("counts and packing")
        return res
    al = e.store.alerts(day=day, sym=sym.upper() if sym else None, min_sev=min_sev, source=source,
                        limit=6000 if focus else max(1, min(int(limit), 6000)))
    total = len(al)
    if focus:
        from live.dayreport import _focus
        al = [a for a in al if _focus(a)][:1000]
    return clean({"day": day, "alerts": al, "total": total, "days": e.store.alert_days(60)})


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


INDEX_SYMS = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY")
FALLBACK_STOCKS = ("RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "AXISBANK", "KOTAKBANK", "LT", "ITC",
                   "BHARTIARTL", "BAJFINANCE", "HINDUNILVR", "MARUTI", "M&M", "TATAMOTORS", "TATASTEEL", "SUNPHARMA",
                   "TITAN", "ULTRACEMCO", "ASIANPAINT", "WIPRO", "HCLTECH", "ADANIENT", "ADANIPORTS", "NTPC", "POWERGRID",
                   "ONGC", "COALINDIA", "JSWSTEEL", "HINDALCO", "BAJAJFINSV", "TECHM", "DRREDDY", "CIPLA", "EICHERMOT",
                   "HEROMOTOCO", "BPCL", "INDUSINDBK", "GRASIM", "DLF", "BEL", "HAL", "VEDL", "PNB", "BANKBARODA", "ZOMATO")


def _symbol_names(e) -> list:
    """Every symbol the app knows: the live universe, else NSE's F&O list, else a built-in list of big names."""
    names = set(e.metrics or {})
    if e.uni:
        names |= set(e.uni.underlyings)
    try:
        if e.context and (e.context.mwpl or {}).get("symbols"):
            names |= set(e.context.mwpl["symbols"])
    except Exception:
        pass
    if len(names - set(INDEX_SYMS)) < 10:
        names |= set(FALLBACK_STOCKS)
    return sorted(names | set(INDEX_SYMS))


@router.get("/api/symbols")
def symbols():
    """Indices and F&O stocks for the symbol pickers (works before the scanner starts)."""
    e = engine()
    names = _symbol_names(e)
    return {"indices": [x for x in INDEX_SYMS], "stocks": [x for x in names if x not in INDEX_SYMS]}


@router.post("/api/timemachine/scan")
async def timemachine_scan(req: Request):
    """Find a better stop and target for an entry: a grid of 24 plans, each replayed 4,000 times and on the real past."""
    b = await req.json()
    e = engine()
    try:
        return clean(await _run_blocking(lambda: e.tm.scan(str(b["sym"]).upper().strip(), entry=float(b["entry"]),
                                                           direction=b.get("direction") or "up", start=(b.get("start") or None))))
    except KeyError as ex:
        raise HTTPException(400, f"Missing {ex}")
    except PermissionError as ex:
        raise HTTPException(401, str(ex))
    except ValueError as ex:
        raise HTTPException(400, str(ex))


@router.get("/api/price/{sym}")
def last_price(sym: str):
    """Live price, or the last close when the live feed is off (from saved history or Yahoo)."""
    e = engine()
    sym = sym.upper()
    m = (e.metrics or {}).get(sym)
    if m and m.get("spot"):
        return {"sym": sym, "price": round(float(m["spot"]), 2), "live": True}
    rows = e.charts.offline_rows(sym) if e.charts else []
    if not rows:
        raise HTTPException(404, f"No price for {sym} yet.")
    return {"sym": sym, "price": round(float(rows[-1][4]), 2), "live": False,
            "as_of": dt.datetime.fromtimestamp(rows[-1][0], IST).strftime("%d %b %H:%M")}


@router.get("/api/timemachine/setup")
def timemachine_setup():
    """What the Time Machine page needs: symbols with a live price, and today's alerts that carry a plan."""
    e = engine()
    syms = {}
    for sym, m in (e.metrics or {}).items():
        if m.get("spot"):
            syms[sym] = round(float(m["spot"]), 2)
    names = set(syms) | set(_symbol_names(e))
    idx = list(INDEX_SYMS)
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


STALE_MIN = 5          # an alert older than this can no longer be paper-traded (its price is gone)


@router.post("/api/live/alerts/{alert_id}/paper")
async def live_paper_from_alert(alert_id: int, request: Request):
    """Paper trade from an alert. The risk gate is re-checked now: hard rules (time window, day loss limit, ban,
    duplicate) always refuse; other BLOCK reasons need an explicit override, which takes 1 lot and is tagged."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    override = bool((body or {}).get("override"))
    e = engine()
    if not e.running or not e.paper:
        raise HTTPException(400, "Start the live scanner first.")
    a = next((x for x in e.alerts_mem if x["id"] == alert_id), None) or e.store.alert(alert_id)
    if not a or not a.get("option"):
        raise HTTPException(400, "This alert has no option idea to paper trade.")
    now = e.clock.now()
    age = (now.timestamp() - float(a.get("ts") or 0)) / 60
    if age > STALE_MIN:
        raise HTTPException(400, f"This signal is {age:.0f} minutes old: the price has moved on. Take a fresh signal.")
    a = {**a, "data": {**(a.get("data") or {})}}
    risk = e.risk_for(a, now)
    a["data"]["risk"] = risk
    if risk["verdict"] != "PASS":
        if risk.get("hard"):
            raise HTTPException(400, {"message": "Risk check: BLOCK. " + " ".join(risk["hard"]) + " No paper trade (hard rule).",
                                      "risk": risk})
        if not override:
            raise HTTPException(409, {"message": "Risk check: BLOCK. " + " ".join(risk["reasons"]), "risk": risk, "can_override": True})
    tid = e.paper.try_open(a, e.state, now, manual=True, override=override and risk["verdict"] != "PASS")
    if not tid:
        raise HTTPException(400, f"No live price for {a['option'].get('label', 'the option')} right now.")
    return {"ok": True, "id": tid, "override": risk["verdict"] != "PASS", "lots": 1 if risk["verdict"] != "PASS" else risk.get("lots")}


@router.post("/api/live/paper/{trade_id}/close")
def live_paper_close(trade_id: int):
    e = engine()
    if not e.paper:
        raise HTTPException(400, "Start the live scanner first.")
    r = e.paper.close_manual(trade_id, e.state, e.clock.now())
    if not r:
        raise HTTPException(404, "Trade not open")
    return clean(r)


@router.post("/api/live/paper/close_all")
def live_paper_close_all():
    e = engine()
    if not e.paper:
        raise HTTPException(400, "Start the live scanner first.")
    done = e.paper.close_all(e.state, e.clock.now())
    return {"ok": True, "closed": len(done), "net": round(sum(t["net"] for t in done), 2)}


def _book_live(bid: str):
    """2.69: a running extra paper account (Best only, 3% a day, Trailing, Ghanshyam...) for its Exit buttons."""
    e = engine()
    bs = getattr(e, "books", None)
    if not e.paper or bs is None:
        raise HTTPException(400, "Start the live scanner first.")
    if bid not in bs.books:
        raise HTTPException(404, f"No paper account '{bid}'")
    return e, bs.books[bid]


@router.post("/api/live/paper/books/{bid}/close_all")
def live_book_close_all(bid: str):
    """2.69 (Mandeep: "har section mein exit all"): close every open trade of one extra paper account. Paper only."""
    e, bk = _book_live(bid)
    done = bk.close_all(e.state, e.clock.now())
    return {"ok": True, "book": bid, "closed": len(done), "net": round(sum(t["net"] for t in done), 2)}


@router.post("/api/live/paper/books/{bid}/{trade_id}/close")
def live_book_close(bid: str, trade_id: int):
    """2.69: close one open trade of an extra paper account at the live price. Paper only."""
    e, bk = _book_live(bid)
    r = bk.close_manual(trade_id, e.state, e.clock.now())
    if not r:
        raise HTTPException(404, "Trade not open")
    return clean(r)


# ----------------------------------------------------------------- 2.98: day calendar and AI day review (live/daybook.py)
def _open_today(e) -> dict:
    """{account: open P&L} of every paper account now (the calendar's and the day's 'today')."""
    try:
        t = live_paper_total()
        return {r["id"]: r.get("open_pnl") or 0 for r in t.get("accounts") or [] if "error" not in r}
    except Exception:
        return {}


@router.get("/api/live/calendar")
def live_calendar(month: str | None = None):
    """Every day of a month green / red: the net after charges of every paper account, trades, won, the Nifty's day."""
    from live import daybook
    e = engine()
    today = e.session_day().isoformat()
    month = (month or today[:7])[:7]
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(400, "month is YYYY-MM")
    extra = {today: _open_today(e)} if month == today[:7] and e.paper else None
    out = daybook.calendar(e.store, e.data_dir, e._real_source(), month, today=extra)
    out.update(today=today, times=daybook.cfg().get("times"))
    return clean(out)


@router.get("/api/live/day/{day}")
def live_day(day: str, acct: str | None = None):
    """One day: the market, every trade of every account with its segment and why it made or lost money, what was
    missed, the lessons and the AI reviews."""
    from live import daybook
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise HTTPException(400, "day is YYYY-MM-DD")
    from webapp import memo
    e = engine()
    accts = None
    if acct:                                             # 3.03: only these accounts (Pro's calendar)
        names = {"main", *daybook.book_names()}
        accts = sorted({a.strip() for a in acct.split(",") if a.strip()})
        if not accts or any(a not in names for a in accts):
            raise HTTPException(400, f"unknown account (one of {', '.join(sorted(names))})")
    live_ = day == e.session_day().isoformat() and e.paper
    try:                                                 # 3.00: the same answer until a trade of that day changes
        fp = list(e.store._read("SELECT COUNT(*), COALESCE(SUM(net), 0), MAX(id), COALESCE(SUM(exit), 0) FROM paper "
                                "WHERE day=?", (day,), one=True) or [])
    except Exception:
        fp = None
    kw = {"accounts": accts} if accts else {}
    if fp is not None and not live_:
        b = memo.memo(f"daybook|{day}|{','.join(accts or [])}", fp, lambda: e.day_book(day, **kw), ttl=900)
    else:
        b = e.day_book(day, open_pnl=_open_today(e) if live_ else None, **kw)
    b = dict(b)
    b["reviews"] = daybook.reviews(e.data_dir, day)
    b["reviewing"] = bool(getattr(e, "_day_review_lock", None) and e._day_review_lock.locked())
    return clean(b)


SEG_BOOK = {"index": "pro_index", "stock": "pro_stocks", "strict": "pro_stocks_strict",      # 3.07
            "v2index": "pro2_index", "v2stock": "pro2_stocks", "v2early": "pro2_early"}            # 3.08: Pro v2


@router.get("/api/live/pro")
def live_pro(seg: str = "index", day: str | None = None):
    """3.01 Stockway Pro v1: one segment's strong / watch signals of the day (Trend Rider scored by Pro), its paper
    account, which checks lined up and worked (graded signals, last 20 market days) and the learned weights."""
    from live import pro
    from webapp import memo
    if seg not in SEG_BOOK:
        raise HTTPException(400, "seg is one of " + ", ".join(SEG_BOOK))
    if day and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise HTTPException(400, "day is YYYY-MM-DD")
    e = engine()
    src = e._real_source() if hasattr(e, "_real_source") else (e.source or "upstox")
    if not day:
        try:
            r = e.store._read("SELECT MAX(day) FROM trial_alerts WHERE source=? AND kind='trend_rider'", (src,), one=True)
            day = (r[0] if r and r[0] else None) or e.session_day().isoformat()
        except Exception:
            day = e.session_day().isoformat()
    c = pro.cfg()
    st = pro._state(e.data_dir)
    fac = memo.memo(f"pro_factors|{src}|{day}", None, lambda: pro.factors(e.store, src, 20, day), ttl=60)
    book = SEG_BOOK[seg]
    from live import books as _bk                         # 3.07: an account's must-have / must-not checks
    pick = ((_bk.cfg().get("books") or {}).get(book) or {}).get("pick") or {}
    gates = {k: pick[k] for k in ("need_with", "not_with") if pick.get(k)}
    v2 = {}
    if seg.startswith("v2"):                              # 3.08: Pro v2: its checks, the Vote Lab's names, the comparison
        from live import pro2
        if seg == "v2early":
            sig = pro2.early_signals(e.store, src, day)
        else:
            sig = pro2.signals(e.store, src, day, "index" if seg == "v2index" else "stock", pick)
        v2 = {"v2": pro2.words(), "signals": sig,
              "compare": pro2.compare(e.store, src, ["pro2_index", "pro2_stocks", "pro2_stocks_rider", "pro2_early", "pro_index",
                                                      "pro_stocks", "pro_stocks_strict", "trail", "main"])}
    from live import provotes as _pv
    out = {"day": day, "seg": seg, "book": book, "days": pro.days(e.store, src), "gates": gates, "votes": _pv.names(),
           "trades": pro.book_history(e.store, f"{src}#{book}"), "base_weights": dict(c.get("weights") or {}),
           "rules": {k: c.get(k) for k in ("strong", "medium", "min_groups", "expiry_cutoff", "cooloff_min", "exit", "groups")},
           "signals": None if v2 else pro.signals(e.store, src, day, "stock" if seg == "strict" else seg, gates or None),
           "factors": fac, "weights": pro.weights(e.data_dir),
           "learn": {"champion": st.get("champion"), "challenger": st.get("challenger"), "since": st.get("since"),
                     "check": st.get("check"), "history": (st.get("history") or [])[-5:],
                     "gates_challenger": st.get("gates_challenger"), "gates_check": st.get("gates_check"),
                     "gates_suggested": st.get("gates_suggested"), "gates_history": (st.get("gates_history") or [])[-5:]}}
    out.update(v2)
    return clean(out)


@router.post("/api/live/day/{day}/review")
def live_day_review(day: str):
    """Write the AI day review now (one at a time; a deep-tier AI message, a minute or more)."""
    import threading
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise HTTPException(400, "day is YYYY-MM-DD")
    e = engine()
    lk = getattr(e, "_day_review_lock", None)
    if lk is not None and lk.locked():
        return {"busy": True}
    slot = dt.datetime.now(IST).strftime("%H%M") if day == e.session_day().isoformat() else "manual"
    threading.Thread(target=e.day_review_run, args=(day, slot), daemon=True, name="day-review").start()
    return {"started": True}


@router.get("/api/live/paper/total")
def live_paper_total(source: str | None = None):
    """2.69 (Mandeep: "sabhi trades ka mila ke bhi dikhao"): every paper account added up: booked, open, today."""
    from live.paper import PaperBook
    e = engine()
    src = source or (e.source if e.paper else e.cfg()["live"]["source"])
    live_ = bool(e.paper and e.source == src)
    accts = []
    try:
        mb = e.paper if live_ else PaperBook(e.store, src, e.cfg()["live"].get("paper"))
        accts.append(("main", "Main account", mb))
    except Exception:
        pass
    try:
        bs, _ = _books(src)
        for bid, bk in bs.books.items():
            accts.append((bid, (bs.specs.get(bid) or {}).get("name") or bid, bk))
    except Exception:
        pass
    rows = []
    for bid, name, bk in accts:
        try:
            sm = bk.summary(e.state if live_ else None)
            a = sm.get("account") or {}
            rows.append({"id": bid, "name": name, "booked": a.get("realised") or 0, "open_pnl": a.get("unrealised") or 0,
                         "today": a.get("today") or 0, "open": len(sm.get("open") or []), "trades": sm.get("n") or 0,
                         "won": round((sm.get("win_rate") or 0) * (sm.get("n") or 0) / 100)})
        except Exception as ex:
            rows.append({"id": bid, "name": name, "error": str(ex)[:120]})
    ok = [r for r in rows if "error" not in r]
    tot = {k: round(sum(r[k] for r in ok), 2) for k in ("booked", "open_pnl", "today", "open", "trades", "won")}
    tot["net"] = round(tot["booked"] + tot["open_pnl"], 2)
    tot["won_pct"] = round(tot["won"] / tot["trades"] * 100, 1) if tot["trades"] else None
    return clean({"source": src, "total": tot, "accounts": rows})


@router.get("/api/live/ghanshyam")
def live_ghanshyam(source: str | None = None, days: int = 30):
    """2.69: the Ghanshyam section: his strategies' records (Lab, test bench, paper account) and their signals on Nifty
    and Bank Nifty. Paper only."""
    from live import ghanshyam as G
    e = engine()
    src = source or (e.source if e.paper else e.cfg()["live"]["source"])
    bs, live_ = _books(src)
    return clean(G.view(e, bs, src, live_, max(1, min(365, int(days)))))


@router.post("/api/live/ghanshyam/install")
def live_ghanshyam_install():
    """2.69: load the repo's Ghanshyam strategy file into the Strategy Lab (same check as an upload)."""
    from live import ghanshyam as G
    try:
        out = G.install(getattr(engine(), "data_dir", None) or G.ROOT / "data")
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return clean({k: out.get(k) for k in ("id", "name", "version", "rules", "problems", "already")})


@router.post("/api/live/paper/open")
async def live_paper_open(request: Request):
    """Paper Buy (direction 1) / Sell (-1) one option by instrument key; or {sym, side: buy|sell} for the chart's
    at-the-money buttons. Paper only."""
    b = await request.json()
    e = engine()
    try:
        if b.get("key"):
            return clean(e.paper_open(b["key"], int(b.get("direction") or 1), int(b.get("lots") or 1)))
        return clean(e.paper_quick(str(b.get("sym", "")).upper(), b.get("side", "buy")))
    except ValueError as ex:
        raise HTTPException(400, str(ex))


@router.post("/api/live/paper/settings")
async def live_paper_settings(request: Request):
    """Paper switches from the Paper page (aggressive mode). Saved to your settings."""
    from radar.config import save_user_settings
    b = await request.json()
    upd = {}
    if "aggressive" in b:
        upd["live.paper.aggressive"] = bool(b["aggressive"])
    if upd:
        save_user_settings(upd)
        e = engine()
        if e.paper:
            e.paper.cfg.update({k.split(".")[-1]: v for k, v in upd.items()})
    return {"ok": True, **upd}


@router.get("/api/watchlist")
def watchlist_view():
    from live import watchlist
    return clean(watchlist.view(engine()))


@router.post("/api/watchlist")
async def watchlist_change(request: Request):
    from live import watchlist
    b = await request.json()
    sym = str(b.get("add") or "").strip().upper()
    e = engine()
    if sym and e.uni and sym not in e.uni.underlyings:
        raise HTTPException(400, f"{sym} is not an F&O symbol the app follows.")
    return {"manual": watchlist.change(e.store, add=sym or None, remove=b.get("remove"))}


@router.get("/api/live/paper/mini")
def live_paper_mini():
    """For the side bar: today's net (booked + open) and the demo account. 2.97: every open page asks; one answer per 2 s."""
    from webapp import memo
    return memo.memo("paper_mini", None, _paper_mini_now, ttl=2)


def _paper_mini_now():
    e = engine()
    if not e.paper:
        return {"running": False}
    p = e.paper.summary(e.state)
    a = p["account"]
    return clean({"running": True, "today": a["today"], "equity": a["equity"], "free": a["free"], "open": len(p["open"])})


def _books(src: str):
    """2.49: the extra paper accounts (live/books.py): the running ones, or read-only copies when the scanner is off."""
    from live.books import Books
    from live.paper import DEFAULTS, PaperBook
    e = engine()
    if getattr(e, "books", None) is not None and e.source == src:
        return e.books, True
    lc = e.cfg()["live"]
    main = e.paper.cfg if e.paper else PaperBook(e.store, src, lc.get("paper")).cfg if lc.get("paper") else dict(DEFAULTS)
    return Books(src, e.store, main, lc.get("books"), log=lambda m: None, data_dir=e.data_dir), False


@router.get("/api/live/paper")
def live_paper(source: str | None = None, book: str | None = None):
    from live.paper import PaperBook
    e = engine()
    src = source or (e.source if e.paper else e.cfg()["live"]["source"])
    if book and book != "main":                        # 2.49: Best only / 3% a day
        bs, live_ = _books(src)
        if book not in bs.books:
            raise HTTPException(404, f"No paper account '{book}'")
        out = bs.summary(book, e.state if live_ else None)
        seen = {}
        for t in out["open"] + out["closed"]:
            if t["sym"] not in seen:
                seen[t["sym"]] = e.underlying_view(t["sym"])
            t["und_now"] = seen[t["sym"]]
        out["aggressive"] = False
        return clean({**out, "source": src})
    book = e.paper if e.paper and e.source == src else PaperBook(e.store, src, e.cfg()["live"].get("paper"))
    out = book.summary(e.state if e.paper is book else None)
    seen = {}                                          # 2.48: one look-up per symbol (was one per trade: slow)
    for t in out["open"] + out["closed"]:              # the underlying's price and day move, for the tables
        if t["sym"] not in seen:
            seen[t["sym"]] = e.underlying_view(t["sym"])
        t["und_now"] = seen[t["sym"]]
    out["aggressive"] = bool(book.cfg.get("aggressive"))
    return clean({**out, "source": src})


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
    from webapp import memo
    e = engine()                                         # 3.00: worked out at most once a minute (was 1.5-2 s a call)
    day = e.clock.now().date().isoformat() if getattr(e, "clock", None) else ""
    return memo.memo(f"scorecard|{source}|{day}", None, lambda: clean(e.scorecard_view(source)), ttl=60)


@router.get("/api/live/grades")
def live_grades(source: str | None = None):
    """3.05: honest grade of every alert type (live/grade.py, on trial): median rupees a trade, win %, capped average,
    the main exit and the trailing exit, from the test bench. Worked out in the background; finished days cached."""
    from live import grade
    e = engine()
    src = source or e.source or e.cfg()["live"].get("source") or "upstox"
    today = e.clock.now().date().isoformat() if getattr(e, "clock", None) else dt.date.today().isoformat()
    return clean(grade.view(e.store, src, today))


@router.get("/api/live/board")
def live_board(regime: str = "auto", days: int = 60, source: str | None = None, family: str | None = None, mini: int = 0):
    """Strategy board (2.30): every strategy paper-tested on the test bench, ranked for today's day type and your capital.
    mini=1 (2.95, the Live page's "what works today"): the same answer without the ~4,000 rows."""
    out = _live_board(regime, days, source, family)
    if mini:
        out = {k: v for k, v in out.items() if k != "rows"}
    return out


def _live_board(regime, days, source, family):
    from webapp import memo
    e = engine()
    key = f"board|{source}|{regime}|{days}|{family}|{e.clock.now().date().isoformat()}|{int(e.running)}"
    return memo.memo(key, None, lambda: _live_board_now(regime, days, source, family), ttl=10)   # 2.95: pages share 10 s


def _live_board_now(regime: str, days: int, source: str | None, family: str | None):
    from live import bench
    e = engine()
    src = source or e.source or e.cfg()["live"].get("source") or "upstox"
    pc = (e.paper.cfg if e.paper else {**(e.cfg()["live"].get("paper") or {})})
    from live.paper import DEFAULTS
    cap = float(pc.get("capital") or DEFAULTS["capital"])
    risk = float(pc.get("risk_pct") or DEFAULTS["risk_pct"])
    now_reg = (e.regimes.get("NIFTY") or {}) if e.running else {}
    reg = now_reg.get("code") if regime == "auto" else (None if regime in ("all", "") else regime)
    if reg == "opening":
        reg = None
    fam = family if family in bench.FAMILIES else None
    from webapp import perf as _perf
    mark = _perf.Marks("board")                          # 2.94: which piece of the Strategy board is slow
    out = bench.board(e.store, src, cap, risk, reg, max(1, min(365, days)), today=e.clock.now().date().isoformat(), family=fam)
    mark("bench results")
    out.update(day_type=now_reg.get("label") if e.running else None, day_code=now_reg.get("code") if e.running else None, source=src,
               styles=[{"style": k, "label": v["label"], "family": v["family"]} for k, v in bench.load_styles()[0].items()])
    try:                                                  # 2.66: the 09:45 day-type forecast (lab/daytype.py), on trial
        from lab import daytype
        out["daytype"] = daytype.today(e.data_dir, e.clock.now().date().isoformat())
    except Exception:
        out["daytype"] = None
    mark("day-type forecast")
    return clean(out)


@router.get("/api/live/board/trades")
def live_board_trades(kind: str, style: str, variant: str | None = None, source: str | None = None, limit: int = 100):
    """2.38: one Strategy board row's test trades with their date and time, lots, quantity and money used."""
    from live import bench
    e = engine()
    src = source or e.source or e.cfg()["live"].get("source") or "upstox"
    return clean({"trades": bench.trades(e.store, src, kind, variant, style, limit)})


_FS_CACHE: dict = {}


@router.get("/api/live/focus_shadow")
def live_focus_shadow(days: int = 10, source: str | None = None):
    """2.37 shadow Focus: the current Focus vs the challenger versions (rules/focus_shadow.json) on the same alerts:
    picks, how much is one alert type, and the test bench's honest 1-lot result on those picks. Shadow only."""
    from live import focuslab
    e = engine()
    src = source or e.source or e.cfg()["live"].get("source") or "upstox"
    days = max(1, min(60, int(days)))
    k = (src, days)
    hit = _FS_CACHE.get(k)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    today = e.clock.now().date().isoformat() if getattr(e, "clock", None) else None
    out = focuslab.compare(e.store, src, days, today=today, lc=e.cfg().get("live") or {})
    out.update(source=src, status=e.focuslab.status() if getattr(e, "focuslab", None) is not None else None)
    out = clean(out)
    _FS_CACHE[k] = (time.time(), out)
    return out


@router.get("/api/live/accounts")
def live_accounts(days: int | None = None, source: str | None = None):
    """2.31 capital test: virtual paper accounts at your capital plus the levels in rules/capital.json, one per way of
    trading (only buying, buying + selling, only selling), replaying the test bench with each tier's rules.
    2.48: your own capital is always the first account (no fixed ₹40K level)."""
    from live import accounts
    e = engine()
    src = source or e.source or e.cfg()["live"].get("source") or "upstox"
    mine = float(((e.paper.cfg if e.paper else (e.cfg()["live"].get("paper") or {})) or {}).get("capital") or 500000)
    lv = [mine] + [x for x in accounts.settings()["levels"] if abs(x - mine) > 1]
    return clean(accounts.grid(e.store, src, levels=lv, days=max(1, min(365, days)) if days else None, wait=False))


@router.get("/api/live/candles_now/{sym}")
def live_candles_now(sym: str):
    """2.31: the latest closed candle's patterns on every timeframe for a symbol, and how each forming candle looks so far
    (information only: a candle is a pattern only once it closes)."""
    return clean(engine().candles_now(sym.upper()))


@router.get("/api/live/events")
def live_events(sym: str | None = None):
    """2.35: official feeds (RBI, SEBI, Government, NSE, BSE) and the event risk right now (NIFTY or a symbol)."""
    e = engine()
    if getattr(e, "events", None) is None:
        return {"sources": [], "items": [], "today": None}
    return clean(e.events.view(sym.upper() if sym else None))


@router.get("/api/live/vault")
def live_vault():
    """2.31 data vault: what is being recorded today and on past days, the test bench styles and the mover sweep."""
    return clean(engine().vault_view())


@router.get("/api/live/capital")
def live_capital(amount: str | None = None):
    """Read an amount ("40k", "1.5 lakh", "2,00,000") and show the account rules that fit it. Nothing is saved."""
    from live import capital
    e = engine()
    cur = float(((e.paper.cfg if e.paper else (e.cfg()["live"].get("paper") or {})) or {}).get("capital") or 500000)
    if amount is None:
        return {"capital": cur, "tier": capital.tier(cur)}
    try:
        v = capital.parse_amount(amount)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return {"capital": v, "tier": capital.tier(v), "settings": capital.paper_settings(v), "current": cur}


@router.post("/api/live/capital")
async def live_capital_set(request: Request):
    """Set the one capital the whole app uses (paper account, risk check, Strategy Lab, Strategy board) and, unless
    told not to, the account rules of its tier (rules/capital.json). Paper only."""
    from live import capital
    from radar.config import save_user_settings
    b = await request.json()
    try:
        v = capital.parse_amount(b.get("amount"))
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    p = capital.paper_settings(v) if b.get("apply_rules", True) else {"capital": v}
    upd = {f"live.paper.{k}": val for k, val in p.items()}
    upd["lab.capital"] = v
    save_user_settings(upd)
    e = engine()
    if e.paper:
        e.paper.cfg.update(p)
    return clean({"ok": True, "capital": v, "applied": p, "tier": capital.tier(v)})


@router.get("/api/live/export.xlsx")
async def live_export(days: int = 60, trades_days: int = 5, source: str | None = None):
    """2.49: everything the app tests in one Excel file (alert check, daily validation, test bench, exit styles,
    Strategy Lab 1 / 5 years, paper accounts). Read-only."""
    from live import export as X
    e = engine()
    sheets = await _run_blocking(X.build, e, source, max(1, min(365, days)), max(1, min(30, trades_days)))
    data, ext, media = await _run_blocking(X.to_xlsx, sheets)
    name = f"stockway_tests_{dt.datetime.now(IST).strftime('%Y-%m-%d_%H%M')}.{ext}"
    return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


_CRED: dict = {}


@router.get("/api/live/credibility")
async def live_credibility(days: int = 60, source: str | None = None, fresh: bool = False):
    """2.50 (#11): does a higher score / conviction / Focus really mean better alerts, and which facts make a test-bench
    trade better (with vs against, both halves of the days)? Read-only; kept 10 minutes."""
    import time as _t
    from live import credibility as CR
    e = engine()
    key = (source or e.source or "", int(days))
    hit = _CRED.get(key)
    if hit and not fresh and _t.time() - hit[0] < 600:
        return hit[1]
    out = clean(await _run_blocking(CR.report, e, source, max(1, min(365, days))))
    _CRED[key] = (_t.time(), out)
    return out


@router.get("/api/live/paper/books")
def live_paper_books(source: str | None = None, days: int | None = None):
    """2.49: the extra paper accounts and how each compares with the main account on the same days."""
    from live import books as B
    e = engine()
    src = source or (e.source if e.paper else e.cfg()["live"]["source"])
    bs, _ = _books(src)
    main = (e.paper.cfg if e.paper else (e.cfg()["live"].get("paper") or {})) or {}
    try:                                                 # 3.05: the scorecard against the main account
        rev = B.scorecard(e.store, src, e.clock.now().date().isoformat() if getattr(e, "clock", None) else None)
    except Exception:
        rev = []
    return clean({"books": bs.list(), "compare": B.compare(e.store, src, main, bs, days=days), "days": days,
                  "file_error": B._C.get("error"), "review": rev, "review_rules": B.review_cfg(),
                  "retired": B.retired(e.data_dir)})


@router.post("/api/live/paper/books/{bid}/keep")
def live_paper_book_keep(bid: str):
    """3.05: turn an account the scorecard switched off back on (never switched off by it again). Paper only."""
    from live import books as B
    if bid not in (B.cfg().get("books") or {}):
        raise HTTPException(404, f"No paper account '{bid}'")
    e = engine()
    j = B.keep_on(e.data_dir, bid)
    if getattr(e, "books", None) is not None and e.paper:
        e.books.load(e.paper.cfg, e.cfg()["live"].get("books"))
    return {"ok": True, "book": bid, "kept": j["kept"]}


@router.post("/api/live/paper/books/{bid}")
async def live_paper_book_set(bid: str, request: Request):
    """2.49: set an extra paper account's capital (Mandeep: "capital can be set manual"). Paper only."""
    from live import books as B
    from live import capital
    from radar.config import save_user_settings
    if bid not in (B.cfg().get("books") or {}):
        raise HTTPException(404, f"No paper account '{bid}'")
    b = await request.json()
    try:
        v = capital.parse_amount(b.get("amount"))
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    save_user_settings({f"live.books.{bid}.capital": v})
    e = engine()
    if getattr(e, "books", None) is not None and e.paper:
        e.books.load(e.paper.cfg, e.cfg()["live"].get("books"))
    return {"ok": True, "book": bid, "capital": v}


@router.get("/api/live/coach")
def live_coach(source: str | None = None):
    """Prop-firm challenge status and your trading patterns, from the paper account's trades."""
    from live import coach
    e = engine()
    src = source or e.source or e.cfg()["live"].get("source") or "upstox"
    trades = e.store.trades(source=src, limit=5000)
    pc = (e.paper.cfg if e.paper else (e.cfg()["live"].get("paper") or {})) or {}
    start = (e.cfg().get("live") or {}).get("challenge_start")
    return clean({"challenge": coach.challenge(trades, float(pc.get("capital") or 500000), start),
                  "patterns": coach.patterns(trades), "source": src})


@router.post("/api/live/coach/challenge")
async def live_coach_start(request: Request):
    """Start a new challenge today (earlier trades no longer count)."""
    from radar.config import save_user_settings
    e = engine()
    day = e.clock.now().date().isoformat()
    save_user_settings({"live.challenge_start": day})
    return {"ok": True, "start": day}


@router.get("/api/live/markets")
def live_markets():
    """World markets for the Live page (US, Asia, Europe, money and commodities), with day range and 52-week range."""
    return clean(engine().markets_board())


@router.get("/api/live/trials")
def live_trials(source: str | None = None):
    """Bullets on trial (shadow in live/registry.py): recorded and graded only, never in alerts or paper trades."""
    from live import trials
    e = engine()
    return clean(trials.board(e.store, source or e.source or "upstox"))


@router.get("/api/live/briefs")
def live_briefs(day: str | None = None):
    return clean(engine().briefs_view(day))


@router.get("/api/live/report")
def live_report(day: str | None = None, rebuild: bool = False):
    """End-of-day report: paper P&L, discipline vs the risk check, alerts and what worked, lessons."""
    e = engine()
    if day and not rebuild:
        saved = e.store.get(f"report:{day}")
        if saved:
            return clean(saved)
    try:
        return clean(e.day_report(day, save=bool(day)))
    except Exception as ex:
        raise HTTPException(400, f"Could not build the report: {ex}")


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
            "pro_auto_per_day": c.get("pro_auto_per_day") or 12, "last_error": e.ai.last_error,
            "routing": c.get("routing") is not False, "tiers": _ai_tiers(), "tasks": dict(getattr(e.ai, "tasks", {}) or {})}


def _ai_tiers() -> dict:
    """2.62: which model each kind of AI job uses (rules/ai_models.json)."""
    try:
        from live.notify import ai_models
        r = ai_models()
        out = {}
        for task, tier in (r.get("tasks") or {}).items():
            out.setdefault(tier, {"model": (r["tiers"].get(tier) or {}).get("model"),
                                  "effort": (r["tiers"].get(tier) or {}).get("effort"), "tasks": []})["tasks"].append(task)
        return out
    except Exception:
        return {}


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
