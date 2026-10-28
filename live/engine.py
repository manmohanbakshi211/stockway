"""Live engine: feed -> state -> metrics -> alerts -> notifications + paper trades."""
from __future__ import annotations

import collections
import datetime as dt
import threading
import time
import traceback
from pathlib import Path

from . import detectors
from .analytics import compute, norm_iv, pct
from .clock import IST, Clock, SimClock, is_market_hours, minutes_since_open
from .notify import AI, News, Telegram, alert_prompt, match_symbols, pulse_prompt, rule_pulse, symbol_words, telegram_text
from .paper import PaperBook
from .state import MarketState
from .store import Store
from .universe import VIX_KEY, atm_window
from .upstox import TokenStore

LIQUID = ["RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "AXISBANK", "KOTAKBANK", "LT", "ITC",
          "BHARTIARTL", "BAJFINANCE", "MARUTI", "TATAMOTORS", "M&M", "SUNPHARMA", "TITAN", "ADANIENT", "ADANIPORTS",
          "TATASTEEL", "JSWSTEEL", "HCLTECH", "HAL", "BEL", "DIXON", "ETERNAL", "ZOMATO", "TRENT", "BAJAJ-AUTO",
          "HINDALCO", "VEDL", "DLF", "INDUSINDBK", "PNB", "CANBK", "BANKBARODA", "COALINDIA", "ONGC", "NTPC",
          "POWERGRID", "TECHM", "WIPRO", "SWIGGY", "PAYTM", "IRFC", "BSE", "CDSL", "ANGELONE", "MCX", "POLYCAB"]

LIVE_DEFAULTS = {
    "source": "upstox",
    "redirect_uri": "http://127.0.0.1:8765/upstox/callback",
    "auto_start": True,
    "sim_speed": 10,
    "option_budget": 1900,
    "index_strikes": 12,
    "top_stock_strikes": 3,
    "cooldown_min": 15,
    "telegram_min_severity": 3,
    "news": True,
    "detectors": {},
    "paper": {},
}


