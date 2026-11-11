"""Live engine: feed -> state -> metrics -> alerts -> scoring, notifications, paper trades, briefs."""
from __future__ import annotations

import collections
import datetime as dt
import json
import threading
import time
import traceback

import numpy as np
from pathlib import Path

from . import briefs, dayreport, detectors, htf, registry
from . import session as SESSION                  # 2.48: the NSE calendar
from .giftnifty import GiftNifty
from .analytics import compute, norm_iv, pct
from .clock import IST, Clock, SimClock, is_market_hours, minutes_since_open
from .context import Context
from .learn import Ranker, features, regime, regime_adjust
from .notify import AI, News, Telegram, alert_prompt, match_symbols, pulse_prompt, rule_pulse, symbol_words, telegram_text
from .paper import PaperBook
from .record import Recorder, Tracker, evidence, levels, outcomes, scorecard, signal_score, size_mb
from .state import MarketState
from .store import Store
from .universe import SENSEX_KEY, VIX_KEY, atm_window
from . import riskgate
from .upstox import TokenStore
from .vol import atm_iv_from_chain, daily_stats, gex, percentile, structure

_LIQUID = ["RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "AXISBANK", "KOTAKBANK", "LT", "ITC",
          "BHARTIARTL", "BAJFINANCE", "MARUTI", "TATAMOTORS", "M&M", "SUNPHARMA", "TITAN", "ADANIENT", "ADANIPORTS",
          "TATASTEEL", "JSWSTEEL", "HCLTECH", "HAL", "BEL", "DIXON", "ETERNAL", "ZOMATO", "TRENT", "BAJAJ-AUTO",
          "HINDALCO", "VEDL", "DLF", "INDUSINDBK", "PNB", "CANBK", "BANKBARODA", "COALINDIA", "ONGC", "NTPC",
          "POWERGRID", "TECHM", "WIPRO", "SWIGGY", "PAYTM", "IRFC", "BSE", "CDSL", "ANGELONE", "MCX", "POLYCAB"]
try:                                          # rules/stocks.json "liquid" (the built-in list is the fallback)
    from lab.rules import stock_list as _sl
    LIQUID = _sl("liquid", _LIQUID)
