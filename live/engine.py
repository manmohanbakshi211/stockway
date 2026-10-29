"""Live engine: feed -> state -> metrics -> alerts -> scoring, notifications, paper trades, briefs."""
from __future__ import annotations

import collections
import datetime as dt
import threading
import time
import traceback
from pathlib import Path

from . import briefs, detectors
from .analytics import compute, norm_iv, pct
from .clock import IST, Clock, SimClock, is_market_hours, minutes_since_open
from .context import Context
from .learn import Ranker, features, regime, regime_adjust
from .notify import AI, News, Telegram, alert_prompt, match_symbols, pulse_prompt, rule_pulse, symbol_words, telegram_text
from .paper import PaperBook
from .record import Recorder, Tracker, evidence, levels, outcomes, scorecard, signal_score, size_mb
from .state import MarketState
from .store import Store
from .universe import VIX_KEY, atm_window
from . import riskgate
from .upstox import TokenStore
from .vol import atm_iv_from_chain, daily_stats, gex, percentile, structure

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
    "filings": True,
    "briefs": True,
    "mute_hit_rate": 35,
    "keep_recordings_days": 365,          # alert types below this hit rate (30+ cases) are demoted
    "detectors": {},
    "paper": {},
}
STORY_GAP_MIN = 30


class LiveEngine:
    def __init__(self, get_cfg, data_dir: str | Path):
        self.get_cfg = get_cfg
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.data_dir / "live.db")
        self.tokens = TokenStore(self.data_dir / "upstox_token.json")
        self.logs = collections.deque(maxlen=400)
        self.lock = threading.RLock()
        self.telegram = Telegram(get_cfg, self.log)
        self.ai = AI(get_cfg, self.log)
        self.news = News(self.log)
        self.context = Context(self.data_dir, self.log)
        self.context.on_filing = self._filing_alert
        from .brain import Brain
        from .world import World
        self.world = World(self.data_dir, self.log, self.ai)
        self.world.on_macro = self._macro_alert
        self.brain = Brain(self)
        self.thread = None
        self.stop_evt = threading.Event()
        self.manual_stop_day = None
        self.reminded_day = None
        self.brief_done = set()
        self.status = {"state": "stopped", "message": "Not started", "source": None, "since": time.time()}
        Tracker(self.store, "init")              # make sure the outcomes table exists
        self._reset()
        from lab.live import StrategyDesk
        self.desk = StrategyDesk(self)
        threading.Thread(target=self._watch, daemon=True, name="live-watch").start()
        threading.Thread(target=self._news_loop, daemon=True, name="live-news").start()
        threading.Thread(target=self.context.run_forever, args=(self._filings_wanted,), daemon=True,
                         name="live-context").start()
        threading.Thread(target=self._world_loop, daemon=True, name="live-world").start()

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
        self.recorder = None
        self.tracker = None
        self.ranker = None
        self.metrics = {}
        self.prev = {}
        self.cool = {}
        self.alerts_mem = collections.deque(maxlen=500)
        self.windows = {}
        self.opt_subs = set()
        self.full_chains = {}
        self.next_chains = {}
        self.chain_cache = {}
        self.gex = {}
        self.regimes = {}
        self.open_marks = collections.defaultdict(dict)
        self.avg_vol = {}
        self.daily = {}
        self.vix_prev = None
        self.pulse = {"lines": [], "ai": None, "ts": None}
        self.market = {}
        self.words = {}
        self.timers = collections.defaultdict(float)
        self.stories = {}
        self.stats = {}
        self.prints_seen = 0
        self.watch = {"stalled": False, "since": None}
        from .chartdata import ChartData
        self.charts = ChartData(self)
        from .edgebook import EdgeBook
        self.edge = EdgeBook(self.data_dir)
        from .analog import Scenarios
        self.scenarios = Scenarios(self)
        from .edgebook import PatternBook
        self.patterns = PatternBook(self.data_dir)
        from .timemachine import TimeMachine
        self.tm = TimeMachine(self)
        from .insiders import Insiders
        self.insiders = Insiders(self)

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def _filings_wanted(self):
        now = dt.datetime.now(IST)
        return self.cfg()["live"].get("filings") and now.weekday() < 5 and dt.time(7, 30) <= now.time() <= dt.time(20, 0)

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
            self.thread.join(timeout=15)
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
            if self.tracker:
                try:
                    self.tracker.update(self.state, self.clock.ts(), force=True)
                except Exception:
                    pass

    # ------------------------------------------------------------ startup
    def _run(self):
        c = self.cfg()
        lc = c["live"]
        self.set_status("starting", "Starting live scanner" + (" (simulator)" if self.source == "sim" else ""))
        if self.source == "sim":
            from .sim import SimFeed, SimMarket, SimREST
            self.store.clear_source("sim")
            with self.store.lock:
                self.store.db.execute("DELETE FROM outcomes WHERE source='sim' AND day=?",
                                      (dt.datetime.now(IST).date().isoformat(),))
                self.store.db.commit()
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
        day = self.clock.now().date().isoformat()
        self.paper = PaperBook(self.store, self.source, lc.get("paper"), self.log)
        self.state = MarketState()
        self.state.clock = self.clock
        self.state.und_keys = {u.key: s for s, u in self.uni.underlyings.items()}
        self.state.flow_keys = {u.key for u in self.uni.stocks} | {u.fut_key for u in self.uni.underlyings.values() if u.fut_key}
        self.recorder = Recorder(self.data_dir, self.source, day)
        self.tracker = Tracker(self.store, self.source)
        self.ranker = Ranker(self.data_dir, self.source, self.log)
        if self.source != "sim":
            self.context.fo_symbols = set(self.uni.underlyings)
        self.words = symbol_words(self.uni)
        for n in self.news.items:
            n["syms"] = match_symbols(n["title"], self.words)
        self._refresh_stats()

        base_keys = [u.key for u in self.uni.underlyings.values()] + \
                    [u.fut_key for u in self.uni.underlyings.values() if u.fut_key] + [VIX_KEY]
        self.log(f"Loading starting prices for {len(base_keys)} instruments")
        self._seed(self.rest.quotes(base_keys))
        self.feed = make_feed()
        self.feed.start()
        self.feed.subscribe(base_keys, "full")
        self.daily = self.store.get(f"daily:{self.source}:{day}", {}) or {}
        self.avg_vol = {s: d.get("avg_vol") for s, d in self.daily.items() if d.get("avg_vol")}
        if len(self.daily) < len(self.uni.underlyings) * 0.8:
            threading.Thread(target=self._load_daily, daemon=True, name="daily-history").start()
        self._attach_iv_history()
        self.desk.load_plan()
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

    def _load_daily(self):
        """One year of daily candles per symbol: average volume, realised volatility and its percentile."""
        out = dict(self.daily)
        for u in list(self.uni.indices) + self._rank_stocks():
            if self.stop_evt.is_set():
                return
            if u.symbol in out:
                continue
            try:
                out[u.symbol] = daily_stats(self.rest.daily_candles(u.key, 400))
            except PermissionError:
                raise
            except Exception as e:
                self.log(f"History for {u.symbol} failed: {e}")
            if len(out) % 25 == 0:
                self.daily = out
                self.avg_vol = {s: d.get("avg_vol") for s, d in out.items() if d.get("avg_vol")}
        try:
            cs = sorted(self.rest.daily_candles(VIX_KEY, 400), key=lambda c: c[0])
            out["_vix"] = {"closes": [c[4] for c in cs]}
        except PermissionError:
            raise
        except Exception as e:
            self.log(f"VIX history failed: {e}")
        self.daily = out
        self.avg_vol = {s: d.get("avg_vol") for s, d in out.items() if d.get("avg_vol")}
        self._attach_iv_history()
        self.store.put(f"daily:{self.source}:{self.clock.now().date()}", out)
        self.log(f"One-year history ready for {len(out)} symbols")

    def _attach_iv_history(self):
        hist = self.store.get(f"ivhist:{self.source}", {}) or {}
        for sym, rows in hist.items():
            self.daily.setdefault(sym, {})["iv_hist"] = [iv for _, iv in rows][-250:]

    def _save_iv_history(self):
        hist = self.store.get(f"ivhist:{self.source}", {}) or {}
        day = self.clock.now().date().isoformat()
        for sym, m in self.metrics.items():
            iv = (m.get("opt") or {}).get("atm_iv")
            if iv:
                rows = [r for r in hist.get(sym, []) if r[0] != day] + [[day, round(iv, 2)]]
                hist[sym] = rows[-300:]
        self.store.put(f"ivhist:{self.source}", hist)

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
        want, windows = set(getattr(self, "desk", None).needed_keys() if getattr(self, "desk", None) else ()), {}
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
                        "delta": g.get("delta"), "gamma": g.get("gamma"), "bid": md.get("bid_price"),
                        "ask": md.get("ask_price")}
            out_rows.append({"strike": k, "CE": side(ce, cm), "PE": side(pe, pm)})
        ce_t = sum(v["CE"] for v in oi.values())
        pe_t = sum(v["PE"] for v in oi.values())
        res = {"sym": sym, "expiry": exp.isoformat(), "oi": oi, "pcr": (pe_t / ce_t) if ce_t else None,
               "rows": out_rows, "fetched": time.time(), "expiries": [e.isoformat() for e in u.expiries],
               "spot": rows[0].get("underlying_spot_price") if rows else None}
        self.chain_cache[ck] = res
        near = u.near_expiry(self.clock.now().date())
        if exp == near:
            self.full_chains[sym] = res
            m = self.metrics.get(sym)
            spot = (m or {}).get("spot") or res["spot"]
            if spot:
                g = gex(res, spot, self.clock.now())
                if g:
                    self.gex[sym] = g
        return res

    def _next_expiry_chain(self, sym):
        u = self.uni.underlyings.get(sym)
        exps = [e for e in u.expiries if e > self.clock.now().date()] if u else []
        near = u.near_expiry(self.clock.now().date()) if u else None
        nxt = next((e for e in exps if e != near), None)
        if nxt:
            self.next_chains[sym] = self.chain(sym, max_age=0, expiry=nxt)

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
                self._end_of_day(now, mins)
                return
            minute = int(now.timestamp() // 60) * 60
            if minute != last_minute:
                self.state.snapshot_minute(minute)
                if last_minute is not None and self.metrics:
                    try:
                        self.recorder.snapshot(minute, self.state, self.metrics, self.record_view)
                    except Exception as e:
                        self.log(f"Recording failed: {e}")
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
            for sym in ("NIFTY", "BANKNIFTY"):
                if sym in self.uni.underlyings and self._every(f"next:{sym}", 600):
                    try:
                        self._next_expiry_chain(sym)
                    except PermissionError:
                        raise
                    except Exception as e:
                        self.log(f"Next-expiry chain {sym} failed: {e}")
            self._compute_all(now, mins, emit=True, lc=lc)
            try:
                self.charts.scan(now, lambda a, now=now, lc=lc: self._emit(a, now, lc))
            except Exception as e:
                if self._every("setup_err", 300, clock_time=False):
                    self.log(f"Setup scan error: {e}")
            try:
                self.desk.tick(now)
            except Exception as e:
                if self._every("desk_err", 300, clock_time=False):
                    self.log(f"Strategy desk error: {e}")
            self._big_prints(now, lc)
            self.tracker.update(self.state, self.clock.ts())
            self.paper.update(self.state, now)
            self._flat_by_reminder(now, lc)
            if self._every("pulse", 5, clock_time=False):
                self._market_view()
                self._rule_pulse()
                self._watchdog(now)
            if self._every("stats", 300, clock_time=False):
                self._refresh_stats()
            aic = self.cfg().get("ai") or {}
            every = int(aic.get("brain_every_min") or 10) * 60 if self.ai.enabled() else 300
            if mins >= 3 and self._every("brain", every, clock_time=False):
                threading.Thread(target=self._brain_safe, daemon=True).start()
            if self._every("ai_pulse", int((self.cfg().get("ai") or {}).get("pulse_every_min") or 15) * 60, clock_time=False) \
                    and self.ai.enabled() and mins >= 5:
                threading.Thread(target=self._ai_pulse, daemon=True).start()
            self.status["loop_ms"] = round((time.time() - t0) * 1000)
            self.stop_evt.wait(max(0.2, 1.0 - (time.time() - t0)))

    def _end_of_day(self, now, mins):
        self.paper.update(self.state, now, force_close=True)
        try:
            self.desk.end_of_day(now)
        except Exception as e:
            self.log(f"Strategy desk close failed: {e}")
        self._compute_all(now, mins, emit=False)
        self._market_view()
        self.tracker.update(self.state, self.clock.ts(), force=True)
        try:
            self.recorder.snapshot(int(now.timestamp() // 60) * 60, self.state, self.metrics, self.record_view)
        except Exception:
            pass
        self._save_iv_history()
        self._refresh_stats()
        self.recorder.finalize(int(self.cfg()["live"].get("keep_recordings_days") or 365), self.log)
        try:
            self.ranker.train(outcomes(self.store, self.source))
        except Exception as e:
            self.log(f"Model training failed: {e}")
        if self.cfg()["live"].get("briefs"):
            try:
                self.make_brief("post", send=self.source != "sim")
            except Exception as e:
                self.log(f"Post-market review failed: {e}")
        self.set_status("closed", "Simulation day finished" if self.source == "sim" else "Market closed for today")
        if self.feed:
            self.feed.stop()

    def _watchdog(self, now):
        if not self.feed or self.source == "sim" or not is_market_hours(now):
            return
        age = time.time() - self.feed.last_msg if self.feed.last_msg else None
        if age is not None and age > 20 and not self.watch["stalled"]:
            self.watch.update(stalled=True, since=time.time())
            self.status["warning"] = f"No live data for {int(age)} seconds. Reconnecting…"
            self.telegram.send("⚠️ Stockway: live data stopped. Check your internet or log in to Upstox again.")
        elif self.watch["stalled"] and age is not None and age < 5:
            self.watch.update(stalled=False, since=None)
            self.status.pop("warning", None)
            self.telegram.send("✅ Stockway: live data is back.")

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
                            self.avg_vol.get(sym), self.open_marks[sym], vix["ltp"] if vix else None,
                            self.daily.get(sym))
            except Exception as e:
                if self._every(f"merr:{sym}", 600, clock_time=False):
                    self.log(f"Metrics failed for {sym}: {e}")
                continue
            if not m:
                continue
            if u.is_index:
                g = self.gex.get(sym)
                if g:
                    m["gex"] = g
                nc = self.next_chains.get(sym)
                if nc and m["opt"].get("atm_iv"):
                    nxt = atm_iv_from_chain(nc, m["spot"])
                    m["vol"]["term"] = round(nxt - m["opt"]["atm_iv"], 2) if nxt else None
                    m["vol"]["next_iv"] = nxt
                if vix:
                    m["vol"]["vix_pct"] = percentile(vix["ltp"], (self.daily.get("_vix") or {}).get("closes") or [])
                m["regime"] = self.regimes[sym] = regime(m, mins, self.open_marks[sym], vix["chg_pct"] if vix else None)
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

    def _big_prints(self, now, lc):
        prints = list(self.state.prints)
        new = prints[self.prints_seen:] if len(prints) >= self.prints_seen else prints
        self.prints_seen = len(prints)
        for ts, key, qty, px, side in new:
            kind, sym, _ = self.uni.by_key.get(key, (None, None, None))
            if not sym or not side:
                continue
            u = self.uni.underlyings[sym]
            val = qty * px
            if (u.is_index and val < 25e7) or (not u.is_index and val < 5e7):
                continue
            m = self.metrics.get(sym) or {"sym": sym, "spot": px}
            what = "futures" if kind == "FUT" else "shares"
            a = {"sym": sym, "kind": "big_print", "direction": "up" if side > 0 else "down",
                 "severity": 2 if val >= (1e9 if u.is_index else 2.5e8) else 1,
                 "title": f"₹{val / 1e7:,.1f} cr single {'buy' if side > 0 else 'sell'} in {sym} {what}",
                 "detail": f"{qty:,.0f} {'units' if kind == 'FUT' else 'shares'} at {px:,.2f}, {'hitting the ask' if side > 0 else 'hitting the bid'}. "
                           "One large player acting in a hurry.", "price": m.get("spot"), "data": {"qty": qty, "px": px}}
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

    def _ctx_for(self, sym, m):
        v = (m or {}).get("vol") or {}
        return {"iv_hv": v.get("iv_hv"), "iv_pct": v.get("iv_pct") if v.get("iv_pct") is not None else v.get("vix_pct"),
                "gex": self.gex.get(sym),
                "regime": (self.regimes.get(sym) or self.regimes.get("NIFTY") or {}).get("label"), "now": self.clock.now()}

    def _emit(self, a, now, lc):
        ts = now.timestamp()
        key = (a["sym"], a["kind"], None if a["kind"] in detectors.NO_DIRECTION_COOLDOWN else a["direction"])
        last = self.cool.get(key)
        cool = detectors.COOLDOWN.get(a["kind"], float(lc["cooldown_min"]))
        if last and ts - last[0] < cool * 60 and a["severity"] <= last[1]:
            return
        self.cool[key] = (ts, a["severity"])
        u = self.uni.underlyings.get(a["sym"]) if self.uni else None
        m = self.metrics.get(a["sym"])
        mins = minutes_since_open(now)
        a["is_index"] = bool(u and u.is_index)
        a["und_key"] = (u.key if self.state.get(u.key) and self.state.get(u.key).ltp else u.fut_key) if u else None
        a.update(ts=ts, day=now.date().isoformat(), source=self.source, option=self._suggest(a["sym"], a["direction"]))
        # evidence, levels, structure, track record, score
        reg = self.regimes.get(a["sym"]) or self.regimes.get("NIFTY")
        ev = evidence(m, a["direction"], self.metrics.get("NIFTY")) if m else []
        ctx = self._ctx_for(a["sym"], m)
        st = self.stats.get((a["kind"], int(a["is_index"]))) or self.stats.get((a["kind"],))
        feats = features(a, m, ev, reg, mins, ctx)
        p = self.ranker.predict(feats) if self.ranker else None
        sc = signal_score(a["severity"], ev, st, regime_adjust((reg or {}).get("code"), a["kind"], a["direction"]), p)
        muted = None
        if st and st.get("n", 0) >= 30 and st.get("hit_rate") is not None and st["hit_rate"] < float(lc["mute_hit_rate"]) \
                and a["direction"] in ("up", "down"):
            muted = f"Demoted: this alert type has worked only {st['hit_rate']}% of {st['n']} times."
            a["severity"] = max(1, a["severity"] - 1)
        lv_setup = (a.get("data") or {}).get("setup_levels")
        a["data"] = {**(a.get("data") or {}), "evidence": ev,
                     "levels": lv_setup or (levels(m, a["direction"], self.state) if m else {}),
                     "structure": structure(a["direction"], m, ctx) if m else None,
                     "track": st, "score": sc, "model_p": round(p * 100) if p is not None else None, "muted": muted,
                     "regime": (reg or {}).get("label")}
        # risk gate: PASS / BLOCK with reasons and lots, before anything can become a paper trade
        try:
            if a.get("option") and self.paper:
                inst = self.state.get(a["option"]["key"])
                if inst:
                    a["option"]["bid"], a["option"]["ask"] = inst.bid, inst.ask or a["option"].get("ask")
                ban = (self.context.view().get("ban") or []) if self.context else []
                a["data"]["risk"] = riskgate.check(a, a["option"], now=now, paper_cfg=self.paper.cfg, guard=self.paper.guard(now),
                                                   open_syms={t["sym"] for t in self.paper.open_trades()}, ban=ban,
                                                   cfg=(lc.get("risk") or {}))
        except Exception as e:
            self.log(f"Risk gate failed: {e}")
        # stories: alerts on the same symbol within 30 minutes are one developing story
        s = self.stories.get(a["sym"])
        new_story = not s or ts - s["last"] > STORY_GAP_MIN * 60
        a["id"] = self.store.add_alert(a)
        if new_story:
            s = self.stories[a["sym"]] = {"id": a["id"], "first": ts, "last": ts, "n": 0}
        s["last"] = ts
        s["n"] += 1
        a["story"], a["story_n"] = s["id"], s["n"]
        self.store.set_alert_story(a["id"], s["id"])
        a["ai"] = None
        self.alerts_mem.appendleft(a)
        if a["severity"] >= int(lc["telegram_min_severity"]) and not muted:
            self.telegram.send(telegram_text(a))
        aic = self.cfg().get("ai") or {}
        if self.ai.enabled() and a["severity"] >= int(aic.get("explain_min_severity") or 3):
            threading.Thread(target=self.explain, args=(a["id"],), daemon=True).start()
        try:
            self.tracker.add(a, m, self.state, self.clock.ts(), feats)
        except Exception as e:
            self.log(f"Tracking failed: {e}")
        if not muted:
            try:
                self.paper.try_open(a, self.state, now)
            except Exception as e:
                self.log(f"Paper trade failed: {e}")

    def _refresh_stats(self):
        try:
            self.stats = scorecard(self.store, self.source)
        except Exception as e:
            self.log(f"Scorecard failed: {e}")

    def _brain_safe(self):
        try:
            note = self.brain.run()
            if note and note.get("by") == "AI" and int((self.cfg().get("ai") or {}).get("brain_telegram") or 0):
                self.telegram.send(f"🧠 <b>{note.get('headline')}</b>\n" + "\n".join("• " + x for x in note.get("summary", [])[:4]))
        except Exception as e:
            self.log(f"Market brain failed: {e}")

    def _macro_alert(self, a):
        if self.running and self.source != "sim":
            self._emit(a, self.clock.now(), self.cfg()["live"])
        elif self.running:
            a["title"] = "[real market] " + a["title"]
            self._emit(a, self.clock.now(), self.cfg()["live"])

    def _world_loop(self):
        time.sleep(8)
        t = collections.defaultdict(float)
        while True:
            now = time.time()
            hour = dt.datetime.now(IST).hour
            awake = 6 <= hour <= 23
            def due(k, sec):
                if now - t[k] >= sec:
                    t[k] = now
                    return True
                return False
            live_every = max(30, int(((self.cfg().get("world") or {}).get("live_every_sec")) or 60))
            if self.world.errors.get("live"):
                live_every = min(600, live_every * 4)          # Yahoo pushing back: slow down
            for k, sec, fn in (("live", live_every, self.world.refresh_live), ("prices", 900, self.world.refresh_prices),
                               ("corr", 6 * 3600, self.world.refresh_correlations),
                               ("calendar", 2 * 3600, self._world_calendar), ("results", 6 * 3600, self._world_results),
                               ("news", 300, self.world.refresh_news)):
                if (awake or not self.world.updated.get(k) or k == "live") and due(k, sec):
                    try:
                        fn()
                        self.world.errors.pop(k, None)
                    except Exception as e:
                        self.world.errors[k] = str(e)[:160]
            try:
                self.world.check_events()
            except Exception:
                pass
            if due("scenario_hist", 3 * 3600):
                threading.Thread(target=self._scenario_refresh, daemon=True).start()
            if 7 <= hour <= 22 and due("insiders", 3 * 3600):
                threading.Thread(target=self._insiders_refresh, daemon=True).start()
            time.sleep(5)

    def _flat_by_reminder(self, now, lc):
        """Five minutes before the square-off time: say what is still open (once a day)."""
        ex = (self.paper.cfg.get("exit_time") if self.paper else None) or "15:15"
        h, m = map(int, ex.split(":"))
        warn_at = (dt.datetime.combine(now.date(), dt.time(h, m)) - dt.timedelta(minutes=5)).strftime("%H:%M")
        day = now.date().isoformat()
        if now.strftime("%H:%M") < warn_at or getattr(self, "_flat_warned", None) == day:
            return
        self._flat_warned = day
        opens = self.paper.open_trades() if self.paper else []
        desk_open = []
        try:
            desk_open = [t for t in self.desk.view()["today"] if t["status"] == "open"]
        except Exception:
            pass
        if not opens and not desk_open:
            return
        names = ", ".join([t["label"] for t in opens][:6] + [f"{t['family']} {t['sym']}" for t in desk_open][:3])
        self._emit({"sym": "PORTFOLIO", "kind": "flat_by", "direction": "neutral", "severity": 2,
                    "title": f"Square-off at {ex}: {len(opens) + len(desk_open)} paper position(s) still open",
                    "detail": f"Open: {names}. Intraday positions close at {ex}; nothing is carried overnight.",
                    "price": None, "data": {}}, now, lc)

    def _scenario_refresh(self, force=False):
        """Keep the scenario engine's 1-minute index history current (needs an Upstox login) and re-test it daily."""
        tok = self.tokens.load()
        sc = self.scenarios
        try:
            if tok:
                before = {s: sc._file(s).exists() for s in ("NIFTY", "BANKNIFTY")}
                sc.ensure_history(tok["access_token"], log=self.log, force=force)
                ev_age = time.time() - ((sc.eval or {}).get("_ts") or 0)
                if force or not sc.eval or ev_age > 20 * 3600 or not all(before.values()):
                    sc.run_eval(log=self.log)
        except Exception as ex:
            self.log(f"Scenario engine refresh failed: {ex}")

    def _world_calendar(self):
        from lab.strategies import EVENTS
        today = dt.datetime.now(IST).date()
        extra = [(dt.date.fromisoformat(d), "India: scheduled policy / budget event") for d in EVENTS
                 if today <= dt.date.fromisoformat(d) <= today + dt.timedelta(days=7)]
        if self.uni:
            exps = sorted({u.near_expiry(today) for u in self.uni.indices if u.near_expiry(today)})
            extra += [(d, "Index option expiry") for d in exps if d <= today + dt.timedelta(days=7)]
        self.world.refresh_calendar(extra_events=extra)

    def _world_results(self):
        fo = set(self.uni.underlyings) if self.uni and self.source != "sim" else \
            set(((self.context.mwpl or {}).get("symbols") or {}).keys())
        self.world.refresh_results(self.context.nse, fo)

    def _insiders_refresh(self):
        fo = set(self.uni.underlyings) if self.uni and self.source != "sim" else \
            set(((self.context.mwpl or {}).get("symbols") or {}).keys())
        fo = {x for x in fo if x not in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY")}
        if not fo:
            return
        try:
            new = self.insiders.refresh(fo, log=self.log) or []
        except Exception as e:
            self.log(f"Insider trades refresh failed: {e}")
            return
        for x in new:
            buy = x["side"] == "Buy"
            cr = x["value"] / 1e7
            a = {"sym": x["sym"], "kind": "insider", "direction": "up" if buy else "down",
                 "severity": 3 if ("promoter" in x["category"].lower() and buy and cr >= 10) else 2,
                 "title": f"{x['sym']}: {x['category'].lower()} {'bought' if buy else 'sold'} ₹{cr:,.1f} cr of shares",
                 "detail": f"{x['person']} ({x['category']}), {x['mode'].lower()}, traded {x['traded']}, disclosed {x['days_to_file']} day(s) later. "
                           f"Holding {x['before_pct']:.2f}% → {x['after_pct']:.2f}%. Public disclosure under SEBI's insider-trading rules.",
                 "price": (self.metrics.get(x["sym"]) or {}).get("spot"), "data": {"link": x.get("xbrl")}}
            self._macro_alert(a)

    def _filing_alert(self, f):
        if not self.running or self.source == "sim" or not self.uni or f["sym"] not in self.uni.underlyings:
            return
        m = self.metrics.get(f["sym"]) or {}
        d = "up" if f["tone"] > 0 else "down" if f["tone"] < 0 else "neutral"
        a = {"sym": f["sym"], "kind": "filing", "direction": d, "severity": f["severity"],
             "title": f"{f['sym']} filing: {f['label']}", "detail": (f.get("text") or f.get("desc") or "")[:220],
             "price": m.get("spot"), "data": {"link": f.get("link"), "desc": f.get("desc")}}
        self._emit(a, self.clock.now(), self.cfg()["live"])

    # ------------------------------------------------------------ market-wide views
    def _market_view(self):
        st = [m for m in self.metrics.values() if not m["is_index"]]
        if not st:
            return
        adv = sum(1 for m in st if (m.get("chg_pct") or 0) > 0)
        dec = sum(1 for m in st if (m.get("chg_pct") or 0) < 0)
        above = sum(1 for m in st if (m.get("vs_vwap_pct") or 0) > 0)
        bu = collections.Counter((m.get("fut") or {}).get("buildup") for m in st)
        highs = sum(1 for m in st if m.get("day_h") and m["spot"] >= m["day_h"] * 0.999)
        lows = sum(1 for m in st if m.get("day_l") and m["spot"] <= m["day_l"] * 1.001)
        sectors = {}
        for m in st:
            sec = self.context.sector_of(m["sym"])
            x = sectors.setdefault(sec, {"sector": sec, "n": 0, "sum": 0.0, "up": 0, "best": None, "worst": None})
            c = m.get("chg_pct") or 0
            x["n"] += 1
            x["sum"] += c
            x["up"] += c > 0
            if x["best"] is None or c > x["best"][1]:
                x["best"] = (m["sym"], c)
            if x["worst"] is None or c < x["worst"][1]:
                x["worst"] = (m["sym"], c)
        secs = sorted([{**x, "chg": round(x["sum"] / x["n"], 2)} for x in sectors.values()], key=lambda x: -x["chg"])
        contrib = {}
        for idx, w in self.context.weights.items():
            im = self.metrics.get(idx)
            if not im or not im.get("cp"):
                continue
            tot = sum(v for s, v in w.items() if s in self.metrics) or 1
            rows = []
            for s, wt in w.items():
                sm = self.metrics.get(s)
                if sm and sm.get("chg_pct") is not None:
                    rows.append({"sym": s, "weight": round(wt / tot * 100, 1), "chg_pct": sm["chg_pct"],
                                 "points": round(wt / tot * sm["chg_pct"] / 100 * im["cp"], 1)})
            rows.sort(key=lambda r: -abs(r["points"]))
            contrib[idx] = {"rows": rows[:10], "explained": round(sum(r["points"] for r in rows), 1),
                            "actual": round(im["spot"] - im["cp"], 1)}
        self.market = {"breadth": {"adv": adv, "dec": dec, "n": len(st), "above_vwap": above,
                                   "long_buildup": bu.get("long_buildup", 0), "short_buildup": bu.get("short_buildup", 0),
                                   "short_covering": bu.get("short_covering", 0), "long_unwinding": bu.get("long_unwinding", 0),
                                   "at_high": highs, "at_low": lows},
                       "sectors": secs, "contrib": contrib, "regime": self.regimes.get("NIFTY")}

    def _movers(self):
        st = [m for m in self.metrics.values() if not m["is_index"]]
        def k(f):
            return lambda m: (m.get(f) if m.get(f) is not None else -1e9)
        return {"gainers": [self.slim(m) for m in sorted(st, key=k("chg_pct"), reverse=True)[:8]],
                "losers": [self.slim(m) for m in sorted(st, key=lambda m: m.get("chg_pct") if m.get("chg_pct") is not None else 1e9)[:8]],
                "hot": [self.slim(m) for m in sorted(st, key=k("heat"), reverse=True)[:8]]}

    def _indices(self):
        order = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"]
        return [self.metrics[s] for s in order if s in self.metrics]

    def _rule_pulse(self):
        lines = rule_pulse(self._indices(), list(self.alerts_mem)[:5], self._movers(), self._vix())
        r = self.regimes.get("NIFTY")
        if r and r.get("code") != "opening":
            lines.insert(1, f"Day type: {r['label']}. {r['detail']}")
        b = (self.market or {}).get("breadth")
        if b:
            lines.insert(2, f"Breadth: {b['adv']} up, {b['dec']} down; {b['above_vwap']} of {b['n']} above VWAP.")
        self.pulse["lines"] = lines
        self.pulse["rule_ts"] = time.time()

    def _ai_pulse(self):
        snap = {"time": self.clock.now().strftime("%H:%M"), "vix": self._vix(),
                "indices": [self._slim_for_ai(m) for m in self._indices()],
                "regime": self.regimes.get("NIFTY"), "breadth": (self.market or {}).get("breadth"),
                "sectors": [{k: s[k] for k in ("sector", "chg")} for s in (self.market or {}).get("sectors", [])[:4]],
                "gamma": {s: {k: g.get(k) for k in ("net_cr", "flip", "regime")} for s, g in self.gex.items()},
                "top_movers": [{k: m.get(k) for k in ("sym", "chg_pct", "rel_vol", "score")} for m in self._movers()["hot"][:6]],
                "recent_alerts": [a["title"] for a in list(self.alerts_mem)[:10]],
                "headlines": [n["title"] for n in self.news.items[:8]]}
        text = self.ai.ask(pulse_prompt(snap), 350)
        if text:
            self.pulse.update(ai=text, ts=time.time())

    def _slim_for_ai(self, m):
        from .notify import _slim_metrics
        return _slim_metrics(m)

    # ------------------------------------------------------------ briefs
    def _last_levels(self) -> dict:
        if self.metrics and self.source != "sim":
            src = {s: self.index_view(self.metrics[s]) for s in ("NIFTY", "BANKNIFTY") if s in self.metrics}
        else:
            src = self.store.get("last_levels:upstox", {}) or {}      # the morning brief only uses real-market levels
        return {s: {"name": v.get("name"), "spot": v.get("spot"), "day_h": v.get("day_h"), "day_l": v.get("day_l"),
                    "opt": {k: (v.get("opt") or {}).get(k) for k in ("support", "resistance", "max_pain", "pcr")}}
                for s, v in src.items()}

    def make_brief(self, kind: str, send=True) -> dict:
        day = (self.clock.now() if self.running or self.metrics else dt.datetime.now(IST)).date().isoformat()
        if kind == "pre":
            facts = briefs.premarket_facts(self.context.view(), self._last_levels())
        else:
            ps = self.paper.summary(self.state) if self.paper else {}
            facts = briefs.postmarket_facts(self._indices(), self.regimes.get("NIFTY"),
                                            outcomes(self.store, self.source, day=day), ps, self._movers(),
                                            [a for a in self.alerts_mem if a["day"] == day])
            self.store.put(f"last_levels:{self.source}", {s: self.index_view(self.metrics[s])
                                                          for s in ("NIFTY", "BANKNIFTY") if s in self.metrics})
        b = briefs.make(kind, facts, self.ai)
        b["day"] = day
        b["source"] = self.source if kind == "post" else "market"
        self.store.put(f"brief:{day}:{kind}", b)
        days = self.store.get("brief_days", []) or []
        if day not in days:
            self.store.put("brief_days", ([day] + days)[:60])
        if send:
            self.telegram.send(briefs.telegram_text(b))
        return b

    def briefs_view(self, day=None) -> dict:
        days = self.store.get("brief_days", []) or []
        day = day or (days[0] if days else dt.datetime.now(IST).date().isoformat())
        return {"day": day, "days": days, "pre": self.store.get(f"brief:{day}:pre"),
                "post": self.store.get(f"brief:{day}:post")}

    # ------------------------------------------------------------ background: watcher + news
    def _watch(self):
        """Auto-start at 9:00 on market days when logged in; remind to log in; briefs at 8:45 and 15:40."""
        while True:
            time.sleep(20)
            try:
                c = self.cfg()["live"]
                now = dt.datetime.now(IST)
                today = now.date().isoformat()
                if now.weekday() < 5 and c.get("briefs"):
                    if dt.time(8, 45) <= now.time() < dt.time(9, 15) and ("pre", today) not in self.brief_done:
                        self.brief_done.add(("pre", today))
                        self.context.refresh_daily()
                        self.context.refresh_global()
                        self.make_brief("pre")
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
        fl = m.get("flow") or {}
        v = m.get("vol") or {}
        return {"sym": m["sym"], "name": m["name"], "is_index": m["is_index"], "spot": m["spot"], "chg_pct": m.get("chg_pct"),
                "rel_vol": m.get("rel_vol"), "ret_5m": m.get("ret_5m"), "ret_15m": m.get("ret_15m"),
                "vs_vwap_pct": m.get("vs_vwap_pct"), "day_h": m.get("day_h"), "day_l": m.get("day_l"),
                "buildup": f.get("buildup_label"), "buildup_sign": f.get("buildup_sign"), "fut_oi_chg": f.get("oi_chg_pct"),
                "atm_iv": o.get("atm_iv"), "iv_chg_15m": o.get("iv_chg_15m"), "pcr": o.get("pcr"),
                "iv_hv": v.get("iv_hv"), "flow": fl.get("delta_pct_5m"),
                "unusual": len(o.get("unusual") or []), "score": m.get("score"), "heat": m.get("heat"), "lot": m.get("lot")}

    def record_view(self, m) -> dict:
        x = self.slim(m)
        o = m.get("opt") or {}
        x.update({k: o.get(k) for k in ("max_pain", "support", "resistance", "straddle")}, vwap=m.get("vwap"),
                 regime=(m.get("regime") or {}).get("code"), gex=(m.get("gex") or {}).get("net_cr"))
        return x

    @staticmethod
    def index_view(m) -> dict:
        o = dict(m.get("opt") or {})
        o.pop("rows", None)
        g = dict(m.get("gex") or {})
        g.pop("profile", None)
        return {**{k: v for k, v in m.items() if k not in ("opt", "gex")}, "opt": o, "gex": g or None}

    def snapshot(self, since_id=0) -> dict:
        now = self.clock.now()
        feed = None
        if self.feed:
            feed = {"connected": self.feed.connected, "msgs": self.feed.msgs,
                    "last_msg_age": round(time.time() - self.feed.last_msg, 1) if self.feed.last_msg else None,
                    "subs": self.feed.counts(), "lag_ms": round(getattr(self.feed, "lag_ms", None) or 0) if getattr(self.feed, "lag_ms", None) is not None else None}
        new = [a for a in self.alerts_mem if a["id"] > since_id][:100]
        ps = self.paper.summary(self.state) if self.paper else None
        cv = self.context.view()
        return {"status": {**self.status, "running": self.running, "logged_in": bool(self.tokens.load()),
                           "feed": feed, "ticks": self.state.tick_count if self.state else 0},
                "clock": now.isoformat(), "mins": minutes_since_open(now),
                "indices": [self.index_view(m) for m in self._indices()], "vix": self._vix(),
                "alerts": new, "pulse": self.pulse, "movers": self._movers() if self.metrics else None,
                "market": self.market,
                "context": {"fii_dii": cv["fii_dii"], "global": cv["global"], "ban": cv["ban"],
                            "fii": next((r for r in ((cv["participants"] or {}).get("rows") or []) if r["who"] == "FII"), None)},
                "paper": {"open": len(ps["open"]), "net": ps["net"], "unreal": ps["unreal"], "n": ps["n"],
                          "win_rate": ps["win_rate"], "guard": ps.get("guard")} if ps else None,
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
        g = self.gex.get(sym)
        if not g and chain and not expiry:
            g = gex(chain, m["spot"], self.clock.now())
        bars = self.state.bars.get(sym)
        u = self.uni.underlyings[sym]
        keys = {u.key, u.fut_key}
        prints = [{"ts": ts, "qty": q, "price": p, "side": s, "what": "futures" if k == u.fut_key else "shares"}
                  for ts, k, q, p, s in list(self.state.prints) if k in keys][-15:]
        ctx = self._ctx_for(sym, m)
        return {"metrics": m, "chain": chain, "gex": g, "regime": self.regimes.get(sym),
                "structure": {"up": structure("up", m, ctx), "down": structure("down", m, ctx)},
                "levels": {"up": levels(m, "up", self.state), "down": levels(m, "down", self.state)},
                "daily": {k: v for k, v in (self.daily.get(sym) or {}).items() if k != "iv_hist"},
                "prints": prints, "sector": self.context.sector_of(sym),
                "bars": [b[:6] for b in bars.bars] if bars else [],
                "alerts": [a for a in self.alerts_mem if a["sym"] == sym][:30],
                "filings": [f for f in self.context.filings if f["sym"] == sym][:10],
                "news": [n for n in self.news.items if sym in n["syms"]][:10]}

    def scorecard_view(self, source=None) -> dict:
        src = source or self.source or self.cfg()["live"]["source"]
        sc = scorecard(self.store, src)
        rows = []
        for k, v in sc.items():
            if len(k) == 2:
                rows.append({"kind": k[0], "is_index": bool(k[1]), **v})
        rows.sort(key=lambda r: (-(r["n"] >= 10), -(r["hit_rate"] or 0)))
        recent = outcomes(self.store, src, limit=200)
        titles = {}
        if recent:
            ids = [r["alert_id"] for r in recent]
            with self.store.lock:
                q = f"SELECT id, title FROM alerts WHERE id IN ({','.join('?' * len(ids))})"
                titles = dict(self.store.db.execute(q, ids).fetchall())
        for r in recent:
            r["title"] = titles.get(r["alert_id"])
            r.pop("features", None)
        graded = [r for r in recent if r["result"]]
        ranker = self.ranker or Ranker(self.data_dir, src, self.log)
        return {"source": src, "rows": rows, "recent": recent[:120], "graded": len(graded),
                "overall": round(sum(r["result"] == "hit" for r in graded) / len(graded) * 100) if graded else None,
                "model": ranker.info, "recordings_mb": size_mb(self.data_dir),
                "rule": "With an option idea: hit = option +20% before −20% within 60 minutes. Without: price moves "
                        "about one 30-minute sigma in the alert's direction before the same against it. Neither = flat."}