class LiveEngine:
    def __init__(self, get_cfg, data_dir: str | Path):
        self.get_cfg = get_cfg
        self.data_dir = Path(data_dir)
        self.store = Store(self.data_dir / "live.db")
        self.tokens = TokenStore(self.data_dir / "upstox_token.json")
        self.logs = collections.deque(maxlen=400)
        self.lock = threading.RLock()
        self.telegram = Telegram(get_cfg, self.log)
        self.ai = AI(get_cfg, self.log)
        self.news = News(self.log)
        self.thread = None
        self.stop_evt = threading.Event()
        self.manual_stop_day = None
        self.reminded_day = None
        self.status = {"state": "stopped", "message": "Not started", "source": None, "since": time.time()}
        self._reset()
        threading.Thread(target=self._watch, daemon=True, name="live-watch").start()
        threading.Thread(target=self._news_loop, daemon=True, name="live-news").start()

    # ------------------------------------------------------------ helpers
    def cfg(self) -> dict:
        c = self.get_cfg()
        live = {**LIVE_DEFAULTS, **(c.get("live") or {})}
        return {**c, "live": live}

    def log(self, msg):
        line = f"{dt.datetime.now(IST):%H:%M:%S}  {msg}"
        self.logs.append(line)
        print(line, flush=True)

    def set_status(self, state, message):
        self.status.update(state=state, message=message, since=time.time())
        self.log(message)

    def _reset(self):
        self.clock = Clock()
        self.state = None
        self.uni = None
        self.rest = None
        self.feed = None
        self.paper = None
        self.source = None
        self.metrics = {}
        self.prev = {}
        self.cool = {}
        self.alerts_mem = collections.deque(maxlen=500)
        self.windows = {}
        self.opt_subs = set()
        self.full_chains = {}
        self.chain_cache = {}
        self.open_marks = collections.defaultdict(dict)
        self.avg_vol = {}
        self.vix_prev = None
        self.pulse = {"lines": [], "ai": None, "ts": None}
        self.words = {}
        self.timers = collections.defaultdict(float)

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    # ------------------------------------------------------------ control
    def start(self, source: str | None = None) -> dict:
        with self.lock:
            if self.running:
                return self.status
            c = self.cfg()["live"]
            source = source or c["source"]
            self.stop_evt.clear()
            self._reset()
            self.source = source
            self.status.update(source=source)
            self.thread = threading.Thread(target=self._run_safe, daemon=True, name="live-engine")
            self.thread.start()
            return self.status

    def stop(self, manual=True):
        if manual:
            self.manual_stop_day = dt.datetime.now(IST).date()
        self.stop_evt.set()
        if self.feed:
            self.feed.stop()
        if self.thread:
            self.thread.join(timeout=10)
        if self.status["state"] in ("running", "starting"):
            self.set_status("stopped", "Live scanner stopped")

    def _run_safe(self):
        try:
            self._run()
        except PermissionError as e:
            self.tokens.clear()
            self.set_status("login_needed", str(e))
        except Exception as e:
            self.log(traceback.format_exc())
            self.set_status("error", f"Live scanner error: {e}")
        finally:
            if self.feed:
                self.feed.stop()

    # ------------------------------------------------------------ startup
    def _run(self):
        c = self.cfg()
        lc = c["live"]
        self.set_status("starting", "Starting live scanner" + (" (simulator)" if self.source == "sim" else ""))
        if self.source == "sim":
            from .sim import SimFeed, SimMarket, SimREST
            self.store.clear_source("sim")
            self.clock = SimClock(speed=float(lc.get("sim_speed") or 10))
            market = SimMarket(self.clock)
            self.uni = market.uni
            self.rest = SimREST(market)
            self.status["user"] = "Simulator"
            make_feed = lambda: SimFeed(market, self._on_ticks, self.log)
        else:
            from . import universe
            from .upstox import UpstoxFeed, UpstoxREST
            tok = self.tokens.load()
            if not tok:
                self.set_status("login_needed", "Log in with Upstox to start (needed once a day)")
                return
            self.clock = Clock()
            self.uni = universe.load(str(self.data_dir), log=self.log)
            self.rest = UpstoxREST(tok["access_token"])
            prof = self.rest.profile() or {}
            self.status["user"] = prof.get("user_name") or tok.get("user")
            make_feed = lambda: UpstoxFeed(tok["access_token"], self._on_ticks, self._feed_status)
        self.paper = PaperBook(self.store, self.source, lc.get("paper"), self.log)
        self.state = MarketState()
        self.state.und_keys = {u.key: s for s, u in self.uni.underlyings.items()}
        self.words = symbol_words(self.uni)
        for n in self.news.items:
            n["syms"] = match_symbols(n["title"], self.words)

        base_keys = [u.key for u in self.uni.underlyings.values()] + \
                    [u.fut_key for u in self.uni.underlyings.values() if u.fut_key] + [VIX_KEY]
        self.log(f"Loading starting prices for {len(base_keys)} instruments")
        self._seed(self.rest.quotes(base_keys))
        self.feed = make_feed()
        self.feed.start()
        self.feed.subscribe(base_keys, "full")
        self.avg_vol = self.store.get(f"avg_vol:{self.source}:{self.clock.now().date()}", {}) or {}
        if len(self.avg_vol) < len(self.uni.stocks) * 0.8:
            threading.Thread(target=self._load_avg_vol, daemon=True, name="avg-vol").start()
        self._recenter()
        self.set_status("running", f"Live: {len(self.uni.indices)} indices, {len(self.uni.stocks)} stocks, "
                                   f"{len(self.opt_subs)} options streaming")
        self._loop()

    def _seed(self, quotes: dict):
        ticks, base = [], {}
        for k, q in quotes.items():
            cp = q.get("prev_close_price") or ((q.get("last_price") or 0) - (q.get("net_change") or 0)) or None
            base[k] = {"prev_oi": q.get("previous_oi"), "cp": cp}
            o = q.get("ohlc") or {}
            t = {"key": k, "ltp": q.get("last_price"), "cp": cp, "vol": q.get("volume"), "oi": q.get("oi") or None,
                 "atp": q.get("average_price") or None}
            if is_market_hours(self.clock.now()):
                t.update(day_o=o.get("open"), day_h=o.get("high"), day_l=o.get("low"))
            ticks.append(t)
        self.state.set_baselines(base)
        self.state.apply(ticks, self.clock.ts())

    def _load_avg_vol(self):
        out = dict(self.avg_vol)
        for u in self.uni.stocks:
            if self.stop_evt.is_set():
                return
            if u.symbol in out:
                continue
            try:
                cs = self.rest.daily_candles(u.key, 40)
                vols = [c[5] for c in cs[:20] if c[5]]
                if vols:
                    out[u.symbol] = sum(vols) / len(vols)
            except PermissionError:
                raise
            except Exception as e:
                self.log(f"Average volume for {u.symbol} failed: {e}")
        self.avg_vol = out
        self.store.put(f"avg_vol:{self.source}:{self.clock.now().date()}", out)
        self.log(f"Average volumes ready for {len(out)} stocks")

    def _feed_status(self, msg):
        self.log(msg)
        if "rejected the token" in msg:
            self.tokens.clear()
            self.stop_evt.set()
            self.set_status("login_needed", "Upstox login expired. Log in again.")

    def _on_ticks(self, ticks):
        st = self.state
        if st is not None:
            st.apply(ticks, self.clock.ts())

    # ------------------------------------------------------------ option windows
    def _recenter(self):
        lc = self.cfg()["live"]
        budget = int(lc["option_budget"])
        today = self.clock.now().date()
        plan = {}
        def strikes_for(u, n):
            exp = u.near_expiry(today)
            if not exp:
                return None, []
            und = self.state.get(u.key) or self.state.get(u.fut_key)
            spot = und.ltp if und and und.ltp else None
            if not spot:
                return exp, []
            return exp, atm_window(u.strikes(exp), spot, n)
        used = 0
        for u in self.uni.indices:
            exp, ks = strikes_for(u, int(lc["index_strikes"]))
            plan[u.symbol] = (exp, ks)
            used += 2 * len(ks)
        stocks = self._rank_stocks()
        for u in stocks:
            exp, ks = strikes_for(u, 0)
            plan[u.symbol] = (exp, ks)
            used += 2 * len(ks)
        n_top = int(lc["top_stock_strikes"])
        for u in stocks:
            extra = 2 * (2 * n_top)
            if used + extra > budget:
                break
            exp, ks = strikes_for(u, n_top)
            used += 2 * (len(ks) - len(plan[u.symbol][1]))
            plan[u.symbol] = (exp, ks)
        want, windows = set(), {}
        for sym, (exp, ks) in plan.items():
            u = self.uni.underlyings[sym]
            windows[sym] = ks
            for k in ks:
                for side in ("CE", "PE"):
                    o = u.chains.get(exp, {}).get(k, {}).get(side)
                    if o:
                        want.add(o.key)
        add, drop = want - self.opt_subs, self.opt_subs - want
        if add:
            try:
                self._seed(self.rest.quotes(sorted(add)))
            except PermissionError:
                raise
            except Exception as e:
                self.log(f"Option baselines failed: {e}")
            self.feed.subscribe(sorted(add), "option_greeks")
        if drop:
            self.feed.unsubscribe(sorted(drop))
        self.opt_subs = want
        self.windows = windows

    def _rank_stocks(self):
        stocks = self.uni.stocks
        if self.avg_vol:
            def turnover(u):
                i = self.state.get(u.key)
                return (self.avg_vol.get(u.symbol) or 0) * ((i.cp or i.ltp or 0) if i else 0)
            return sorted(stocks, key=turnover, reverse=True)
        rank = {s: i for i, s in enumerate(LIQUID)}
        return sorted(stocks, key=lambda u: rank.get(u.symbol, 999))

    # ------------------------------------------------------------ full chains (REST)
    def chain(self, sym: str, max_age=20.0, expiry=None) -> dict | None:
        u = self.uni.underlyings.get(sym) if self.uni else None
        if not u:
            return None
        exp = expiry or u.near_expiry(self.clock.now().date())
        if not exp:
            return None
        ck = (sym, exp)
        hit = self.chain_cache.get(ck)
        if hit and time.time() - hit["fetched"] < max_age:
            return hit
        rows = self.rest.option_chain(u.key, exp)
        oi, out_rows = {}, []
        for r in rows:
            k = float(r["strike_price"])
            ce, pe = r.get("call_options") or {}, r.get("put_options") or {}
            cm, pm = ce.get("market_data") or {}, pe.get("market_data") or {}
            oi[k] = {"CE": cm.get("oi") or 0, "PE": pm.get("oi") or 0}
            def side(o, md):
                g = o.get("option_greeks") or {}
                prev = md.get("prev_oi")
                return {"key": o.get("instrument_key"), "ltp": md.get("ltp"), "cp": md.get("close_price"),
                        "chg_pct": pct(md.get("ltp"), md.get("close_price")), "oi": md.get("oi"),
                        "oi_chg": (md.get("oi") - prev) if md.get("oi") is not None and prev else None,
                        "oi_chg_pct": pct(md.get("oi"), prev), "vol": md.get("volume"), "iv": norm_iv(g.get("iv")),
                        "delta": g.get("delta"), "bid": md.get("bid_price"), "ask": md.get("ask_price")}
            out_rows.append({"strike": k, "CE": side(ce, cm), "PE": side(pe, pm)})
        ce_t = sum(v["CE"] for v in oi.values())
        pe_t = sum(v["PE"] for v in oi.values())
        res = {"sym": sym, "expiry": exp.isoformat(), "oi": oi, "pcr": (pe_t / ce_t) if ce_t else None,
               "rows": out_rows, "fetched": time.time(), "expiries": [e.isoformat() for e in u.expiries]}
        self.chain_cache[ck] = res
        if exp == u.near_expiry(self.clock.now().date()):
            self.full_chains[sym] = res
        return res

    # ------------------------------------------------------------ main loop
    def _every(self, name, seconds, clock_time=True):
        now = self.clock.ts() if clock_time else time.time()
        if now - self.timers[name] >= seconds:
            self.timers[name] = now
            return True
        return False

    def _loop(self):
        lc = self.cfg()["live"]
        last_minute = None
        while not self.stop_evt.is_set():
            t0 = time.time()
            now = self.clock.now()
            mins = minutes_since_open(now)
            ended = (self.source == "sim" and mins >= 375) or (self.source != "sim" and (
                now.time() >= dt.time(15, 31) or now.weekday() >= 5))
            if ended:
                self.paper.update(self.state, now, force_close=True)
                self._compute_all(now, mins, emit=False)
                self.set_status("closed", "Simulation day finished" if self.source == "sim" else "Market closed for today")
                if self.feed:
                    self.feed.stop()
                return
            minute = int(now.timestamp() // 60) * 60
            if minute != last_minute:
                self.state.snapshot_minute(minute)
                last_minute = minute
                lc = self.cfg()["live"]       # pick up settings changes once a minute
                if self.paper:
                    self.paper.cfg.update(lc.get("paper") or {})
            if self._every("recenter", 120):
                try:
                    self._recenter()
                except PermissionError:
                    raise
                except Exception as e:
                    self.log(f"Re-centering failed: {e}")
            for u in self.uni.indices:
                if self._every(f"chain:{u.symbol}", 180 if u.symbol in ("NIFTY", "BANKNIFTY") else 360):
                    try:
                        self.chain(u.symbol, max_age=0)
                    except PermissionError:
                        raise
                    except Exception as e:
                        self.log(f"Option chain {u.symbol} failed: {e}")
            self._compute_all(now, mins, emit=True, lc=lc)
            self.paper.update(self.state, now)
            if self._every("pulse", 5, clock_time=False):
                self._rule_pulse()
            if self._every("ai_pulse", int((self.cfg().get("ai") or {}).get("pulse_every_min") or 15) * 60, clock_time=False) \
                    and self.ai.enabled() and mins >= 5:
                threading.Thread(target=self._ai_pulse, daemon=True).start()
            self.status["loop_ms"] = round((time.time() - t0) * 1000)
            self.stop_evt.wait(max(0.2, 1.0 - (time.time() - t0)))

    def _vix(self):
        v = self.state.get(VIX_KEY) if self.state else None
        if not v or not v.ltp:
            return None
        return {"ltp": v.ltp, "cp": v.cp, "chg_pct": pct(v.ltp, v.cp), "day_h": v.day_h, "day_l": v.day_l}

    def _compute_all(self, now, mins, emit=True, lc=None):
        lc = lc or self.cfg()["live"]
        dcfg = lc.get("detectors") or {}
        vix = self._vix()
        new_alerts = []
        for sym, u in self.uni.underlyings.items():
            try:
                m = compute(u, self.state, now, self.windows.get(sym, []), self.full_chains.get(sym),
                            self.avg_vol.get(sym), self.open_marks[sym], vix["ltp"] if vix else None)
            except Exception as e:
                self.log(f"Metrics failed for {sym}: {e}")
                continue
            if not m:
                continue
            if emit:
                try:
                    new_alerts += detectors.run(m, self.prev.get(sym), dcfg, mins)
                except Exception as e:
                    if self._every(f"deterr:{sym}", 600, clock_time=False):
                        self.log(f"Alert check failed for {sym}: {e}")
            self.prev[sym] = m
            self.metrics[sym] = m
        if vix and emit:
            new_alerts += detectors.vix_alerts(vix["ltp"], self.vix_prev, vix["cp"], dcfg)
            self.vix_prev = vix["ltp"]
        for a in sorted(new_alerts, key=lambda a: -a["severity"]):
            self._emit(a, now, lc)

    def _suggest(self, sym, direction):
        if direction not in ("up", "down"):
            return None
        u = self.uni.underlyings.get(sym)
        m = self.metrics.get(sym)
        if not u or not m:
            return None
        exp = u.near_expiry(self.clock.now().date())
        if not exp:
            return None
        chain = u.chains.get(exp, {})
        if not chain:
            return None
        k = min(chain, key=lambda s: abs(s - m["spot"]))
        side = "CE" if direction == "up" else "PE"
        o = chain[k].get(side)
        i = self.state.get(o.key) if o else None
        if not o or not i or not i.ltp:
            return None
        return {"key": o.key, "label": f"{sym} {k:g} {side} {exp:%d %b}", "side": side, "strike": k,
                "expiry": exp.isoformat(), "lot": o.lot, "ltp": i.ltp, "ask": i.ask, "iv": norm_iv(i.iv),
                "delta": i.delta, "cost_per_lot": round((i.ask or i.ltp) * o.lot, 2)}

    def _emit(self, a, now, lc):
        ts = now.timestamp()
        key = (a["sym"], a["kind"], None if a["kind"] in detectors.NO_DIRECTION_COOLDOWN else a["direction"])
        last = self.cool.get(key)
        cool = detectors.COOLDOWN.get(a["kind"], float(lc["cooldown_min"]))
        if last and ts - last[0] < cool * 60 and a["severity"] <= last[1]:
            return
        self.cool[key] = (ts, a["severity"])
        a.update(ts=ts, day=now.date().isoformat(), source=self.source, option=self._suggest(a["sym"], a["direction"]))
        a["id"] = self.store.add_alert(a)
        a["ai"] = None
        self.alerts_mem.appendleft(a)
        if a["severity"] >= int(lc["telegram_min_severity"]):
            self.telegram.send(telegram_text(a))
        aic = self.cfg().get("ai") or {}
        if self.ai.enabled() and a["severity"] >= int(aic.get("explain_min_severity") or 3):
            threading.Thread(target=self.explain, args=(a["id"],), daemon=True).start()
        try:
            self.paper.try_open(a, self.state, now)
        except Exception as e:
            self.log(f"Paper trade failed: {e}")

    # ------------------------------------------------------------ AI + pulse
    def explain(self, alert_id) -> str | None:
        a = next((x for x in self.alerts_mem if x["id"] == alert_id), None) or self.store.alert(alert_id)
        if not a:
            return None
        if a.get("ai"):
            return a["ai"]
        news = [n for n in self.news.items if a["sym"] in n["syms"]]
        text = self.ai.ask(alert_prompt(a, self.metrics.get(a["sym"]), news), 300)
        if text:
            self.store.set_alert_ai(alert_id, text)
            for x in self.alerts_mem:
                if x["id"] == alert_id:
                    x["ai"] = text
        return text

    def _movers(self):
        st = [m for m in self.metrics.values() if not m["is_index"]]
        def k(f):
            return lambda m: (m.get(f) if m.get(f) is not None else -1e9)
        return {"gainers": [self.slim(m) for m in sorted(st, key=k("chg_pct"), reverse=True)[:8]],
                "losers": [self.slim(m) for m in sorted(st, key=lambda m: m.get("chg_pct") if m.get("chg_pct") is not None else 1e9)[:8]],
                "hot": [self.slim(m) for m in sorted(st, key=k("heat"), reverse=True)[:8]]}

    def _indices(self):
        order = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"]
        out = [self.metrics[s] for s in order if s in self.metrics]
        return out

    def _rule_pulse(self):
        self.pulse["lines"] = rule_pulse(self._indices(), list(self.alerts_mem)[:5], self._movers(), self._vix())
        self.pulse["rule_ts"] = time.time()

    def _ai_pulse(self):
        snap = {"time": self.clock.now().strftime("%H:%M"), "vix": self._vix(),
                "indices": [self._slim_for_ai(m) for m in self._indices()],
                "top_movers": [{k: m.get(k) for k in ("sym", "chg_pct", "rel_vol", "score")} for m in self._movers()["hot"][:6]],
                "recent_alerts": [a["title"] for a in list(self.alerts_mem)[:10]],
                "headlines": [n["title"] for n in self.news.items[:10]]}
        text = self.ai.ask(pulse_prompt(snap), 350)
        if text:
            self.pulse.update(ai=text, ts=time.time())

    def _slim_for_ai(self, m):
        from .notify import _slim_metrics
        return _slim_metrics(m)

    # ------------------------------------------------------------ background: watcher + news
    def _watch(self):
        """Auto-start at 9:00 on market days when logged in; remind to log in."""
        while True:
            time.sleep(20)
            try:
                c = self.cfg()["live"]
                now = dt.datetime.now(IST)
                if c["source"] != "upstox" or not c.get("auto_start") or now.weekday() >= 5:
                    continue
                if not (dt.time(9, 0) <= now.time() < dt.time(15, 25)) or self.running:
                    continue
                if self.manual_stop_day == now.date() or self.status["state"] == "closed":
                    continue
                if self.tokens.load():
                    self.log("Auto-starting live scanner for today's session")
                    self.start("upstox")
                elif self.reminded_day != now.date():
                    self.reminded_day = now.date()
                    self.status.update(state="login_needed", message="Log in with Upstox to start today's live scanner")
                    self.telegram.send("🔑 Stockway: log in with Upstox to start today's live scanner (open the app → Live).")
            except Exception as e:
                self.log(f"Watcher error: {e}")

    def _news_loop(self):
        time.sleep(5)
        while True:
            try:
                if self.cfg()["live"].get("news"):
                    new = self.news.fetch(self.words or {})
                    for n in new:
                        if n["syms"] and self.running and n["ts"] > time.time() - 1800:
                            self._news_alert(n)
            except Exception as e:
                self.log(f"News fetch failed: {e}")
            time.sleep(300)

    def _news_alert(self, n):
        sym = n["syms"][0]
        if self.uni and sym in self.uni.underlyings and not self.uni.underlyings[sym].is_index:
            d = "up" if n["tone"] > 0 else "down" if n["tone"] < 0 else "neutral"
            m = self.metrics.get(sym) or {"sym": sym}
            a = {"sym": sym, "kind": "news", "direction": d, "severity": 1, "title": f"News: {n['title'][:110]}",
                 "detail": f"{n['source']}. {sym} {m.get('chg_pct') or 0:+.2f}% now.", "price": m.get("spot"),
                 "data": {"link": n["link"]}}
            self._emit(a, self.clock.now(), self.cfg()["live"])

    # ------------------------------------------------------------ views for the API
    @staticmethod
    def slim(m) -> dict:
        f = m.get("fut") or {}
        o = m.get("opt") or {}
        return {"sym": m["sym"], "name": m["name"], "is_index": m["is_index"], "spot": m["spot"], "chg_pct": m.get("chg_pct"),
                "rel_vol": m.get("rel_vol"), "ret_5m": m.get("ret_5m"), "ret_15m": m.get("ret_15m"),
                "vs_vwap_pct": m.get("vs_vwap_pct"), "day_h": m.get("day_h"), "day_l": m.get("day_l"),
                "buildup": f.get("buildup_label"), "buildup_sign": f.get("buildup_sign"), "fut_oi_chg": f.get("oi_chg_pct"),
                "atm_iv": o.get("atm_iv"), "iv_chg_15m": o.get("iv_chg_15m"), "pcr": o.get("pcr"),
                "unusual": len(o.get("unusual") or []), "score": m.get("score"), "heat": m.get("heat"), "lot": m.get("lot")}

    @staticmethod
    def index_view(m) -> dict:
        o = dict(m.get("opt") or {})
        o.pop("rows", None)
        return {**{k: v for k, v in m.items() if k != "opt"}, "opt": o}

    def snapshot(self, since_id=0) -> dict:
        now = self.clock.now()
        feed = None
        if self.feed:
            feed = {"connected": self.feed.connected, "msgs": self.feed.msgs,
                    "last_msg_age": round(time.time() - self.feed.last_msg, 1) if self.feed.last_msg else None,
                    "subs": self.feed.counts()}
        new = [a for a in self.alerts_mem if a["id"] > since_id][:100]
        ps = self.paper.summary(self.state) if self.paper else None
        return {"status": {**self.status, "running": self.running, "logged_in": bool(self.tokens.load()),
                           "feed": feed, "ticks": self.state.tick_count if self.state else 0},
                "clock": now.isoformat(), "mins": minutes_since_open(now),
                "indices": [self.index_view(m) for m in self._indices()], "vix": self._vix(),
                "alerts": new, "pulse": self.pulse, "movers": self._movers() if self.metrics else None,
                "paper": {"open": len(ps["open"]), "net": ps["net"], "unreal": ps["unreal"], "n": ps["n"],
                          "win_rate": ps["win_rate"]} if ps else None,
                "news": self.news.items[:12], "ai": {"enabled": self.ai.enabled(), "used": self.ai.used,
                                                     "error": self.ai.last_error}}

    def scanner(self) -> list:
        return [self.slim(m) for m in self.metrics.values()]

    def symbol(self, sym, expiry=None) -> dict | None:
        m = self.metrics.get(sym)
        if not m:
            return None
        chain = None
        try:
            exp = dt.date.fromisoformat(expiry) if expiry else None
            chain = self.chain(sym, max_age=20, expiry=exp)
        except PermissionError:
            raise
        except Exception as e:
            self.log(f"Chain for {sym} failed: {e}")
        bars = self.state.bars.get(sym)
        return {"metrics": m, "chain": chain,
                "bars": [b[:5] for b in bars.bars] if bars else [],
                "alerts": [a for a in self.alerts_mem if a["sym"] == sym][:30],
                "news": [n for n in self.news.items if sym in n["syms"]][:10]}