except Exception:
    LIQUID = _LIQUID

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
    "mute_hit_rate": 45,
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
        from .kite import KiteTokenStore
        self.kite_tokens = KiteTokenStore(self.data_dir / "kite_token.json")     # 2.52: Kite backup feed (market data only)
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
        self.closed_day = None                       # 2.48: the day the session ended (yesterday's 'closed' never blocks today)
        self.reminded_day = None
        self.brief_done = set()
        self.status = {"state": "stopped", "message": "Not started", "source": None, "since": time.time()}
        Tracker(self.store, "init")              # make sure the outcomes table exists
        self._reset()
        from lab.live import StrategyDesk
        self.desk = StrategyDesk(self)
        from lab.expiry_desk import ExpiryDesk
        self.expiry = ExpiryDesk(self)
        try:                                         # 2.33: is the app really collecting data? (live/health.py)
            from .health import Health
            self.health = Health(self)
        except Exception as e:
            self.health = None
            self.log(f"Health monitor off: {e}")
        try:                                         # 2.35: official feeds + event risk on every alert (live/events.py)
            from .events import EventEngine
            self.events = EventEngine(self)
            self.events.start()
        except Exception as e:
            self.events = None
            self.log(f"Event engine off: {e}")
        try:                                         # 2.40: India market indication every 15 min (live/indication.py)
            from .indication import Indication
            self.indication = Indication(self)
        except Exception as e:
            self.indication = None
            self.log(f"Market indication off: {e}")
        threading.Thread(target=self._watch_loop, daemon=True, name="live-watch").start()
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
        try:                                         # 2.42: kept on disk too (data/logs), so a crash or restart keeps its reason
            from .applog import write
            write(self.data_dir, line)
        except Exception:
            pass

    def set_status(self, state, message):
        self.status.update(state=state, message=message, since=time.time())
        self.log(message)

    def _reset(self):
        self.trading_open = False                        # 2.48: set by the loop from the NSE calendar
        self.clock = Clock()
        self.state = None
        self.uni = None
        self.rest = None
        self.feed = None
        self.paper = None
        self.books = None
        self.source = None
        self.recorder = None
        self.tracker = None
        self.shadow = None                           # exit styles graded in shadow on every alert (live/shadow.py)
        self.linegrade = None                        # 2.54: smart chart lines graded on every touch (live/linegrade.py)
        self.trials = None                           # bullets on trial: recorded and graded only (live/trials.py)
        self.bench = None                            # an honest paper trade for every signal (live/bench.py)
        self.focuslab = None                         # 2.37: shadow Focus challengers, recorded only (live/focuslab.py)
        self.in_play: set = set()                   # 2.31: stocks the mover sweep tested today (library rules run on them)
        self._facts_cache: dict = {}                 # (sym, direction, minute) -> facts for test-bench trades
        self.mover_last = None                       # the last mover sweep (live/movers.py)
        self.vault_last = None                       # the last stock option-chain sweep (data vault)
        self.cdl = None                              # 2.31: candle patterns on every timeframe (live/mtfcandles.py)
        self.dguard = None                           # 2.33: bad ticks / crossed / stale prices (live/dataguard.py)
        self._run_token = None
        self.depth = None                            # Dhan 200-level order book for index futures (live/dhandepth.py)
        self.dhan_intel = None                       # Dhan news headlines + market movers (live/dhanintel.py)
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
        self._explaining = threading.Lock()
        self.pinned: set = set()                     # option keys you traded by hand: kept streaming
        self.gift = GiftNifty(self.log)
        self.fact_bars: dict = {}                    # (sym, day) -> {bar: (atm iv, straddle %)} for Strategy Lab rules
        self.fact_fired: dict = {}                   # (rule, sym, day) -> times fired
        self._fact_rules = (0.0, [])
        self.VIX_KEY = VIX_KEY
        self.focus_last = {}
        self._mctx, self._mctx_at = {}, 0.0
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
        if self.depth:
            try:
                self.depth.stop()
            except Exception:
                pass
            self.depth = None
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
                    if self.shadow:
                        self.shadow.update(self.state, self.clock.ts(), force=True)
                    if self.trials is not None:
                        self.trials.update(self.state, self.clock.ts(), force=True)
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
            n0 = dt.datetime.now(IST)                # 2.48: no NSE session today (holiday / weekend) or it is over
            ses = SESSION.session(n0.date())
            if ses is None or n0 >= ses["close"]:
                nxt = SESSION.next_session(n0 + dt.timedelta(minutes=1))
                self.closed_day = n0.date()
                why = "NSE holiday today" if SESSION.is_holiday(n0.date()) else "No NSE session today" if ses is None \
                    else "Today's session is over"
                self.set_status("closed", why + (f". Next: {nxt['name'].lower()} {nxt['open']:%a %d %b %H:%M}, the scanner "
                                                 f"starts by itself at {nxt['start_scanner']:%H:%M}." if nxt else "."))
                return
            self.clock = Clock()
            self.uni = universe.load(str(self.data_dir), log=self.log)
            self.rest = UpstoxREST(tok["access_token"])
            prof = self.rest.profile() or {}
            self.status["user"] = prof.get("user_name") or tok.get("user")
            make_feed = lambda: self._make_router(tok)       # 2.52: Upstox main + Kite backup / cross-check
        day = self.clock.now().date().isoformat()
        self.paper = PaperBook(self.store, self.source, lc.get("paper"), self.log, on_open=self._keep_live)
        try:                                         # 2.49: extra paper accounts (Best only, 3% a day); never block the main one
            from .books import Books
            self.books = Books(self.source, self.store, self.paper.cfg, lc.get("books"), self.log, on_open=self._keep_live,
                               risk_for=self.risk_for, data_dir=self.data_dir)
        except Exception as e:
            self.books = None
            self.log(f"Extra paper accounts not loaded: {e}")
        self.state = MarketState()
        self.state.clock = self.clock
        self.state.und_keys = {u.key: s for s, u in self.uni.underlyings.items()}
        self.state.flow_keys = {u.key for u in self.uni.stocks} | {u.fut_key for u in self.uni.underlyings.values() if u.fut_key}
        self.recorder = Recorder(self.data_dir, self.source, day)
        self.tracker = Tracker(self.store, self.source)
        from .shadow import ShadowExits
        self.shadow = ShadowExits(self.store, self.source, self.log)
        try:                                         # 2.54: smart chart lines, graded in shadow (display only)
            from .linegrade import LineGrader
            self.linegrade = LineGrader(self, self.store, self.source, self.log)
        except Exception as e:
            self.linegrade = None
            self.log(f"Smart lines grading off: {e}")
        try:
            from .trials import Trials
            self.trials = Trials(self.store, self.source, self.log)
        except Exception as e:
            self.log(f"Trial alerts off: {e}")
        try:
            from .bench import Bench
            self.bench = Bench(self.store, self.source, self.log)
        except Exception as e:
            self.log(f"Test bench off: {e}")
        try:                                         # 2.37: shadow Focus (recorded next to every alert, changes nothing)
            from .focuslab import FocusLab
            self.focuslab = FocusLab(self.store, self.source, self.log)
            self.focuslab.warm(day)
        except Exception as e:
            self.focuslab = None
            self.log(f"Shadow Focus off: {e}")
        self.in_play, self._facts_cache, self.mover_last = set(), {}, None
        try:
            from .dataguard import DataGuard
            self.dguard = DataGuard(self)
        except Exception as e:
            self.dguard = None
            self.log(f"Data quality guard off: {e}")
        try:
            from .mtfcandles import CandleScanner
            self.cdl = CandleScanner(self)
        except Exception as e:
            self.cdl = None
            self.log(f"Candle scanner off: {e}")
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
        self._start_depth()
        if self.source != "sim":                  # Sensex, live from Upstox (BSE index); optional, never blocks the start
            try:
                self._seed(self.rest.quotes([SENSEX_KEY]))
                self.feed.subscribe([SENSEX_KEY], "ltpc")
            except Exception as e:
                self.log(f"Sensex live price not available ({e}); using Yahoo")
        self.daily = self.store.get(f"daily:{self.source}:{day}", {}) or {}
        self.avg_vol = {s: d.get("avg_vol") for s, d in self.daily.items() if d.get("avg_vol")}
        stale = self.source != "sim" and not self._daily_fresh(self.daily, self.clock.now().date())
        if len(self.daily) < len(self.uni.underlyings) * 0.8 or stale or \
                sum(1 for d in self.daily.values() if isinstance(d, dict) and d.get("d_ohlc")) < len(self.uni.underlyings) * 0.8:
            threading.Thread(target=self._load_daily, kwargs={"force": stale}, daemon=True, name="daily-history").start()
        self._attach_iv_history()
        self.desk.load_plan()
        self._recenter()
        self.set_status("running", f"Live: {len(self.uni.indices)} indices, {len(self.uni.stocks)} stocks, "
                                   f"{len(self.opt_subs)} options streaming")
        token = self._run_token = object()
        threading.Thread(target=self._vault_loop, args=(token,), daemon=True, name="data-vault").start()
        threading.Thread(target=self._nextcandle_loop, args=(token,), daemon=True, name="next-candle").start()
        self._loop()

    def _seed(self, quotes: dict):
        ticks, base = [], {}
        for k, q in quotes.items():
            cp = q.get("prev_close_price") or ((q.get("last_price") or 0) - (q.get("net_change") or 0)) or None
            base[k] = {"prev_oi": q.get("previous_oi"), "cp": cp}
            o = q.get("ohlc") or {}
            t = {"key": k, "ltp": q.get("last_price"), "cp": cp, "vol": q.get("volume"), "oi": q.get("oi") or None,
                 "atp": q.get("average_price") or None}
            ltt = str(q.get("last_trade_time") or "")
            if ltt.isdigit():                            # 2.48: the exchange's last-trade time (ms)
                t["ltt"] = int(ltt)
            if is_market_hours(self.clock.now()):
                t.update(day_o=o.get("open"), day_h=o.get("high"), day_l=o.get("low"))
            ticks.append(t)
        self.state.set_baselines(base)
        self.state.apply(ticks, self.clock.ts())

    @staticmethod
    def _daily_fresh(daily: dict, day: dt.date) -> bool:
        """2.42: the daily history must end on the last trading day before `day`. Built at night it can miss yesterday
        (the broker publishes a day's daily candle hours later): the Mac's 5 Nov copy, made at 00:46, had 3 Nov as its
        'previous day', so the previous high / low / close, gaps, the daily trend and daily patterns were a day old."""
        from .health import is_trading_day
        want = day - dt.timedelta(days=1)
        for _ in range(12):
            if is_trading_day(want):
                break
            want -= dt.timedelta(days=1)
        p = ((daily or {}).get("NIFTY") or {}).get("prev") or {}
        return bool(p.get("date")) and str(p["date"]) >= want.isoformat()

    def _load_daily(self, force: bool = False):
        """One year of daily candles per symbol: average volume, realised volatility and its percentile.
        force (2.42): load every symbol again (the cached copy missed the last trading day)."""
        out = dict(self.daily)
        for u in list(self.uni.indices) + self._rank_stocks():
            if self.stop_evt.is_set():
                return
            if not force and u.symbol in out and out[u.symbol].get("d_ohlc"):
                continue
            try:
                cs = self.rest.daily_candles(u.key, 400)
                out[u.symbol] = daily_stats(cs)
                # 2.31: the last 80 daily candles, for daily / weekly candle patterns (live/mtfcandles.py)
                out[u.symbol]["d_ohlc"] = [[str(c[0])[:10], c[1], c[2], c[3], c[4], c[5]] for c in sorted(cs, key=lambda c: str(c[0]))[-80:]]
                try:                                     # the bigger picture (daily / weekly trend), finished days only
                    out[u.symbol]["htf"] = htf.daily(cs, self.clock.now().date())
                except Exception:
                    pass
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
        if self.source != "sim" and not self._daily_fresh(out, self.clock.now().date()) and not getattr(self, "_daily_retry", None):
            p = ((out.get("NIFTY") or {}).get("prev") or {}).get("date")
            self.log(f"Daily history ends on {p}: the broker has not published the last trading day yet; trying again in 30 minutes")
            token = self._run_token

            def again():
                self._daily_retry = None
                if self.running and self._run_token is token:
                    self._load_daily(force=True)
            self._daily_retry = threading.Timer(1800, again)
            self._daily_retry.daemon = True
            self._daily_retry.start()

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

    def _make_router(self, tok):
        """2.52: the Upstox feed inside the feed router (live/feeds.py). Kite is the backup and the cross-check; without
        Kite keys or today's Kite login the router is the Upstox feed exactly as before."""
        from .feeds import FeedRouter
        from .upstox import UpstoxFeed                # 2.56.1: was only imported inside _run, so the router crashed
        up = UpstoxFeed(tok["access_token"], self._on_ticks, self._feed_status)
        return FeedRouter(up, self._on_ticks, self.log, backup_factory=self._kite_backup,
                          state_get=lambda k: self.state.get(k) if self.state is not None else None,
                          is_open=lambda: SESSION.is_open(dt.datetime.now(IST)),
                          by_key=self.uni.by_key if self.uni else {},
                          mode_fn=lambda: (self.cfg().get("live") or {}).get("feeds_failover"))

    def _kite_backup(self, on_ticks, on_status):
        """The Kite feed and the Upstox key -> Kite token map (called in the router's own thread). Market data only."""
        from . import kite as K
        from .feeds import BackupNotReady, cfg as fcfg
        lc = self.cfg()["live"]
        if not lc.get("kite_api_key"):
            raise BackupNotReady("off", "Kite not set up: add the Kite Connect API key in Live setup (optional)")
        tok = self.kite_tokens.load()
        if not tok:
            raise BackupNotReady("login_needed", "Log in with Kite (once a day, Live setup) to start the backup feed")
        idx = K.kite_index(lc["kite_api_key"], tok["access_token"], self.data_dir)
        keys = list(self.uni.by_key) + [SENSEX_KEY]
        mapping = K.build_map(keys, self.uni.ex_tok, idx, fcfg().get("index_map"))
        if not mapping:
            raise RuntimeError("no instrument matched between Upstox and Kite")
        self.log(f"Kite backup: {len(mapping):,} instruments matched (market data only)")
        return K.KiteFeed(lc["kite_api_key"], tok["access_token"], on_ticks, on_status,
                          mode=(fcfg().get("kite") or {}).get("mode", "full")), mapping

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
        if getattr(self, "expiry", None):
            want |= self.expiry.needed_keys()
        if self.paper:                                   # open paper positions always keep a live price
            want |= {t["opt_key"] for t in self.paper.open_trades()}
        if getattr(self, "books", None) is not None:     # 2.49: and the extra paper accounts' positions
            want |= self.books.open_keys()
        want |= self.pinned
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
    def chain(self, sym: str, max_age=20.0, expiry=None, keep=True) -> dict | None:
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
        if self.recorder is not None:                    # 2.31 data vault: every full chain the app fetches
            try:
                self.recorder.chain(int(self.clock.ts()), res)
            except Exception:
                pass
        if not keep:                                     # the vault's sweep: recorded only, nothing the scanner uses changes
            return res
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
            if self.source == "sim":
                ended = mins >= 375
            else:                                        # 2.48: by the NSE calendar (holidays, special sessions)
                ses = SESSION.session(now.date())
                ended = ses is None or now >= ses["close"] + dt.timedelta(minutes=1)
            self.trading_open = self._trading_open(now)
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
                if getattr(self, "books", None) is not None and self.paper:
                    self.books.load(self.paper.cfg, lc.get("books"))
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
            if self.dguard is not None:              # before the alerts: a suspect price holds its symbol's alerts
                self._guard("Data quality guard", self.dguard.check, now)
            open_ = self.trading_open                # 2.48: no alert, paper or bench trade outside an open session
            self._compute_all(now, mins, emit=open_, lc=lc)
            if open_:
                try:
                    self.charts.scan(now, lambda a, now=now, lc=lc: self._emit_safe(a, now, lc))
                except Exception as e:
                    if self._every("setup_err", 300, clock_time=False):
                        self.log(f"Setup scan error: {e}")
            if self.cdl is not None and open_:
                self._guard("Candle scanner", self.cdl.tick, now)
            if self._every("vault_bars", 1800):
                self._guard("Saving 1-minute candles", self._save_bars)
            try:
                if open_:
                    self.desk.tick(now)
            except Exception as e:
                if self._every("desk_err", 300, clock_time=False):
                    self.log(f"Strategy desk error: {e}")
            try:
                if open_ and self._every("expiry_desk", 10, clock_time=False):
                    self.expiry.tick(now)
            except Exception as e:
                if self._every("expiry_err", 300, clock_time=False):
                    self.log(f"Expiry desk error: {e}")
            if open_:
                self._guard("Big prints", self._big_prints, now, lc)
            if self.linegrade is not None and open_ and self._every("line_grade", 60):   # 2.54: grade line touches
                self._guard("Smart lines grading", self.linegrade.tick, now)
            self._guard("Alert tracker", self.tracker.update, self.state, self.clock.ts())
            if self.depth:
                try:
                    self.depth.tick(now)
                except Exception:
                    pass
            try:
                self.shadow.update(self.state, self.clock.ts())
            except Exception as e:
                if self._every("shadow_err", 300, clock_time=False):
                    self.log(f"Shadow exits error: {e}")
            self._guard("Paper trades", self.paper.update, self.state, now)
            if self.books is not None:
                self._guard("Extra paper accounts", self.books.update, self.state, now)
            if self.trials is not None:
                self._guard("Trial alerts", self.trials.update, self.state, self.clock.ts())
            if self.bench is not None:
                self._guard("Test bench", self.bench.update, self.state, self.clock.ts(), (self.paper.cfg if self.paper else {}).get("exit_time") or "15:15",
                            False, self.dguard.is_bad if self.dguard is not None else None)
                self._guard("Mover sweep", self._mover_sweep, now)
            self._guard("Data vault", self._vault_context, now)
            self._guard("Flat-by reminder", self._flat_by_reminder, now, lc)
            if self._every("last_session", 60, clock_time=False):
                self._guard("Last session save", self.save_last_session)
            if self._every("pulse", 5, clock_time=False):
                self._guard("Market view", self._market_view)
                self._guard("Rule pulse", self._rule_pulse)
                self._guard("Watchdog", self._watchdog, now)
            if self._every("stats", 300, clock_time=False):
                self._guard("Stats", self._refresh_stats)
            aic = self.cfg().get("ai") or {}
            pro = aic.get("provider") == "claude_pro"          # Pro plan: spread the few automatic notes over the day
            every = int(aic.get("brain_every_min") or 10) * 60 if self.ai.enabled() else 300
            pulse_every = int(aic.get("pulse_every_min") or 15) * 60
            if pro and self.ai.enabled():
                every, pulse_every = max(every, 3600), max(pulse_every, 3600)
            if mins >= 3 and self._every("brain", every, clock_time=False):
                threading.Thread(target=self._brain_safe, daemon=True).start()
            elif mins >= 3 and every > 300 and self._every("brain_rules", 300, clock_time=False):
                threading.Thread(target=self._brain_safe, kwargs={"rules_only": True}, daemon=True).start()   # fresh note every 5 min, no AI call
            if self._every("ai_pulse", pulse_every, clock_time=False) \
                    and self.ai.enabled() and mins >= 5:
                threading.Thread(target=self._ai_pulse, daemon=True).start()
            self.status["loop_ms"] = round((time.time() - t0) * 1000)
            self.stop_evt.wait(max(0.2, 1.0 - (time.time() - t0)))

    def _end_of_day(self, now, mins):
        self._run_token = None                       # stops the data-vault chain sweep before the recording is closed
        self.paper.update(self.state, now, force_close=True)
        if getattr(self, "books", None) is not None:
            try:
                self.books.update(self.state, now, force_close=True)
            except Exception as e:
                self.log(f"Extra paper accounts end of day: {e}")
        try:
            self.save_last_session()
        except Exception:
            pass
        try:
            self.desk.end_of_day(now)
        except Exception as e:
            self.log(f"Strategy desk close failed: {e}")
        self._compute_all(now, mins, emit=False)
        self._market_view()
        self.tracker.update(self.state, self.clock.ts(), force=True)
        try:
            self.shadow.update(self.state, self.clock.ts(), force=True)
        except Exception:
            pass
        try:
            if self.trials is not None:
                self.trials.update(self.state, self.clock.ts(), force=True)
        except Exception:
            pass
        try:
            if self.bench is not None:
                self.bench.update(self.state, self.clock.ts(), force=True)
        except Exception:
            pass
        try:
            self.recorder.snapshot(int(now.timestamp() // 60) * 60, self.state, self.metrics, self.record_view)
        except Exception:
            pass
        self._save_iv_history()
        self._refresh_stats()
        try:
            self._save_bars()
        except Exception:
            pass
        self.recorder.finalize(int(self.cfg()["live"].get("keep_recordings_days") or 365), self.log)
        try:
            self.ranker.train(outcomes(self.store, self.source, limit=200000))
        except Exception as e:
            self.log(f"Model training failed: {e}")
        if self.cfg()["live"].get("briefs"):
            try:
                self.make_brief("post", send=self.source != "sim")
            except Exception as e:
                self.log(f"Post-market review failed: {e}")
        if self.source != "sim":
            self.closed_day = now.date()
        self.set_status("closed", "Simulation day finished" if self.source == "sim" else "Market closed for today")
        if self.feed:
            self.feed.stop()
        if self.depth:
            self.depth.stop()
            self.depth = None

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
            try:
                new_alerts += detectors.vix_alerts(vix["ltp"], self.vix_prev, vix["cp"], dcfg)
            except Exception as e:
                if self._every("vixerr", 600, clock_time=False):
                    self.log(f"VIX alert check failed: {e}")
            self.vix_prev = vix["ltp"]
        for a in sorted(new_alerts, key=lambda a: -a["severity"]):
            self._emit_safe(a, now, lc)

    def _trading_open(self, now) -> bool:
        """2.48: alerts and trades only while an NSE session is open (calendar: holidays, special sessions) and the
        exchange has not told the feed that F&O is closed. The simulator always trades."""
        if self.source == "sim":
            return True
        if not SESSION.is_open(now):
            return False
        st = (getattr(self.feed, "market_status", None) or {}).get("NSE_FO") if self.feed else None
        return st not in ("NORMAL_CLOSE", "CLOSING_END")

    def _emit_safe(self, a, now, lc):
        """One alert that fails (a bad fact, a model, a plan) is skipped and logged; the scanner and the other alerts go on."""
        if not getattr(self, "trading_open", True):
            return                                       # 2.48: never an alert outside an open session
        try:
            self._emit(a, now, lc)
        except PermissionError:
            raise
        except Exception as e:
            if self._every(f"emiterr:{a.get('kind')}", 300, clock_time=False):
                self.log(f"Alert {a.get('kind')} {a.get('sym')} skipped: {e}")

    def _guard(self, name, fn, *args):
        """Run one part of the loop; if it fails, log it (at most every 5 min) and keep the scanner running."""
        try:
            return fn(*args)
        except PermissionError:
            raise
        except Exception as e:
            if self._every(f"guard:{name}", 300, clock_time=False):
                self.log(f"{name} error (scanner keeps running): {e}")

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
            self._emit_safe(a, now, lc)

    def _suggest(self, sym, direction, strike: str | None = None):
        """The option an alert suggests: the ATM call / put of the nearest expiry, or (2.57) the strike a Strategy Lab
        rule asks for (rules/strikes.json: n strikes away, or the out-of-the-money strike nearest a premium)."""
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
        if strike:
            try:
                k = self._strike_for(chain, k, side, float(m["spot"]), strike) or k
            except Exception:
                pass
        o = chain[k].get(side)
        i = self.state.get(o.key) if o else None
        if not o or not i or not i.ltp:
            return None
        return {"key": o.key, "label": f"{sym} {k:g} {side} {exp:%d %b}", "side": side, "strike": k,
                "expiry": exp.isoformat(), "lot": o.lot, "ltp": i.ltp, "ask": i.ask, "iv": norm_iv(i.iv),
                "delta": i.delta, "cost_per_lot": round((i.ask or i.ltp) * o.lot, 2)}

    def _strike_for(self, chain: dict, k_atm: float, side: str, spot: float, name: str):
        """2.57: the strike a choice means right now (None = keep ATM): n steps away (+ = out of the money) or the
        out-of-the-money strike whose live price is nearest premium_pct of the index."""
        from lab.factory import strikes_cfg
        ch = strikes_cfg()["choices"].get(name)
        if not ch:
            return None
        ks = sorted(chain)
        sgn = 1 if side == "CE" else -1
        if ch.get("steps"):
            i = ks.index(k_atm) + sgn * int(ch["steps"])
            return ks[i] if 0 <= i < len(ks) and chain[ks[i]].get(side) else None
        want = spot * float(ch["premium_pct"]) / 100
        best, err = None, None
        for kk in ks:
            o = chain[kk].get(side)
            if not o or (kk - k_atm) * sgn <= 0:
                continue
            i = self.state.get(o.key)
            if not i or not i.ltp or i.ltp <= 0.5:
                continue
            e = abs(i.ltp - want)
            if err is None or e < err:
                best, err = kk, e
        return best

    def trade_plan(self, a, m, now):
        """Entry, stop-loss and targets for an alert, set on the chart the way pro traders do (lab/exits.py), plus the
        risk in each exit style for shadow grading. Shown as "Pro plan (testing)"; paper trades keep their exits."""
        from lab import exits
        if not m or a.get("direction") not in ("up", "down") or not m.get("spot"):
            return None, None, None
        b = self.charts.five_min(a["sym"], now.date())
        if not b:
            return None, None, None
        t0 = self.charts._session_start(now.date())
        n = max(0, min(len(b["c"]), int((now.timestamp() - t0) // 300)))      # closed 5-minute bars only
        if n < 3:
            return None, None, None
        h, l, c = (np.asarray(b[k][:n], float) for k in ("h", "l", "c"))
        atr = exits.atr14(h, l, c)[-1]
        if not np.isfinite(atr):                         # before 10:25: average true range of the bars so far
            pc = np.r_[c[0], c[:-1]]
            atr = float(np.nanmean(np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))))
        d = 1 if a["direction"] == "up" else -1
        s0 = float(m["spot"])
        R = {st: exits.risk_pts(st, s0, d, atr, l[-1], h[-1]) for st in exits.STYLES}
        o = a.get("option") or {}
        style = self.best_style()
        pc = self.paper.cfg if self.paper else {}       # 2.48: your capital and risk (was a fixed ₹40K account)
        p = exits.plan(style, s0, d, atr, o.get("ask") or o.get("ltp"), o.get("delta"), o.get("lot"), l[-1], h[-1],
                       capital=float(pc.get("capital") or 500000), risk_pct=float(pc.get("risk_pct") or 1.0))
        if p:
            p["status"] = "testing"
        return p, {k: v for k, v in R.items() if np.isfinite(v)}, s0

    def best_style(self) -> str:
        """The chart-exit style shown on alerts: the Lab's best on holdout (random entries), else ATR 2R."""
        c = getattr(self, "_best_style", None)
        if c and time.time() - c[1] < 600:
            return c[0]
        try:
            from lab import factory
            st = (factory.load(self.data_dir).get("exit_board") or {}).get("best_style") or "atr2r"
        except Exception:
            st = "atr2r"
        self._best_style = (st, time.time())
        return st

    def factory_rules(self) -> list:
        """Promoted Strategy Lab rules that are switched on (re-read every 10 minutes)."""
        at, rules = self._fact_rules
        if time.time() - at > 600:
            try:
                from lab import factory
                rules = factory.promoted(self.data_dir)
            except Exception:
                rules = []
            try:
                from lab import mlstrat                  # model-based strategies that passed the same test
                rules = rules + mlstrat.live_rules(self.data_dir)
            except Exception:
                pass
            self._fact_rules = (time.time(), rules)
        return rules

    def factory_rules_5y(self) -> list:
        """Rules promoted by the 5-year Lab (data/lab5y) that are not already live from the 1-year Lab. They run live as
        ideas on trial (registry "lab5y", shadow): recorded and graded only, until you switch one on."""
        at, rules = getattr(self, "_fact_rules5", (0.0, []))
        if time.time() - at > 600:
            try:
                from lab import dhan_hist, factory
                live_ids = {r["id"] for r in self.factory_rules()}
                rules = [r for r in factory.promoted(dhan_hist.root(self.data_dir)) if r["id"] not in live_ids]
            except Exception:
                rules = []
            self._fact_rules5 = (time.time(), rules)
        return rules

    def signal_facts(self, sym, direction, now) -> dict:
        """2.31: the facts at this moment for a test-bench trade, the same ones an alert records (live/learn.py
        features: the stock, its options, the index, breadth, sector, world, VIX, news, order book, movers; plus the
        bigger picture: daily / weekly trend, results and events ahead). Cached per symbol, direction and minute."""
        k = (sym, direction, int(now.timestamp() // 60))
        hit = self._facts_cache.get(k)
        if hit is not None:
            return hit
        if len(self._facts_cache) > 4000:
            self._facts_cache.clear()
        m = self.metrics.get(sym)
        reg = self.regimes.get(sym) or self.regimes.get("NIFTY")
        ev = evidence(m, direction, self.metrics.get("NIFTY")) if m else []
        ctx = self._ctx_for(sym, m)
        try:
            ctx["mkt"] = self.market_ctx(sym)
        except Exception:
            ctx["mkt"] = {}
        a = {"sym": sym, "kind": "mover", "direction": direction, "severity": 0, "option": self._suggest(sym, direction)}
        f = features(a, m, ev, reg, minutes_since_open(now), ctx)
        try:
            f.update(self.bigger_picture(sym, direction, m, now).get("facts") or {})
        except Exception:
            pass
        v = self._vix() or {}
        dly = (self.daily.get(sym) or {}).get("htf") or {}
        f.update(vix=v.get("ltp"), atr_pct=dly.get("atr_pct"), spot=(m or {}).get("spot"), dow=now.weekday(),
                 in_play=int(sym in self.in_play))
        if self.cdl is not None:                     # which way the latest candle on each timeframe points
            try:
                f.update(self.cdl.facts(sym, direction))
            except Exception:
                pass
        if getattr(self, "events", None) is not None:   # 2.35: the event risk at that moment
            try:
                f.update(self.events.risk(sym, now).get("facts") or {})
            except Exception:
                pass
        try:                                         # 2.42: the newer readings, recorded for a later honest-label model
            f.update(self.forecast_facts(sym, direction, now))
        except Exception:
            pass
        try:                                         # 2.46: how many named strategies agree right now
            f.update(self.confluence_facts(sym, direction, now))
        except Exception:
            pass
        out = {x: y for x, y in f.items() if x not in ("kind", "severity", "direction") and y is not None}
        self._facts_cache[k] = out
        return out

    CONF_WINDOW_S = 900

    def note_confluence(self, sym, d, who, ts):
        """2.46: a named strategy (library / book / file setup) fired on `sym` in direction d (+1 / -1) at ts."""
        q = self.__dict__.setdefault("_conf", {}).setdefault(sym, [])
        q.append((float(ts), int(d), str(who)))
        cut = float(ts) - self.CONF_WINDOW_S
        while q and q[0][0] < cut:
            q.pop(0)

    def confluence_facts(self, sym, direction, now) -> dict:
        """2.46: how many different named strategies fired with / against this direction on `sym` in the last 15
        minutes (this signal's own setup included). Recorded with every bench signal; nothing decides on it."""
        sgn = 1 if direction == "up" else -1 if direction == "down" else 0
        if not sgn:
            return {}
        ts = now.timestamp()
        q = [x for x in (self.__dict__.get("_conf") or {}).get(sym, []) if ts - self.CONF_WINDOW_S <= x[0] <= ts + 1]
        return {"conf_al": len({w for _t, d, w in q if d == sgn}), "conf_against": len({w for _t, d, w in q if d == -sgn})}

    IND_WORDS = {"gap up": 1.0, "positive": 0.5, "neutral": 0.0, "negative": -0.5, "gap down": -1.0}

    def forecast_facts(self, sym, direction, now) -> dict:
        """2.42: what the candle forecast (2.41), the next-candle cascade (2.39), the India indication (2.40) and the data
        guard (2.34) said at this moment, aligned with the trade's direction (+ = with it). Only recorded with the signal
        (bench, alerts) for a later challenger to learn from: nothing here decides anything.
        fc5_al / fc15_al: the symbol's forming 5m / 15m candle forecast, strength signed (fc*_pakka: 1 at PAKKA);
        mfc*: the same for Nifty (the market); nc*_al / mnc*_al: the cascade's chance of green, -1..+1;
        ind_al: today's India indication word, -1..+1; dq_bad: 1 when the symbol's own price is suspect or stale."""
        sgn = 1 if direction == "up" else -1 if direction == "down" else 0
        out = {}
        ts = now.timestamp()
        nc = getattr(self, "nextc", None)
        if nc is not None and sgn:
            from .nextcandle import TF_MIN
            cast = getattr(nc, "_cast", None)
            for who, tag in ((sym, ""), ("NIFTY", "m")):
                if tag and sym == "NIFTY":
                    continue
                for tf in ("5m", "15m"):
                    n = tf[:-1]
                    c = cast.cur.get((who, tf)) if cast is not None else None
                    if c and c.get("stages") and c["start"] <= ts < c["start"] + TF_MIN[tf] * 60:
                        p = c["stages"][max(c["stages"])]
                        out[f"{tag}fc{n}_al"] = round(sgn * (1 if p["dir"] == "up" else -1) * float(p["strength"]) / 100, 3)
                        out[f"{tag}fc{n}_pakka"] = int(bool(p.get("pakka")))
                    k = nc.cur.get((who, tf))
                    if k and k.get("pred") and k["start"] <= ts < k["start"] + TF_MIN[tf] * 60:
                        out[f"{tag}nc{n}_al"] = round(sgn * (float(k["pred"]["p_green"]) - 50) / 50, 3)
        ind = getattr(self, "indication", None)
        lat = getattr(ind, "latest", None) if ind is not None else None
        if sgn and lat and lat.get("day") == now.date().isoformat():
            w = self.IND_WORDS.get(str(lat.get("word") or "").strip().lower())
            if w is not None:
                out["ind_al"] = sgn * w
        dg = getattr(self, "dguard", None)
        if dg is not None:
            try:
                out["dq_bad"] = int(bool(dg.symbol_problem(sym)))
            except Exception:
                pass
        fn = getattr(self, "_radar_today", None)    # 2.56: the Daily Radar's call for today (recorded only)
        if fn is not None:
            try:
                out.update(fn(now) or {})
            except Exception:
                pass
        return out

    def _radar_today(self, now) -> dict:
        """2.56: the Daily Radar (Bank Nifty big-move model) forecast for today, if it was made for today:
        radar_big = its chance of a big-range day (0-1), radar_flag = 1 when it flagged the day. Recorded with bench
        signals so the credibility check can show whether trades on flagged days do better; decides nothing."""
        c = getattr(self, "_radar_c", None)
        if c is None or time.time() - c[0] > 600:
            fc = {}
            try:
                f = Path(self.data_dir).parent / (self.cfg().get("output_dir") or "output") / "forecast_latest.json"
                if f.exists():
                    fc = json.loads(f.read_text())
            except Exception:
                fc = {}
            c = self._radar_c = (time.time(), fc)
        fc = c[1]
        if not fc or str(fc.get("for_date") or "")[:10] != now.date().isoformat() or fc.get("demo"):
            return {}
        try:
            return {"radar_big": round(float(fc["prob_big"]), 3), "radar_flag": int(bool(fc.get("flag")))}
        except Exception:
            return {}

    def _mover_sweep(self, now):
        """2.31: every few minutes the F&O stocks (and indices) moving most get an honest paper test on the bench in
        every style, with the move and against it (live/movers.py, rules/bench_movers.json): the yardstick every
        strategy has to beat. Test bench only: no alert, no paper account, no broker."""
        from . import movers
        c = movers.cfg()
        if self.bench is None or not self.metrics or not self._every("mover_sweep", float(c["every_min"]) * 60):
            return
        if not (str(c["start"]) <= now.strftime("%H:%M") <= str(c["end"])):
            return
        picks = movers.pick(self.metrics, self.daily, c)
        n = 0
        for p in picks:
            tag = "idx_" if p["is_index"] else ""
            what = "index" if p["is_index"] else "stock"
            n += self.bench_signal(p["sym"], p["direction"], now, "mover", tag + "with", f"Mover ({what}): with the move")
            if c.get("both_ways"):
                n += self.bench_signal(p["sym"], "down" if p["direction"] == "up" else "up", now, "mover", tag + "fade",
                                       f"Mover ({what}): against the move")
            if not p["is_index"] and len(self.in_play) < int(c["in_play_max"]):
                self.in_play.add(p["sym"])
        self.mover_last = {"ts": now.timestamp(), "at": now.strftime("%H:%M"), "picks": picks[:20], "opened": n,
                           "in_play": sorted(self.in_play)}

    def _vault_context(self, now):
        """2.31 data vault: the market around everything, every few minutes (breadth, sectors, day types, VIX, GIFT
        Nifty, world markets, index option facts), saved with the day's recording for backtests."""
        from . import vault
        if self.recorder is None or not self.metrics or not self._every("vault_ctx", float(vault.cfg()["context_every_min"]) * 60):
            return
        mk = self.market or {}
        idx = {}
        for s, m in self.metrics.items():
            if m.get("is_index"):
                o = m.get("opt") or {}
                idx[s] = {"spot": m.get("spot"), "chg_pct": m.get("chg_pct"), "vwap": m.get("vwap"), "pcr": o.get("pcr"),
                          "atm_iv": o.get("atm_iv"), "max_pain": o.get("max_pain"), "straddle": o.get("straddle"),
                          "regime": (self.regimes.get(s) or {}).get("code"), "gex": (self.gex.get(s) or {}).get("net_cr")}
        try:
            world = {t: {k: r.get(k) for k in ("last", "chg_pct", "name") if r.get(k) is not None}
                     for t, r in (getattr(self.world, "rows", None) or {}).items()}
            pressure = self.world.pressure()
        except Exception:
            world, pressure = {}, None
        self.recorder.context(int(now.timestamp()), "market", {
            "breadth": mk.get("breadth"), "sectors": [{"sector": x.get("sector"), "chg": x.get("chg")} for x in mk.get("sectors") or []],
            "indices": idx, "vix": self._vix(), "gift": self.gift.row if self.gift else None, "world": world,
            "pressure": pressure, "regimes": {s: r.get("code") for s, r in self.regimes.items() if r},
            "in_play": sorted(self.in_play), "movers": (self.mover_last or {}).get("picks"),
            "events": (self.events.risk("NIFTY", now).get("items") if getattr(self, "events", None) is not None else None)})

    def next_candle(self):
        """2.39: the Fortune chart's next-candle cascade (live/nextcandle.py), kept for the app's life."""
        if getattr(self, "nextc", None) is None:
            from .nextcandle import NextCandle
            self.nextc = NextCandle(self)
        return self.nextc

    def _nextcandle_loop(self, token):
        """2.39: every few seconds, predict the forming 5m / 15m / 1H / 1D candles from their closed small candles and
        grade the ones that finished (in its own thread: never slows the scanner)."""
        time.sleep(5)
        while self._run_token is token and not self.stop_evt.is_set():
            try:
                self.next_candle().tick()
            except Exception as e:
                if self._every("nextcandle_err", 600, clock_time=False):
                    self.log(f"Next-candle tracker: {e}")
            self.stop_evt.wait(max(2.0, 10.0 / max(1.0, float(getattr(self.clock, "speed", 1) or 1))))

    def _vault_loop(self, token):
        """2.31 data vault: a full option chain for every F&O stock every few minutes (rules/vault.json), spread over the
        interval and only with spare Upstox calls (it waits while the app's 30-minute budget is busy). Index chains are
        recorded by chain() whenever the scanner fetches them."""
        from . import vault
        from .upstox import RateLimiter
        time.sleep(30)
        while self._run_token is token and not self.stop_evt.is_set():
            c = vault.cfg()
            every = max(5.0, float(c["stock_chains_every_min"])) * 60
            t0 = time.time()
            now = self.clock.now()
            hm = now.strftime("%H:%M")
            if self.source != "sim" and str(c["start"]) <= hm <= str(c["end"]) and self.uni:
                stocks = [u.symbol for u in self._rank_stocks()]
                gap = every * 0.6 / max(1, len(stocks))
                done = waited = 0
                cap = RateLimiter.WINDOWS[-1][1] * float(c["max_upstox_share"])
                lim = getattr(self.rest, "limiter", None)
                for sym in stocks:
                    if self._run_token is not token or self.stop_evt.is_set():
                        return
                    while lim is not None and lim.used(1800) >= cap and self._run_token is token:
                        waited += 1
                        self.stop_evt.wait(5)
                    try:
                        self.chain(sym, max_age=60, keep=False)
                        done += 1
                    except PermissionError:
                        return
                    except Exception as e:
                        if self._every("vault_err", 600, clock_time=False):
                            self.log(f"Data vault: chain {sym} failed: {e}")
                    self.stop_evt.wait(gap)
                self.vault_last = {"at": now.strftime("%H:%M"), "stocks": done, "of": len(stocks), "waits": waited,
                                   "secs": round(time.time() - t0)}
            self.stop_evt.wait(max(10.0, every - (time.time() - t0)))

    def vault_view(self) -> dict:
        from . import vault
        out = vault.view(self)
        out["bench"] = self.bench.status() if self.bench is not None else None
        out["movers"] = self.mover_last
        out["candles"] = self.cdl.status() if self.cdl is not None else None
        out["quality"] = self.dguard.view() if self.dguard is not None else None
        return out

    def _save_bars(self):
        """2.31: today's 1-minute candles of every index and stock into the day's recording (the candle scanner joins
        them to the next day's, and they are backtest data)."""
        if self.recorder is None or self.state is None:
            return
        with self.state.lock:
            snap = {s: [list(x) for x in b.bars] for s, b in self.state.bars.items() if b.bars}
        if snap:
            self.recorder.bars(snap)

    def candles_now(self, sym) -> dict:
        if self.cdl is None or not self.running:
            return {"sym": sym, "rows": [], "note": "Starts with the live scanner."}
        return self.cdl.now_view(sym)

    def _data_hold(self, a) -> bool:
        """2.33: hold a price-based alert while its symbol's own price is suspect (a jump being checked, a crossed
        quote) or stale (live/dataguard.py). News and other information alerts are never held."""
        if self.dguard is None or registry.get(a.get("kind") or "").get("family") == "info":
            return False
        try:
            return self.dguard.hold(a)
        except Exception:
            return False

    def bench_signal(self, sym, direction, now, kind, variant, name):
        """A signal that is only paper-tested on the bench (Lab library / book rules live): no alert, no paper account."""
        if self.bench is None:
            return 0
        if self.dguard is not None and self.dguard.symbol_problem(sym):
            return 0
        a = {"sym": sym, "kind": kind, "direction": direction, "option": self._suggest(sym, direction), "data": {}}
        return self._bench_add(a, now, name=name, variant=variant)

    def _bench_add(self, a, now, **kw):
        """2.58: every test-bench signal (alerts, ideas on trial, Lab rules) is also offered to the "Multiverse pick" paper
        account, which takes it when it matches one of the multiverse's live picks (lab/multiverse.py). The bench itself
        is called exactly as before."""
        out = self.bench.add(a, self, now, self.paper.cfg if self.paper else {}, **kw)
        bs = getattr(self, "books", None)
        if bs is not None and getattr(bs, "has_multiverse", lambda: False)() and a.get("direction") in ("up", "down"):
            try:
                from lab.multiverse import _facts_of
                ad = a.get("data") or {}
                tag = {"sev": a.get("severity"), "score": ad.get("score"), "conv": ad.get("conviction"), "focus": ad.get("focus")}
                facts = _facts_of(self.signal_facts(a["sym"], a["direction"], now),
                                  {k: v for k, v in tag.items() if v is not None}, a)
                bs.offer_multiverse(a, self.state, now, facts,
                                    lambda strike, a=a: self._suggest(a["sym"], a["direction"], None if strike == "atm" else strike))
            except Exception as e:
                if self._every("mv_offer_err", 600, clock_time=False):
                    self.log(f"Multiverse pick account skipped a signal: {e}")
        return out

    def factory_live_record(self) -> list:
        """Paper results of Strategy Lab rules, for demotion: [{'rule', 'net'}]."""
        out = []
        for t in self.store.trades(source="upstox", limit=5000):
            if t["kind"] == "factory" and t["status"] == "closed":
                rid = t["mode"][8:] if str(t.get("mode") or "").startswith("factory:") else None
                if not rid and t.get("alert_id"):
                    a = self.store.alert(t["alert_id"])
                    rid = (((a or {}).get("data") or {}).get("factory") or {}).get("id")
                if rid:
                    out.append({"rule": rid, "net": t["net"]})
        return out

    def find_option(self, key: str):
        """(underlying, Option) for an option instrument key, from the loaded universe."""
        for u in (self.uni.underlyings.values() if self.uni else []):
            for exp, ch in u.chains.items():
                for k, sides in ch.items():
                    for side, o in sides.items():
                        if o.key == key:
                            return u, o
        return None, None

    def ensure_live(self, key: str):
        """Start streaming an option that is outside the scanned strikes (seeded with a REST quote first)."""
        if key in self.opt_subs or (self.state.get(key) and self.state.get(key).ltp and key in self.pinned):
            return
        self.pinned.add(key)
        try:
            self._seed(self.rest.quotes([key]))
        except Exception as e:
            self.log(f"Quote for {key} failed: {e}")
        try:
            self.feed.subscribe([key], "option_greeks")
            self.opt_subs.add(key)
        except Exception as e:
            self.log(f"Subscribe {key} failed: {e}")

    def _keep_live(self, key: str, und_key: str | None = None):
        """2.48: a new paper trade's option streams from the first second (before, it waited for the next strike-window
        refresh, so an open trade's price could lag). Kept while the trade is open (the window adds open trades)."""
        if not key or key in self.opt_subs or not self.feed:
            return
        try:
            self._seed(self.rest.quotes([key]))
        except Exception as e:
            self.log(f"Quote for {key} failed: {e}")
        self.feed.subscribe([key], "option_greeks")
        self.opt_subs.add(key)

    def paper_open(self, key: str, direction: int, lots: int = 1) -> dict:
        """Paper Buy/Sell of one option from the option chain or the chart. Paper only: nothing reaches a broker."""
        if not self.running or not self.paper:
            raise ValueError("The live scanner is not running (it starts by itself before every NSE session).")
        u, o = self.find_option(key)
        if not o:
            raise ValueError("Option not found in today's contracts.")
        self.ensure_live(key)
        opt = {"key": o.key, "label": f"{u.symbol} {o.strike:g} {o.kind} {o.expiry:%d %b}", "side": o.kind, "lot": o.lot}
        und_key = u.key if self.state.get(u.key) and self.state.get(u.key).ltp else u.fut_key
        return self.paper.open_manual(self.state, self.clock.now(), sym=u.symbol, opt=opt, direction=1 if direction > 0 else -1,
                                      lots=lots, und_key=und_key)

    def paper_quick(self, sym: str, side: str) -> dict:
        """Chart buttons: Buy = at-the-money call, Sell = at-the-money put (both bought), nearest expiry."""
        o = self._suggest(sym, "up" if side == "buy" else "down")
        if not o:
            raise ValueError(f"No live at-the-money option for {sym} right now.")
        return {**self.paper_open(o["key"], 1, 1), "label": o["label"]}

    def underlying_view(self, sym: str) -> dict:
        m = self.metrics.get(sym) if self.metrics else None
        return {"spot": m.get("spot"), "chg_pct": m.get("chg_pct")} if m else {}

    def _ctx_for(self, sym, m):
        v = (m or {}).get("vol") or {}
        return {"iv_hv": v.get("iv_hv"), "iv_pct": v.get("iv_pct") if v.get("iv_pct") is not None else v.get("vix_pct"),
                "gex": self.gex.get(sym),
                "regime": (self.regimes.get(sym) or self.regimes.get("NIFTY") or {}).get("label"), "now": self.clock.now()}

    def _start_depth(self):
        """Dhan 200-level order book for Nifty and Bank Nifty futures (needs Dhan keys with the Pro data plan).
        Optional: any problem here only logs; the scanner never depends on it."""
        if self.source == "sim" or self.depth:
            return
        lab = self.cfg().get("lab") or {}
        if not (lab.get("dhan_client_id") and lab.get("dhan_token")) or lab.get("dhan_depth") is False:
            return
        try:
            from .dhandepth import DepthFeed, futures_ids
            ids = futures_ids(self.data_dir)
            if not ids:
                self.log("Order book (Dhan): index futures not found in Dhan's scrip master")
                return
            self.depth = DepthFeed(lab["dhan_client_id"], lab["dhan_token"], ids, self.data_dir, self.log)
            self.depth.start()
            self.log(f"Order book (Dhan 200-level): {', '.join(ids)} futures")
        except Exception as e:
            self.depth = None
            self.log(f"Order book (Dhan) not started: {e}")
        if not self.dhan_intel:
            try:
                from lab.dhan import Dhan
                from .dhanintel import DhanIntel
                self.dhan_intel = DhanIntel(Dhan(lab["dhan_client_id"], lab["dhan_token"], per_sec=2.0), self.data_dir, self.log)
                threading.Thread(target=self._movers_loop, daemon=True, name="dhan-movers").start()
                self.log("Dhan news headlines and market movers: on")
            except Exception as e:
                self.dhan_intel = None
                self.log(f"Dhan news / movers not started: {e}")

    def _movers_loop(self):
        """Market movers every minute in market hours (stock options / futures OI and volume, F&O stock price)."""
        while self.dhan_intel:
            now = dt.datetime.now(IST)
            if now.weekday() < 5 and dt.time(9, 15) <= now.time() <= dt.time(15, 30) and self.running:
                try:
                    exp = None
                    for u in (self.uni.stocks if self.uni else []):
                        exp = u.near_expiry(now.date())
                        if exp:
                            break
                    self.dhan_intel.refresh_movers(exp)
                except PermissionError as e:
                    self.log(f"Dhan movers: {e}")
                    time.sleep(1800)
                except Exception as e:
                    if self._every("movers_err", 600, clock_time=False):
                        self.log(f"Dhan movers failed: {e}")
            time.sleep(60)

    def market_ctx(self, sym) -> dict:
        """Everything outside the stock itself that an alert's strength may depend on: the index, breadth, its sector,
        global markets, VIX and fresh news. Used as model features (cached ~10 s)."""
        now = time.time()
        if now - self._mctx_at > 10:
            self._mctx_at = now
            mk = self.market or {}
            secs = {x["sector"]: x["chg"] for x in (mk.get("sectors") or [])}
            try:
                gp = (self.world.pressure() or {}).get("implied_pct")
            except Exception:
                gp = None
            v = self._vix() or {}
            self._mctx = {"nifty": self.metrics.get("NIFTY") or {}, "bank": self.metrics.get("BANKNIFTY") or {},
                          "breadth": mk.get("breadth") or {}, "sectors": secs, "global": gp, "vix_chg": v.get("chg_pct"),
                          "depth": {k: self.depth.view(k) for k in self.depth.ids} if self.depth else {}}
        c = dict(self._mctx)
        c["sector_chg"] = c["sectors"].get(self.context.sector_of(sym)) if self.context else None
        news = [n for n in (self.news.items if self.news else []) if sym in n.get("syms", []) and n.get("ts", 0) > now - 3600]
        c["news_n"] = len(news)
        c["mover"] = self.dhan_intel.mover_rank(sym) if self.dhan_intel else {}
        c["news_tone"] = sum(n.get("tone") or 0 for n in news) / len(news) if news else 0.0
        return c

    def _trial(self, a, now, lc):
        """A bullet on trial ("shadow" in live/registry.py): recorded and graded only (live/trials.py). It never reaches
        the feed, Telegram, Focus, AI notes, stories, the scorecard, the conviction model or the paper account."""
        if self.trials is None or self._data_hold(a):
            return
        u = self.uni.underlyings.get(a["sym"]) if self.uni else None
        i = self.state.get(u.key) if u and self.state is not None else None
        und = (u.key if i and i.ltp else u.fut_key) if u else None
        a.update(day=now.date().isoformat(), source=self.source)
        cool = registry.get(a["kind"]).get("cooldown") or float(lc["cooldown_min"])
        self.trials.add(a, und, self.clock.ts(), float(cool))
        if self.bench is not None and a.get("direction") in ("up", "down"):     # ideas on trial get an honest paper trade too
            try:
                self._bench_add({**a, "option": self._suggest(a["sym"], a["direction"])}, now)
            except Exception as e:
                if self._every("bench_err", 600, clock_time=False):
                    self.log(f"Test bench skipped a trial: {e}")

    def _emit(self, a, now, lc):
        if registry.is_shadow(a.get("kind")):
            return self._trial(a, now, lc)
        if self._data_hold(a):                       # 2.33: its own price is suspect or stale right now
            return
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
        a.update(ts=ts, day=now.date().isoformat(), source=self.source,
                 option=self._suggest(a["sym"], a["direction"], ((a.get("data") or {}).get("factory") or {}).get("strike")))
        # evidence, levels, structure, track record, score
        reg = self.regimes.get(a["sym"]) or self.regimes.get("NIFTY")
        ev = evidence(m, a["direction"], self.metrics.get("NIFTY")) if m else []
        ctx = self._ctx_for(a["sym"], m)
        st = self.stats.get((a["kind"], int(a["is_index"]))) or self.stats.get((a["kind"],))
        try:
            ctx["mkt"] = self.market_ctx(a["sym"])
        except Exception:
            ctx["mkt"] = {}
        feats = features(a, m, ev, reg, mins, ctx)
        big = {}
        try:                                  # recorded with the alert; only a challenger model may use them (learn.py)
            big = self.bigger_picture(a["sym"], a["direction"], m, now)
            feats.update(big.pop("facts", {}))
        except Exception as e:
            if self._every("bigpic_err", 600, clock_time=False):
                self.log(f"Bigger-picture facts skipped: {e}")
        evr = None
        if getattr(self, "events", None) is not None:   # 2.35: event risk (information and warnings only)
            try:
                evr = self.events.risk(a["sym"], now)
                feats.update(evr.get("facts") or {})
            except Exception as e:
                evr = None
                if self._every("event_risk_err", 600, clock_time=False):
                    self.log(f"Event risk skipped: {e}")
        p = self.ranker.predict(feats) if self.ranker else None
        sc = signal_score(a["severity"], ev, st, regime_adjust((reg or {}).get("code"), a["kind"], a["direction"]), p)
        sev0 = a["severity"]                         # before any scorecard demotion (the shadow Focus judges it too)
        muted = None
        if st and a["direction"] in ("up", "down") and st.get("hit_rate") is not None and (
                (st.get("decided", 0) >= 20 and st["hit_rate"] < float(lc["mute_hit_rate"])) or
                (st.get("n", 0) >= 40 and (st.get("avg_o60") or 0) < -2.0)):
            muted = (f"Demoted: on the live scorecard this alert type worked {st['hit_rate']}% of {st['decided']} decided cases, "
                     f"option {st.get('avg_o60') or 0:+.1f}% after an hour on average.")
            a["severity"] = max(1, a["severity"] - 1)
        # focus: what deserves your eyes. Strong (act-now) directional alerts that are not demoted, at most one per
        # symbol per hour. Everything else is still recorded and graded, and shown under "All".
        conv = self.ranker.conviction(p) if self.ranker else None
        trusted = conv is not None
        focus = False
        strong_enough = (conv >= int(lc.get("focus_min_conviction") or 85) and a["severity"] >= 2) if trusted \
            else a["severity"] >= int(lc.get("focus_min_severity") or 3)
        if a["direction"] in ("up", "down") and not muted and strong_enough:
            lastf = self.focus_last.get(a["sym"])
            if not lastf or ts - lastf >= float(lc.get("focus_gap_min") or 60) * 60:
                focus = True
                self.focus_last[a["sym"]] = ts
        elif a["direction"] not in ("up", "down") and a["severity"] >= 3:
            focus = True                      # market-wide news / macro events
        fch = None
        if getattr(self, "focuslab", None) is not None:   # 2.37: challengers judge the same alert; saved with it, read by nothing else
            try:
                fch = self.focuslab.decide(a, sev0=sev0, muted=bool(muted), strong=bool(strong_enough), ts=ts, day=a["day"])
            except Exception as e:
                fch = None
                if self._every("focuslab_err", 600, clock_time=False):
                    self.log(f"Shadow Focus skipped: {e}")
        lv_setup = (a.get("data") or {}).get("setup_levels")
        tp_plan, tp_R, tp_s0 = None, None, None
        try:
            tp_plan, tp_R, tp_s0 = self.trade_plan(a, m, now)
        except Exception as e:
            self.log(f"Trade plan failed: {e}")
        a["data"] = {**(a.get("data") or {}), "evidence": ev,
                     "levels": lv_setup or (levels(m, a["direction"], self.state) if m else {}),
                     "structure": structure(a["direction"], m, ctx) if m else None,
                     "track": st, "score": sc, "model_p": round(p * 100) if p is not None else None, "muted": muted, "focus": focus,
                     **({"focus_ch": fch} if fch else {}),
                     "conviction": conv, "regime": (reg or {}).get("label"),
                     "plan": tp_plan,
                     "plan_R": {k: round(float(v), 2) for k, v in (tp_R or {}).items()} or None,   # 2.56: risk per chart exit
                     "bigger": big or None,
                     "event_risk": {k: evr.get(k) for k in ("level", "line", "items", "warnings")} if evr and evr.get("items") else None,
                     "fits_small": bool(a.get("option") and (a["option"].get("cost_per_lot") or 1e9) <= float(lc.get("small_lot_max") or 10000)),
                     "context": {k: feats.get(k) for k in ("nifty_al", "sector_al", "global_al", "breadth_al", "news_n", "news_al")}}
        # risk gate: PASS / BLOCK with reasons and lots, before anything can become a paper trade
        try:
            if a.get("option") and self.paper:
                a["data"]["risk"] = self.risk_for(a, now)
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
        if a["severity"] >= int(lc["telegram_min_severity"]) and not muted and (focus or not lc.get("telegram_focus_only", True)):
            self.telegram.send(telegram_text(a))
        aic = self.cfg().get("ai") or {}
        if self.ai.enabled() and a["severity"] >= int(aic.get("explain_min_severity") or 3) and self._auto_explain_ok(a, aic):
            threading.Thread(target=self._explain_one, args=(a["id"],), daemon=True).start()
        try:
            self.tracker.add(a, m, self.state, self.clock.ts(), feats)
        except Exception as e:
            self.log(f"Tracking failed: {e}")
        try:
            if tp_R and self.shadow:
                pc = self.paper.cfg if self.paper else {}
                self.shadow.add(a, tp_s0, tp_R, (float(pc.get("target_pct") or 20), float(pc.get("stop_pct") or 15),
                                                 float(pc.get("max_hold_min") or 60)), self.clock.ts(),
                                float(lc.get("small_lot_max") or 10000))
        except Exception as e:
            self.log(f"Shadow exits failed: {e}")
        main_trade = None
        if not muted:
            try:
                tid = self.paper.try_open(a, self.state, now)
                if tid:                                 # 2.56.2: what the main account took, for accounts that mirror it
                    main_trade = next(({"id": t["id"], "lots": t["lots"]} for t in self.paper.open_trades() if t["id"] == tid),
                                      {"id": tid, "lots": 1})
            except Exception as e:
                self.log(f"Paper trade failed: {e}")
        if getattr(self, "books", None) is not None:   # 2.49: Best only / 3% a day look at the same alert
            try:
                self.books.consider(a, self.state, now, main=main_trade)
            except Exception as e:
                if self._every("books_err", 600, clock_time=False):
                    self.log(f"Extra paper accounts skipped an alert: {e}")
        if self.bench is not None:                   # every directional alert, Focus or not, demoted or not
            try:
                self._bench_add(a, now)
            except Exception as e:
                if self._every("bench_err", 600, clock_time=False):
                    self.log(f"Test bench skipped an alert: {e}")

    @staticmethod
    def open_files() -> dict:
        """How many files and connections this process has open, and the system's limit (macOS default is 256)."""
        import os
        import resource
        n = None
        for d in ("/dev/fd", "/proc/self/fd"):
            try:
                n = len(os.listdir(d))
                break
            except OSError:
                continue
        soft = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
        return {"open": n, "limit": soft}

    def _health_check(self):
        try:
            h = self.open_files()
            if h["open"] and h["limit"] and h["open"] > 0.6 * h["limit"]:
                self.log(f"Warning: {h['open']} open files (limit {h['limit']}). Restart the app if it slows down.")
        except Exception:
            pass

    def bigger_picture(self, sym, direction, m, now) -> dict:
        """Daily / weekly trend, room to the 20-day high / low, the stock's next board meeting (results) and index
        events today. {"line", "daily", "event", "index_event", "facts"}; facts are signed with the alert."""
        d = (self.daily.get(sym) or {}).get("htf") or {}
        out = {"line": htf.words(d, direction), "daily": {k: d.get(k) for k in ("trend", "w_trend", "st", "rsi", "pos20",
                                                                                 "hi20", "lo20", "ema20", "ema50", "asof")} if d else None}
        facts = htf.aligned(d, direction, (m or {}).get("spot"))
        u = self.uni.underlyings.get(sym) if self.uni else None
        today = now.date()
        if u and not u.is_index and self.context is not None:
            ev = self.context.next_event(sym, today)
            if ev and ev["days"] <= 10:
                out["event"] = ev
                facts["res_days"] = ev["days"] if ev.get("results") else None
                facts["event_days"] = ev["days"]
        if u and u.is_index:
            try:
                from lab.rules import events_named
                names = [n for dd, n in events_named() if dd == today.isoformat()]
                if names:
                    out["index_event"] = ", ".join(names)
                    facts["index_event"] = 1
            except Exception:
                pass
        out["facts"] = facts
        return out

    def risk_for(self, a: dict, now, book=None, capital=None) -> dict:
        """The risk gate on an idea with live option prices, today's paper book and the ban list, right now.
        2.49: book / capital = an extra paper account (live/books.py) judged with its own money and open trades."""
        book = book or self.paper
        opt = dict(a.get("option") or {}) or None
        if opt:
            inst = self.state.get(opt["key"])
            if inst:
                opt["bid"], opt["ask"] = inst.bid, inst.ask or opt.get("ask")
                opt["ltp"] = inst.ltp or opt.get("ltp")
        ban = (self.context.view().get("ban") or []) if self.context else []
        lc = self.cfg().get("live") or {}
        pcfg = {**book.cfg, "capital": float(capital)} if capital else book.cfg
        out = riskgate.check(a, opt, now=now, paper_cfg=pcfg, guard=book.guard(now),
                             open_syms={t["sym"] for t in book.open_trades()}, ban=ban, cfg=(lc.get("risk") or {}),
                             sim=self.source == "sim", bigger=(a.get("data") or {}).get("bigger"))
        er = (a.get("data") or {}).get("event_risk") or {}
        if er.get("warnings"):                       # 2.35: event risk, warnings only
            out["warnings"] = (out.get("warnings") or []) + list(er["warnings"])
        try:                                         # tilt coach: a warning (never a block) after two losses in a row
            from .coach import streak_warning
            day = now.date().isoformat()
            hist = self._coach_hist()
            w = streak_warning([t for t in hist if t.get("day") == day], hist)
            if w:
                out["warnings"] = (out.get("warnings") or []) + [w]
        except Exception:
            pass
        return out

    def _coach_hist(self) -> list:
        hit = getattr(self, "_coach_cache", None)
        if hit and time.time() - hit[0] < 60:
            return hit[1]
        rows = self.store.trades(source=self.source, limit=3000) if self.store else []
        self._coach_cache = (time.time(), rows)
        return rows

    BRAIN_RESERVE = 8          # automatic AI calls kept for the hourly market notes on the Pro plan

    def _auto_explain_ok(self, a, aic) -> bool:
        """Automatic 'Why?' notes: on the Pro plan (a small daily allowance) only for the strongest alerts the risk
        check passed, never while another one is being written, and never eating the market notes' reserve."""
        if aic.get("provider") != "claude_pro":
            return True
        risk = (a.get("data") or {}).get("risk") or {}
        if a["severity"] < 3 or risk.get("verdict") != "PASS" or (a.get("data") or {}).get("muted"):
            return False
        cap = int(aic.get("pro_auto_per_day") or 12)
        return self.ai.used_auto < cap - self.BRAIN_RESERVE and not self._explaining.locked()

    def _explain_one(self, alert_id):
        if not self._explaining.acquire(blocking=False):
            return
        try:
            self.explain(alert_id)
        except Exception as e:
            self.log(f"Auto note failed: {e}")
        finally:
            self._explaining.release()

    def explain(self, alert_id, auto=True) -> str | None:
        """'Why?' on an alert: a short written explanation from the alert, its metrics and related news (cached)."""
        a = next((x for x in self.alerts_mem if x["id"] == alert_id), None) or self.store.alert(alert_id)
        if not a:
            return None
        if a.get("ai"):
            return a["ai"]
        news = [n for n in self.news.items if a["sym"] in n.get("syms", [])]
        text = self.ai.ask(alert_prompt(a, self.metrics.get(a["sym"]), news), 300, auto=auto)
        if text:
            self.store.set_alert_ai(alert_id, text)
            for x in self.alerts_mem:
                if x["id"] == alert_id:
                    x["ai"] = text
        return text

    def _refresh_stats(self):
        try:
            self.stats = scorecard(self.store, self.source)
        except Exception as e:
            self.log(f"Scorecard failed: {e}")

    def _brain_safe(self, rules_only=False):
        try:
            note = self.brain.run(force_rules=rules_only)
            if note and note.get("by") == "AI" and int((self.cfg().get("ai") or {}).get("brain_telegram") or 0):
                self.telegram.send(f"🧠 <b>{note.get('headline')}</b>\n" + "\n".join("• " + x for x in note.get("summary", [])[:4]))
        except Exception as e:
            self.log(f"Market brain failed: {e}")

    def _macro_alert(self, a):
        now = dt.datetime.now(IST)
        if self.source != "sim" and (now.weekday() >= 5 or not (dt.time(8, 0) <= now.time() <= dt.time(15, 30))):
            return                                   # 2.42: a world move at night is not an alert of the next day (India indication covers it)
        if self.running and self.source != "sim":
            self._emit_safe(a, self.clock.now(), self.cfg()["live"])
        elif self.running:
            a["title"] = "[real market] " + a["title"]
            self._emit_safe(a, self.clock.now(), self.cfg()["live"])

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
            live_every = max(20, int(((self.cfg().get("world") or {}).get("live_every_sec")) or 30))
            if self.world.errors.get("live"):
                live_every = min(600, live_every * 4)          # Yahoo pushing back: slow down
            for k, sec, fn in (("live", live_every, self.world.refresh_live), ("prices", 900, self.world.refresh_prices),
                               ("corr", 6 * 3600, self.world.refresh_correlations),
                               ("extremes", 6 * 3600, self.world.refresh_extremes),
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
            if due("health", 600):
                self._health_check()
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
        self._emit_safe({"sym": "PORTFOLIO", "kind": "flat_by", "direction": "neutral", "severity": 2,
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
        from lab.rules import events_named
        today = dt.datetime.now(IST).date()
        extra = [(dt.date.fromisoformat(d), f"India: {name}") for d, name in events_named()
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
        if not fo and self.uni:
            fo = set(self.uni.underlyings)
        fo = {x for x in fo if x not in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY")}
        if not fo:
            self.log("Insider trades: the F&O stock list is not loaded yet; will try again.")
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
        self._emit_safe(a, self.clock.now(), self.cfg()["live"])

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

    def session_day(self, now=None) -> dt.date:
        """The trading session the current data belongs to: before 15:30 (or on a weekend) on the live feed,
        that is the previous weekday's session; the simulator's clock is its own session."""
        now = now or (self.clock.now() if self.running or self.metrics else dt.datetime.now(IST))
        d = now.date()
        if self.source == "sim":
            return d
        if now.weekday() >= 5 or now.time() < dt.time(15, 30):
            if now.weekday() < 5 and now.time() >= dt.time(9, 15) and self.running:
                return d                                   # market open now: today's session, still running
            d -= dt.timedelta(days=1)
            while d.weekday() >= 5:
                d -= dt.timedelta(days=1)
        return d

    def make_brief(self, kind: str, send=True) -> dict:
        day = (self.clock.now() if self.running or self.metrics else dt.datetime.now(IST)).date().isoformat()
        if kind == "post":
            day = self.session_day().isoformat()
        if kind == "pre":
            facts = briefs.premarket_facts(self.context.view(), self._last_levels())
        else:
            ps = self.paper.summary(self.state) if self.paper else {}
            facts = briefs.postmarket_facts(self._indices(), self.regimes.get("NIFTY"),
                                            outcomes(self.store, self.source, day=day), ps, self._movers(),
                                            [a for a in self.alerts_mem if a["day"] == day])
            self.store.put(f"last_levels:{self.source}", {s: self.index_view(self.metrics[s])
                                                          for s in ("NIFTY", "BANKNIFTY") if s in self.metrics})
            try:
                rep = self.day_report(day)
                facts["day_report"] = {"paper": {k: rep["paper"][k] for k in ("n", "net", "win_rate", "by_gate", "outside_window")},
                                       "alerts": {k: rep["alerts"][k] for k in ("total", "focus", "graded", "focus_graded", "best_kinds", "worst_kinds")},
                                       "lessons": rep["lessons"]}
            except Exception as e:
                rep = None
                self.log(f"Day report failed: {e}")
        b = briefs.make(kind, facts, self.ai)
        if kind == "post" and rep:
            b["lines"] = [l for l in b["lines"] if not l.startswith(("Alerts:", "Paper trading:"))] + dayreport.text(rep)
        b["day"] = day
        b["source"] = self.source if kind == "post" else "market"
        key = kind if not (kind == "post" and self.source == "sim") else "post_sim"   # a simulator day never replaces the real review
        self.store.put(f"brief:{day}:{key}", b)
        days = self.store.get("brief_days", []) or []
        if day not in days:
            self.store.put("brief_days", ([day] + days)[:60])
        if send:
            self.telegram.send(briefs.telegram_text(b))
        return b

    def day_report(self, day: str | None = None, save=True) -> dict:
        """End-of-day report from the app's own records (live/dayreport.py). Real-market days only are saved."""
        src = self.source or self.cfg()["live"]["source"]
        day = day or self.session_day().isoformat()
        idx = self._indices() if self.metrics and (self.session_day().isoformat() == day) else []
        rep = dayreport.build(self.store, src, day, idx)
        if save and src != "sim":
            self.store.put(f"report:{day}", rep)
        return rep

    def briefs_view(self, day=None) -> dict:
        days = self.store.get("brief_days", []) or []
        day = day or (days[0] if days else dt.datetime.now(IST).date().isoformat())
        return {"day": day, "days": days, "pre": self.store.get(f"brief:{day}:pre"),
                "post": self.store.get(f"brief:{day}:post") or self.store.get(f"brief:{day}:post_sim"),
                "report": self.store.get(f"report:{day}")}

    # ------------------------------------------------------------ background: watcher + news
    def _watch_loop(self):
        """Auto-start at 9:00 on market days when logged in; remind to log in; briefs at 8:45 and 15:40.
        2.40: was named _watch, and a later `_watch` (the Sensex / Nasdaq list, 2.11) replaced it inside the class, so
        this loop never started (no 9:00 auto-start, health monitor, keep-awake or nightly Lab). Renamed; a test now
        fails on any method defined twice in a class."""
        while True:
            time.sleep(20)
            if getattr(self, "health", None) is not None:
                try:
                    self.health.tick()
                except Exception as e:
                    self.log(f"Health monitor error: {e}")
            if getattr(self, "indication", None) is not None:      # 2.40: starts its own thread when a slot is due
                try:
                    self.indication.tick()
                except Exception as e:
                    self.log(f"Market indication error: {e}")
            try:                                     # 2.42: candle-forecast models trained by themselves (child process)
                self.next_candle().cast().ensure()
            except Exception as e:
                if self._every("cast_ensure_err", 3600, clock_time=False):
                    self.log(f"Candle forecast training check: {e}")
            try:                                     # 2.54: your line book (smart chart lines), rebuilt while the scanner rests
                if not self.running and self._every("line_book_watch", 1800, clock_time=False):
                    from .linegrade import maybe_rebuild as _line_book
                    _line_book(self.data_dir, False, self.log)
            except Exception as e:
                if self._every("line_book_err", 3600, clock_time=False):
                    self.log(f"Line book rebuild: {e}")
            try:                                     # 2.42: a safe copy of the databases once a day after the close
                from . import backup
                backup.tick(self)
            except Exception as e:
                if self._every("backup_err", 3600, clock_time=False):
                    self.log(f"Backup error: {e}")
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
                if now.time() >= dt.time(15, 50) and ("paths", today) not in self.brief_done and not self.running:
                    self.brief_done.add(("paths", today))          # 2.45: trade journeys from today's recording
                    threading.Thread(target=self._trade_paths, daemon=True, name="trade-paths").start()
                lab_on = (self.cfg().get("lab") or {}).get("nightly", True)
                if lab_on and now.time() >= dt.time(16, 15) and ("lab", today) not in self.brief_done and not self.running:
                    self.brief_done.add(("lab", today))
                    threading.Thread(target=self._nightly_lab, daemon=True).start()
                if c["source"] != "upstox" or not c.get("auto_start"):
                    continue
                ses = SESSION.session(now.date())        # 2.48: the NSE calendar (special sessions too)
                if not ses or not (ses["start_scanner"] <= now < ses["close"] - dt.timedelta(minutes=5)) or self.running:
                    continue
                if self.manual_stop_day == now.date() or self.closed_day == now.date():
                    continue
                if self.tokens.load():
                    self.log(f"Auto-starting live scanner: {ses['name']} {ses['open']:%H:%M}-{ses['close']:%H:%M}")
                    self.start("upstox")
                elif self.reminded_day != now.date():
                    self.reminded_day = now.date()
                    self.status.update(state="login_needed", message="Log in with Upstox to start today's live scanner")
                    self.telegram.send("🔑 Stockway: log in with Upstox to start today's live scanner (open the app → Live).")
            except Exception as e:
                self.log(f"Watcher error: {e}")

    def _trade_paths(self):
        """2.45: every bench signal of the recorded days held to 15:15 (live/paths.py), in a low-priority child process."""
        try:
            from lab import proc
            proc.run("paths", self.data_dir, log=lambda m: self.log(f"Journeys: {m}"))
        except Exception as e:
            self.log(f"Trade journeys failed: {e}")
        try:                                             # 2.58: then the multiverse on every recorded day (incl. today)
            from lab import proc
            proc.run("multiverse", self.data_dir, log=lambda m: self.log(m))
        except Exception as e:
            self.log(f"Multiverse failed: {e}")

    def _nightly_lab(self):
        """Every evening: fetch new expiries (if logged in), switch off rules losing money live, ask Claude for a few
        new ideas, then a research round. Results on the Strategy Lab page. Paper only."""
        from lab import factory
        log = lambda m: self.log(f"Strategy Lab: {m}")
        try:
            hist = self.data_dir / "history"
            if not any(hist.glob("*/raw_*.parquet")):
                return
            tok = (self.tokens.load() or {}).get("access_token")
            if tok:
                try:
                    from lab.upstox_hist import download
                    end = dt.date.today() - dt.timedelta(days=1)
                    download(tok, self.data_dir, start=end - dt.timedelta(days=45), end=end, log=lambda m: None)
                except Exception as e:
                    log(f"new history not fetched ({e})")
            off = factory.apply_live_record(self.data_dir, self.factory_live_record())
            if off:
                log(f"switched off {len(off)} rule(s) that lost money in live paper trading: {', '.join(off)}")
            if self.ai.enabled() and (self.cfg().get("lab") or {}).get("claude_ideas", True):
                try:
                    q = factory.ask_ideas(self.data_dir, self.ai, auto=True)
                    log(f"Claude proposed {q['queued']} new rules")
                except Exception as e:
                    log(f"no Claude ideas tonight ({e})")
            try:                                          # library / book setups this Lab has not tested yet
                from lab import library
                q = factory.queue_untested(self.data_dir, library.rules(), source="library")
                if q["queued"]:
                    log(f"{q['queued']} untested library / book rules queued")
            except Exception as e:
                log(f"library not queued ({e})")
            from lab import proc                          # 2.38: in its own low-priority process (the app stays quick)
            proc.run("factory", self.data_dir, log=log, n_random=6000, n_mutate=3000)
            try:
                proc.run("mlstrat", self.data_dir, log=log)
            except Exception as e:
                log(f"ML strategies failed: {e}")
            try:                                          # the 5-year Lab (challenger) keeps searching too, once it has data
                from lab import dhan_hist
                r5 = dhan_hist.root(self.data_dir)
                if any(r5.glob("history/*/raw_*.parquet")):
                    from lab import library
                    factory.queue_untested(r5, library.rules(), source="library")
                    proc.run("factory", r5, log=lambda m: log("[5y] " + m), n_random=3000, n_mutate=1500)
            except Exception as e:
                log(f"5-year Lab failed: {e}")
            self._fact_rules = (0.0, [])
        except Exception as e:
            log(f"nightly run failed: {e}")

    def _news_loop(self):
        time.sleep(5)
        while True:
            try:
                if self.cfg()["live"].get("news"):
                    new = self.news.fetch(self.words or {})
                    new += self._dhan_news()
                    if new and self.running and self.recorder is not None:      # 2.31 data vault: every headline
                        try:
                            self.recorder.news(new, time.time())
                        except Exception:
                            pass
                    for n in new:
                        if n["syms"] and self.running and n["ts"] > time.time() - 1800:
                            self._news_alert(n)
            except Exception as e:
                self.log(f"News fetch failed: {e}")
            now = dt.datetime.now(IST)
            busy = now.weekday() < 5 and dt.time(8, 30) <= now.time() <= dt.time(15, 30)
            time.sleep(60 if busy else 300)            # every minute in market hours: news reaches alerts fast

    def _dhan_news(self) -> list:
        """Dhan's headlines into the same news list (symbols from Dhan's tags + the app's name matching)."""
        if not self.dhan_intel:
            return []
        from .notify import match_symbols, tone
        added = []
        for n in self.dhan_intel.news():
            if n["title"] in self.news.seen:
                continue
            self.news.seen.add(n["title"])
            syms = sorted(set(n.pop("dhan_syms")) | set(match_symbols(n["title"], self.words or {})))[:5]
            added.append({**n, "syms": syms, "tone": tone(n["title"])})
        if added:
            self.news.items = sorted(added + self.news.items, key=lambda x: -x["ts"])[:300]
        return added

    def _news_alert(self, n):
        sym = n["syms"][0]
        if self.uni and sym in self.uni.underlyings and not self.uni.underlyings[sym].is_index:
            d = "up" if n["tone"] > 0 else "down" if n["tone"] < 0 else "neutral"
            m = self.metrics.get(sym) or {"sym": sym}
            a = {"sym": sym, "kind": "news", "direction": d, "severity": 1, "title": f"News: {n['title'][:110]}",
                 "detail": f"{n['source']}. {sym} {m.get('chg_pct') or 0:+.2f}% now.", "price": m.get("spot"),
                 "data": {"link": n["link"]}}
            self._emit_safe(a, self.clock.now(), self.cfg()["live"])

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
                "unusual": len(o.get("unusual") or []), "score": m.get("score"), "heat": m.get("heat"), "lot": m.get("lot"),
                "px_ts": (m["ltt"] / 1000) if m.get("ltt") else m.get("t")}   # 2.49: when this price was traded

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
            if hasattr(self.feed, "view"):                # 2.52: the backup feed (Kite) and the cross-check
                try:
                    feed["router"] = self.feed.view()
                except Exception:
                    pass
        new = [a for a in self.alerts_mem if a["id"] > since_id][:100]
        ps = self.paper.summary(self.state) if self.paper else None
        cv = self.context.view()
        return {"status": {**self.status, "running": self.running, "logged_in": bool(self.tokens.load()),
                           "feed": feed, "ticks": self.state.tick_count if self.state else 0},
                "clock": now.isoformat(), "mins": minutes_since_open(now), "fetched": time.time(),
                "session": {**SESSION.view(), "paused_today": self.manual_stop_day == dt.datetime.now(IST).date(),
                            "auto_start": bool(self.cfg()["live"].get("auto_start", True))},
                "nse": self._nse_state(),
                "indication": self.indication.view() if getattr(self, "indication", None) is not None else None,
                "indices": self.index_cards(), "vix": self._vix(),
                "alerts": new, "pulse": self.pulse if self.running or not self._last_session() else
                {**self.pulse, "lines": self.pulse.get("lines") or self._last_session().get("pulse") or []},
                "movers": self._movers() if self.metrics else self._last_session().get("movers"),
                "market": self.market or ({} if self.running else self._last_session().get("market") or {}),
                "last_session": None if self._indices() else {"asof": self._last_session().get("asof"), "day": self._last_session().get("day")},
                "context": {"fii_dii": cv["fii_dii"], "global": cv["global"], "ban": cv["ban"],
                            "fii": next((r for r in ((cv["participants"] or {}).get("rows") or []) if r["who"] == "FII"), None)},
                "paper": {"open": len(ps["open"]), "net": ps["net"], "unreal": ps["unreal"], "n": ps["n"],
                          "win_rate": ps["win_rate"], "guard": ps.get("guard")} if ps else None,
                "news": self.news.items[:12], "ai": {"enabled": self.ai.enabled(), "used": self.ai.used,
                                                     "error": self.ai.last_error},
                "watch": self._watch(), "extra": self.extra_indices(),
                "depth": {k: self.depth.view(k) for k in self.depth.ids} if self.depth else None,
                "dhan": {"depth": dict(self.depth.status) if self.depth else None,
                         "movers": {k: len(v) for k, v in self.dhan_intel.movers.items()} if self.dhan_intel else None,
                         "in_play": sorted({x for v in self.dhan_intel.movers.values() for x in v[:5]})[:12] if self.dhan_intel else [],
                         "errors": dict(self.dhan_intel.errors) if self.dhan_intel else {}} if (self.depth or self.dhan_intel) else None,
                "px": {k: [round(m["spot"], 2), round(m.get("chg_pct") or 0, 2)] for k, m in (self.metrics or {}).items() if m.get("spot")}}

    WATCH = (("Nasdaq 100 fut", "NQ=F"), ("Dow fut", "YM=F"), ("S&P 500 fut", "ES=F"))

    def extra_indices(self) -> list:
        """Sensex (Upstox live, else Yahoo) and GIFT Nifty (NSE IX) next to Nifty and Bank Nifty."""
        out = []
        i = self.state.get(SENSEX_KEY) if self.state else None
        r = (getattr(self.world, "rows", {}) or {}).get("^BSESN") or {}
        if i and i.ltp:
            sx = {"name": "Sensex", "sym": "SENSEX", "spot": i.ltp, "chg_pct": (i.ltp / i.cp - 1) * 100 if i.cp else None,
                  "chg": i.ltp - i.cp if i.cp else None, "prev": i.cp, "day_h": i.day_h, "day_l": i.day_l, "src": "BSE, live (Upstox)"}
            out.append(sx)
        elif r.get("last") is not None:
            out.append({"name": "Sensex", "sym": "SENSEX", "spot": r["last"], "chg_pct": r.get("chg_pct"), "prev": r.get("prev"),
                        "chg": r["last"] - r["prev"] if r.get("prev") else None, "day_h": r.get("day_h"), "day_l": r.get("day_l"),
                        "src": "Yahoo, about 1 min behind"})
        if out:
            try:
                out[0].update(self.card_extras("SENSEX", out[0]["spot"], None, out[0].get("day_h"), out[0].get("day_l")))
            except Exception:
                pass
        g = dict(self.gift.get() or {})
        if g:
            nifty = (self.metrics.get("NIFTY") or {}).get("spot")
            saved = next((c for c in (self._last_session().get("indices") or []) if c.get("sym") == "NIFTY"), {})
            ref = nifty or saved.get("spot") or ((getattr(self.world, "rows", {}) or {}).get("^NSEI") or {}).get("last")
            if ref and g.get("spot"):
                # GIFT minus Nifty: while Nifty trades it is the futures premium; after the close it hints at the next open
                g["vs_nifty"] = round(g["spot"] - ref, 1)
                g["nifty_ref"] = round(ref, 2)
                g["vs_nifty_when"] = "now" if self.running and nifty else "close"
        out.append(g if g else {"name": "GIFT Nifty", "spot": None, "src": "NSE IX: loading…" if not self.gift.err else "NSE IX not reachable now"})
        try:                                          # 2.38: is it trading right now?
            from .marketclock import state as _mstate
            for c_ in out:
                c_["mkt"] = _mstate("GIFT" if c_.get("name") == "GIFT Nifty" else "NSE", last_ts=c_.get("ts"))
        except Exception:
            pass
        return out

    # ------------------------------------------------------------ Live page when the scanner is off (2.29)
    LAST_SESSION = "last_session.json"

    def card_extras(self, sym, spot, opt=None, day_h=None, day_l=None, prev_override=None) -> dict:
        """All-time / 52-week high and low (Yahoo) and merged support / resistance for an index card."""
        from .levels import key_levels
        from .world import YAHOO_INDEX
        from . import ta
        ext = (getattr(self.world, "extremes", {}) or {}).get(YAHOO_INDEX.get(sym, ""), {}) if self.world else {}
        try:                                             # 2.56.2: Yahoo's history too short (Midcap Select): the saved daily bars
            from .world import short_history
            if short_history(ext):
                ext = self._daily_extremes(sym, day_h, day_l) or {}
        except Exception:
            pass
        d = self.daily.get(sym) or (self._last_session().get("daily") or {}).get(sym) or {}
        h = d.get("htf") or {}
        prev = d.get("prev") or {}
        ph, pl, pc = prev.get("h") or h.get("prev_h"), prev.get("l") or h.get("prev_l"), prev.get("c") or h.get("prev_c")
        if prev_override and all(prev_override):         # market closed: the last session is "yesterday" for the next one
            ph, pl, pc = prev_override
            day_h = day_l = None
        cp = ta.cpr(ph, pl, pc)
        o = opt or {}
        named = [("Put OI wall", o.get("support")), ("Call OI wall", o.get("resistance")), ("Max pain", o.get("max_pain")),
                 ("Yesterday's high", ph), ("Yesterday's low", pl), ("Yesterday's close", pc),
                 ("Pivot", cp.get("P")), ("R1", cp.get("R1")), ("R2", cp.get("R2")), ("S1", cp.get("S1")), ("S2", cp.get("S2")),
                 ("Last week's high", h.get("pwh")), ("Last week's low", h.get("pwl")), ("20-day high", h.get("hi20")),
                 ("20-day low", h.get("lo20")), ("52-week high", ext.get("hi52")), ("52-week low", ext.get("lo52")),
                 ("All-time high", ext.get("ath")), ("Day high", day_h), ("Day low", day_l)]
        out = {"levels": key_levels(spot, named)}
        if ext:
            out["ext"] = {k: ext.get(k) for k in ("ath", "ath_date", "atl", "atl_date", "since", "hi52", "lo52")}
            if spot and ext.get("ath"):
                out["ext"]["from_ath_pct"] = round((spot / ext["ath"] - 1) * 100, 2)
        if h:
            out["trend"] = {"daily": h.get("trend"), "weekly": h.get("w_trend")}
        return out

    def _daily_extremes(self, sym, day_h=None, day_l=None) -> dict | None:
        """52-week (and, with 3+ years, all-time) high / low from the chart's saved daily bars (data/charts/SYM_D.json, kept by
        the chart from Dhan). Read from disk at most every 30 min; no network call here."""
        from .world import extremes_from_daily
        c = getattr(self, "_dext", None)
        if c is None:
            c = self._dext = {}
        hit = c.get(sym)
        if not hit or time.time() - hit[0] > 1800:
            rows = []
            try:
                rows = json.loads((Path(self.data_dir) / "charts" / f"{sym}_D.json").read_text()).get("rows") or []
            except Exception:
                rows = []
            hit = c[sym] = (time.time(), rows)
        return extremes_from_daily(hit[1], day_h, day_l)

    def index_cards(self) -> list:
        """Index cards: live while the scanner runs; otherwise the last session's (saved every minute), else Yahoo."""
        live = self._indices()
        if live:
            out = []
            for m in live:
                x = self.index_view(m)
                try:
                    x.update(self.card_extras(m["sym"], m.get("spot"), m.get("opt"), m.get("day_h"), m.get("day_l")))
                except Exception:
                    pass
                out.append(x)
            return out
        return self.closed_cards()

    def closed_cards(self) -> list:
        saved = self._last_session()
        cards = saved.get("indices") or []
        if not cards and self.world is not None:          # first run: Yahoo prices for the main indices
            from .world import YAHOO_INDEX
            names = {"NIFTY": "Nifty 50", "BANKNIFTY": "Bank Nifty", "FINNIFTY": "Fin Nifty", "MIDCPNIFTY": "Midcap Select"}
            for sym, t in YAHOO_INDEX.items():
                r = (self.world.rows or {}).get(t) or {}
                if sym in names and r.get("last"):
                    cards.append({"sym": sym, "name": names[sym], "is_index": True, "spot": r["last"], "chg_pct": r.get("chg_pct"),
                                  "day_h": r.get("day_h"), "day_l": r.get("day_l"), "opt": {}, "src": "Yahoo"})
        out = []
        for c in cards:
            c = dict(c)
            try:
                c.update(self.card_extras(c["sym"], c.get("spot"), c.get("opt"), c.get("day_h"), c.get("day_l"),
                                          prev_override=(c.get("day_h"), c.get("day_l"), c.get("spot"))))
            except Exception:
                pass
            c["closed"] = True
            c["asof"] = saved.get("asof")
            out.append(c)
        return out

    def _last_session(self) -> dict:
        f = self.data_dir / self.LAST_SESSION
        hit = getattr(self, "_ls_cache", None)
        try:
            mt = f.stat().st_mtime
        except OSError:
            return {}
        if hit and hit[0] == mt:
            return hit[1]
        try:
            d = __import__("json").loads(f.read_text())
        except Exception:
            d = {}
        self._ls_cache = (mt, d)
        return d

    def save_last_session(self):
        """The Live page's index cards, breadth, sectors and movers, kept for when the scanner is off."""
        import json
        if not self._indices() or self.source == "sim":
            return
        if minutes_since_open(self.clock.now()) < 0:     # 2.38: before 9:15 the prices are yesterday's: keep the real session
            return
        d = {"asof": time.time(), "day": self.clock.now().date().isoformat(),
             "indices": [self.index_view(m) for m in self._indices()], "market": self.market,
             "daily": {m["sym"]: {"prev": (self.daily.get(m["sym"]) or {}).get("prev"), "htf": (self.daily.get(m["sym"]) or {}).get("htf")}
                       for m in self._indices()},
             "movers": self._movers() if self.metrics else None, "pulse": self.pulse.get("lines") or []}
        f = self.data_dir / self.LAST_SESSION
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, default=str))
        tmp.replace(f)

    def _nse_state(self) -> dict | None:
        """2.38: NSE open / closed / holiday now (rules/market_hours.json); the simulator is always 'open'."""
        if self.source == "sim" and self.running:
            return {"session": "NSE", "open": True, "label": "Simulator"}
        try:
            from .marketclock import state as _mstate
            last = getattr(self.state, "last_tick", None) if (self.running and self.state) else None
            return _mstate("NSE", last_ts=last or None)
        except Exception:
            return None

    def markets_board(self) -> dict:
        """World markets for the Live page: US, Asia, Europe, money and commodities (Yahoo, about a minute behind)."""
        from .world import LIVE_BOARD
        rows = (getattr(self.world, "rows", {}) or {}) if self.world else {}
        ext = (getattr(self.world, "extremes", {}) or {}) if self.world else {}
        groups = []
        for gname, items in LIVE_BOARD:
            out = []
            for name, t in items:
                if t == "_GIFT":
                    g = self.gift.get() or {}
                    if g.get("spot"):
                        out.append({"name": name, "ticker": "GIFT", "last": g["spot"], "chg_pct": g.get("chg_pct"), "day_h": g.get("day_h"),
                                    "day_l": g.get("day_l"), "ts": g.get("ts")})
                    continue
                r = rows.get(t) or {}
                if r.get("last") is None:
                    continue
                e_ = ext.get(t) or {}
                out.append({"name": name, "ticker": t, "last": r["last"], "chg_pct": r.get("chg_pct"), "prev": r.get("prev"),
                            "day_h": r.get("day_h"), "day_l": r.get("day_l"), "hi52": r.get("hi52") or e_.get("hi52"),
                            "lo52": r.get("lo52") or e_.get("lo52"), "ath": e_.get("ath"), "ts": r.get("live_ts"),
                            "asof": r.get("live_date") or r.get("asof"), "spark": (r.get("spark_day") or r.get("spark") or [])[-60:]})
            if out:
                groups.append({"group": gname, "items": out})
        try:                                          # 2.38: Open / Closed / Break for every card (rules/market_hours.json)
            from .marketclock import state as _mstate
            for g_ in groups:
                for x in g_["items"]:
                    x["mkt"] = _mstate(x["ticker"], last_ts=x.get("ts"))
        except Exception:
            pass
        try:
            pr = self.world.pressure() if self.world else None
        except Exception:
            pr = None
        return {"groups": groups, "pressure": pr, "updated": (getattr(self.world, "updated", {}) or {}).get("live"),
                "fetched": time.time()}

    def _watch(self) -> list:
        """Sensex, Nasdaq, Dow and S&P on the Live page (Yahoo 1-minute prices, about a minute behind)."""
        out = []
        rows = getattr(self.world, "rows", {}) or {}
        for name, t in self.WATCH:
            r = rows.get(t) or {}
            if r.get("last") is None:
                continue
            out.append({"name": name, "ticker": t, "last": r.get("last"), "chg_pct": r.get("chg_pct"),
                        "ts": r.get("live_ts"), "spark": r.get("spark_day")})
        return out

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

    def _shadow_board(self, src):
        try:
            from .shadow import SQL, board
            with self.store.lock:
                self.store.db.executescript(SQL)
            return board(self.store, src)
        except Exception as e:
            return {"styles": [], "error": str(e)}

    def scorecard_view(self, source=None) -> dict:
        src = source or self.source or self.cfg()["live"]["source"]
        sc = scorecard(self.store, src)
        rows = []
        for k, v in sc.items():
            if len(k) == 2:
                rows.append({"kind": k[0], "is_index": bool(k[1]), **v})
        rows.sort(key=lambda r: (-((r.get("decided") or 0) >= 10), -(r["hit_rate"] or 0)))
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
        honest = {}
        try:                                         # 2.30: the same alert types paper-traded honestly (live/bench.py)
            from .bench import day_groups, db_for     # 2.31: from data/bench.db, finished days cached (never blocks)
            for g in day_groups(db_for(self.store), src, 365, self.clock.now().date().isoformat() if self.clock else None):
                if g["style"] != "buy":
                    continue
                h = honest.setdefault(f"{g['kind']}|{int(g['is_index'] or 0)}", {"n": 0, "net": 0.0, "w": 0})
                h["n"] += g["n"]
                h["net"] += g["s"] or 0
                h["w"] += g["wins"] or 0
            honest = {k: {"n": h["n"], "net": round(h["net"]), "avg": round(h["net"] / h["n"]) if h["n"] else None,
                          "win_pct": round(h["w"] / h["n"] * 100) if h["n"] else None} for k, h in honest.items()}
        except Exception:
            honest = {}
        for r_ in rows:
            r_["honest"] = honest.get(f"{r_['kind']}|{int(r_['is_index'])}")
        return {"source": src, "rows": rows, "recent": recent[:120], "graded": len(graded), "honest": bool(honest),
                "overall": round(sum(r["result"] == "hit" for r in graded) / len(graded) * 100) if graded else None,
                "model": ranker.info, "recordings_mb": size_mb(self.data_dir), "exits": self._shadow_board(src),
                "rule": "With an option idea: hit = option +20% before −20% within 60 minutes. Without: price moves "
                        "about one 30-minute sigma in the alert's direction before the same against it. Neither = flat."}
