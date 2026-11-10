"""No-clash checks: the things that must never collide, checked the same way in the tests and on the System page.

Static checks look at the code and settings (no network, no engine). Runtime checks look at the running app.
Each check: {"id", "name", "ok": True / False / None (not applicable now), "detail"}.
"""
from __future__ import annotations

import inspect
import time
import shutil
from pathlib import Path


def _c(id_, name, ok, detail=""):
    return {"id": id_, "name": name, "ok": ok, "detail": detail}


def static_checks(cfg: dict | None = None) -> list:
    from lab import dhan, dhan_budget, dhan_hist, dhan_token
    out = []
    src = inspect.getsource(dhan.Dhan._wait)
    out.append(_c("dhan_shared_pace", "All Dhan calls share one speed limit and one daily counter",
                  "dhan_budget.wait" in src and "dhan_budget.spend" in src and dhan_budget.RATE <= 5.0,
                  f"{dhan_budget.RATE} calls/s for everything together (Dhan allows 5)"))
    reserve = dhan_budget.DAY_LIMIT - dhan_hist.DAILY_CAP
    out.append(_c("dhan_day_split", "The 5-year download leaves room for live news and movers",
                  0 < dhan_hist.DAILY_CAP <= dhan_budget.DAY_LIMIT and reserve >= 5000,
                  f"download up to {dhan_hist.DAILY_CAP:,} a day, {reserve:,} kept for live (Dhan limit {dhan_budget.DAY_LIMIT:,})"))
    out.append(_c("dhan_one_keeper", "One token keeper: token changes happen one at a time",
                  hasattr(dhan_token, "LOCK") and "with LOCK" in inspect.getsource(dhan_token.maybe_renew),
                  "renew, TOTP, paste and Log in with Dhan all go through the same lock"))
    c = (cfg or {}).get("live") or {}
    budget = int(c.get("option_budget") or 1900)
    out.append(_c("upstox_budget", "Upstox live options stay under Upstox's 2,000 limit", budget <= 2000,
                  f"option budget {budget:,}"))
    from live import dhandepth
    out.append(_c("dhan_depth_sockets", "Dhan 200-level order book uses at most 5 sockets",
                  "full-depth-api" in dhandepth.URL, "one socket per index future (Nifty, Bank Nifty)"))
    from lab import rules
    out += rules.check()
    from live import engine as _eng, registry
    first = [ln.strip() for ln in inspect.getsource(_eng.LiveEngine._emit).splitlines()[1:] if ln.strip()][:2]
    out.append(_c("trials_isolated", "Ideas on trial never reach your alerts, Telegram or paper trades",
                  bool(first) and "is_shadow" in first[0] and "_trial" in first[1],
                  f"{len(registry.shadow_kinds())} idea(s) on trial: recorded and graded only (Scorecard, On trial)"))
    from lab import factory
    out.append(_c("lab_one_run", "Strategy Lab: one research round per Lab folder at a time",
                  hasattr(factory, "_RUN_LOCKS"), "a second run waits for the first instead of overwriting it"))
    # 2.31: test bench styles, mover sweep and data vault
    from live import bench, movers, vault
    styles, errs = bench.load_styles()
    fam = {f: sum(1 for v in styles.values() if v["family"] == f) for f in bench.FAMILIES}
    out.append(_c("bench_styles", "Test bench styles load (rules/bench_styles.json); a broken one is skipped",
                  not errs, (f"{len(styles)} styles: {fam['buy']} only buying, {fam['mixed']} buying + selling, "
                             f"{fam['sell']} only selling") + (f"; skipped: {'; '.join(errs)}" if errs else "")))
    movers.cfg()
    out.append(_c("mover_sweep", "Mover sweep settings load (rules/bench_movers.json)", not movers._C.get("error"),
                  movers._C.get("error") or f"every {movers.cfg()['every_min']} min, top {movers.cfg()['top_n']} stocks, both ways"))
    from live import mtfcandles
    cc = mtfcandles.cfg()
    out.append(_c("candle_scanner", "Candle scanner settings load (rules/candle_tfs.json)", not mtfcandles._C.get("error"),
                  mtfcandles._C.get("error") or ("timeframes " + ", ".join(mtfcandles.TF_NAME.get(int(x), f"{x}m") for x in cc["intraday_min"])
                                                 + (", 1D" if cc.get("daily") else "") + (", 1W" if cc.get("weekly") else "")
                                                 + f"; on the test bench: {', '.join(cc.get('bench') or []) or 'none'}")))
    from live import events as _ev
    ec = _ev.cfg()
    out.append(_c("event_sources", "Event engine settings load (rules/event_sources.json); event risk never blocks anything",
                  not _ev._C.get("error") and bool(ec.get("sources")),
                  _ev._C.get("error") or f"{len(ec.get('sources') or [])} official feeds: " + ", ".join(x['name'] for x in ec.get('sources') or [])))
    from live import dataguard
    dc = dataguard.cfg()
    esrc = inspect.getsource(_eng.LiveEngine._emit)
    out.append(_c("data_guard", "Data quality guard: suspect / stale prices hold their alerts and never fill a test trade",
                  not dataguard._C.get("error") and "_data_hold" in esrc,
                  dataguard._C.get("error") or f"mode {dc['mode']}; jumps over {dc['jump_pct'].get('index')}% (index) / "
                                               f"{dc['jump_pct'].get('stock')}% (stock) in {dc['every_s']} s are checked"))
    from live import focuslab, notify, paper, riskgate
    fl = focuslab.load()
    reads = [m.__name__ for m in (paper, notify, riskgate) if "focus_ch" in inspect.getsource(m)]
    out.append(_c("focus_shadow", "Shadow Focus: challengers load (rules/focus_shadow.json) and nothing you see or trade reads them",
                  not fl["error"] and not fl["errors"] and bool(fl["versions"]) and not reads,
                  fl["error"] or (f"read by {', '.join(reads)}" if reads else
                                  f"{len(fl['versions'])} version(s) on trial: {', '.join(fl['versions'])} (main: {fl['main']})"
                                  + (f"; skipped: {'; '.join(fl['errors'])}" if fl["errors"] else ""))))
    from lab import dhan as _dh, proc as _proc
    nsrc = inspect.getsource(_eng.LiveEngine._nightly_lab)
    out.append(_c("lab_own_process", "Strategy Lab rounds run in their own low-priority process (screens stay quick)",
                  "proc.run(" in nsrc and "factory.run(" not in nsrc and hasattr(_proc, "_child"),
                  f"nightly and manual rounds: a child process at nice {_proc.NICE}; the same code, the same results"))
    out.append(_c("dhan_priority", "Screens, news and movers go before the 5-year Dhan download on the shared pace",
                  "priority=not self.background" in inspect.getsource(_dh.Dhan._wait),
                  "the download waits while a screen's Dhan call is waiting or ran in the last 2 s"))
    from live import indication as _ind
    ic = _ind.cfg()
    wsrc = inspect.getsource(_eng.LiveEngine._watch_loop)
    isrc = inspect.getsource(_eng.LiveEngine.__init__)
    out.append(_c("watcher_loop", "The app's watcher loop runs (9:00 auto-start, health monitor, keep-awake, briefs, nightly Lab)",
                  "target=self._watch_loop" in isrc and "while True" in wsrc and "self.health.tick()" in wsrc,
                  "one thread, every 20 s (2.40: a duplicate method name had silently replaced it since 2.11)"))
    out.append(_c("market_indication", "India market indication: slots load (rules/indication.json) and each reading runs in its own thread",
                  not _ind._C.get("error") and len(_ind.slots(__import__("datetime").date(2026, 11, 4), ic)) >= 2
                  and "self.indication.tick()" in wsrc and "threading.Thread" in inspect.getsource(_ind.Indication.tick),
                  _ind._C.get("error") or f"{ic['start']}-{ic['end']} every {ic['every_min']} min; AI up to {ic['max_ai_per_day']} a day "
                                          f"({'on' if ic.get('ai') else 'off'}), else rules"))
    from live import nextcandle as _nc
    prs = _nc.pairs()
    lsrc = inspect.getsource(_eng.LiveEngine._nextcandle_loop)
    out.append(_c("next_candle", "Next-candle cascade loads (rules/fortune.json) and runs in its own thread (never in the scanner loop)",
                  bool(prs) and not _nc._C.get("error") and "next_candle().tick()" in lsrc
                  and "nextc" not in inspect.getsource(_eng.LiveEngine._loop),
                  _nc._C.get("error") or ", ".join(f"{v['after']} × {v['from']} → {k}" for k, v in prs.items())
                  + "; information only, graded at every close"))
    from live import applog as _al, backup as _bk
    bc = _bk.cfg()
    wsrc2 = inspect.getsource(_eng.LiveEngine._watch_loop)
    out.append(_c("db_backup", "Daily safe copy of live.db and bench.db after the close (SQLite backup, checked, kept a week)",
                  "backup.tick(self)" in wsrc2 and not _bk._C.get("error") and not any(x in " ".join(bc["files"]).lower() for x in _bk.NEVER),
                  _bk._C.get("error") or f"after {bc['after']}, {bc['keep']} days kept in {bc['dir'] or 'data/backups'}"
                                         f"{'' if bc.get('on', True) else ' (switched off)'}"))
    out.append(_c("app_log", "Engine log kept on disk (data/logs/app.log, rotated), tokens and keys masked",
                  "applog import write" in inspect.getsource(_eng.LiveEngine.log) and _al.mask("access_token=abcdef123456") == "access_token=<hidden>",
                  "5 MB × 10 files"))
    from live import candlecast as _cc
    from lab import proc as _proc
    ccc = _cc.cfg()
    out.append(_c("candle_forecast", "Candle forecast (2.41): trained in a low-priority child process, predicted in the next-candle thread, every stage graded",
                  "candlecast" in _proc.JOBS and 'proc.run("candlecast"' in inspect.getsource(_cc.Cast.ensure)
                  and "_cast_step" in inspect.getsource(_nc.NextCandle.tick) and "_TRAIN.acquire(blocking=False)" in inspect.getsource(_cc.Cast.ensure)
                  and not _cc._C.get("error"),
                  _cc._C.get("error") or f"{', '.join(ccc['symbols'])}; PAKKA = the strength that was {int(float(ccc['target']) * 100)}% right "
                                         f"on validation days; one training at a time, again every {ccc['retrain_days']} days after {ccc['train_after']}"))
    vsrc = inspect.getsource(_eng.LiveEngine._vault_loop)
    vc = vault.cfg()
    out.append(_c("vault_spare_calls", "Data vault: stock option chains only with spare Upstox calls, never changing the scanner",
                  "lim.used(1800)" in vsrc and "keep=False" in vsrc and 0 < float(vc["max_upstox_share"]) < 1,
                  f"waits while more than {int(float(vc['max_upstox_share']) * 100)}% of the 30-minute Upstox budget is busy; "
                  f"every {vc['stock_chains_every_min']} min"))
    return out


def runtime_checks(engine=None, data_dir: str | Path = "data", jobs: dict | None = None) -> list:
    from lab import dhan_auto, dhan_budget, dhan_token
    out = []
    jobs = jobs or {}
    free = shutil.disk_usage(Path(data_dir)).free / 1e9
    out.append(_c("disk", "Enough free disk (10 GB+)", free >= 10, f"{free:.1f} GB free"))
    used = dhan_budget.used()
    out.append(_c("dhan_calls", "Dhan calls today under Dhan's daily limit", used < dhan_budget.DAY_LIMIT,
                  f"{used:,} of {dhan_budget.DAY_LIMIT:,}"))
    tok = dhan_token.status()
    has_tok = bool(dhan_token.latest())
    out.append(_c("dhan_token", "Dhan token valid", (not tok.get("dead")) if has_tok else None,
                  "expired: TOTP will replace it" if tok.get("dead") and dhan_auto.load() else
                  "expired" if tok.get("dead") else f"valid till {tok.get('expires')}" if tok.get("expires") else
                  "no token saved" if not has_tok else "valid"))
    au = dhan_auto.status()
    out.append(_c("dhan_totp", "Dhan automatic login (TOTP) not stopped", (not au.get("blocked")) if au.get("on") else None,
                  "off" if not au.get("on") else f"stopped: {au.get('last_error')}" if au.get("blocked") else "on"))
    if engine is not None:
        subs = len(getattr(engine, "opt_subs", None) or [])
        budget = int((engine.cfg().get("live") or {}).get("option_budget") or 1900)
        out.append(_c("upstox_subs", "Upstox options streamed within the budget", subs <= budget if subs else None,
                      f"{subs:,} of {budget:,}" if subs else "scanner not running"))
        dp = getattr(engine, "depth", None)
        n = len(getattr(dp, "ids", {}) or {}) if dp else 0
        out.append(_c("dhan_depth", "Dhan order-book sockets ≤ 5", n <= 5 if dp else None, f"{n} socket(s)" if dp else "off"))
        out.append(_c("one_price_feed", "One live price feed (Upstox, or the simulator)",
                      (engine.source in ("upstox", "sim")) if engine.running else None,
                      f"{engine.source}" if engine.running else "scanner not running"))
    if engine is not None and getattr(engine, "context", None) is not None and getattr(engine, "source", None) != "sim":
        cal = getattr(engine.context, "events_cal", None) or []
        out.append(_c("results_calendar", "Results calendar loaded (NSE board meetings)", bool(cal) or None,
                      f"{len(cal):,} upcoming board meetings" if cal else
                      (f"not loaded: {engine.context.errors.get('events')}" if engine.context.errors.get("events") else "loads with the market context")))
        dl = getattr(engine, "daily", None) or {}
        n_htf = sum(1 for k, v in dl.items() if isinstance(v, dict) and v.get("htf"))
        out.append(_c("daily_trend", "Daily / weekly trend known for the scanned symbols", (n_htf > 0) if engine.running else None,
                      f"{n_htf} symbols" if n_htf else "loads with the daily candles at start"))
    if engine is not None:
        hm = getattr(engine, "health", None)
        age = (time.time() - hm.last_check) if hm is not None and hm.last_check else None
        out.append(_c("health_monitor", "Health monitor watching (gaps, scanner, recording, disk, power)",
                      (age < 120) if age is not None else None,
                      f"last check {int(age)} s ago" if age is not None else "first check within a minute of starting"))
        rec = getattr(engine, "recorder", None)
        h = None
        if rec is not None and engine.running:
            try:
                h = rec.health()
            except Exception:
                h = None
        out.append(_c("vault_recording", "Today's market data is being recorded (prices, option chains, news)",
                      (bool(h.get("minutes")) if h else None),
                      (f"{h.get('minutes') or 0} minutes, {h.get('instruments') or 0} instruments, {h.get('chain_snaps') or 0} chain "
                       f"snapshots, {h.get('news') or 0} headlines" if h else "scanner not running")))
    d5, research5 = jobs.get("dhan5y_running"), jobs.get("research5y_running")
    out.append(_c("download_vs_research", "5-year download and 5-year research not on the same data at once",
                  not (d5 and research5) if (d5 or research5) else None,
                  "both running: research would use half-downloaded data" if d5 and research5 else "fine"))
    sched = jobs.get("scheduler_age_s")
    out.append(_c("scheduler", "Background scheduler alive (auto-renew, auto-resume)",
                  (sched is not None and sched < 180) if sched is not None else None,
                  f"last tick {int(sched)} s ago" if sched is not None else "starting"))
    try:                                                   # 2.45: trade journeys (live/paths.py), built after each close
        from live import paths as _pa
        if _pa.db_path(data_dir).exists():
            _db = _pa.connect(data_dir, ro=True)
            last = _db.execute("SELECT day, n, note FROM path_days WHERE source='upstox' ORDER BY day DESC LIMIT 1").fetchone()
            _db.close()
        else:
            last = None
        out.append(_c("trade_paths", "Trade journeys built from each day's recording (every bench signal held to 15:15)",
                      (last is not None) if last is not None else None,
                      f"last day {last['day']}: {last['n']} signals" + (f" ({last['note']})" if last['note'] else "") if last
                      else "first run after the next close"))
    except Exception as e:
        out.append(_c("trade_paths", "Trade journeys readable", False, str(e)[:160]))
    try:                                                   # 2.43: strategy files (lab/packs.py): data only, checked on upload
        from lab import packs as _pk
        cur = _pk.current(data_dir, only_on=False)
        bad = [pid for pid, _e, res in cur if res.get("fatal")]
        n_on = sum(1 for _pid, e, _r in cur if e.get("on", True))
        out.append(_c("strategy_files", "Strategy files readable (uploaded on the Strategy Lab page; data only, never run as code)",
                      not bad if cur else None,
                      (f"{len(cur)} file(s), {n_on} on, {sum(len(r.get('rules', [])) for _p, _e, r in cur)} Lab rules"
                       + (f"; can't read: {', '.join(bad)}" if bad else "")) if cur else "no strategy files yet"))
    except Exception as e:
        out.append(_c("strategy_files", "Strategy files readable", False, str(e)[:160]))
    try:                                                   # 2.47: bigger-timeframe facts (lab/tfacts.py, rules/tf_facts.json)
        from lab import factory as _F, tfacts as _T
        cfg = _T.config()
        unknown = [ind for inds in (cfg.get("tfs") or {}).values() for ind in inds if ind not in _T.IND]
        cache = getattr(getattr(engine, "charts", None), "_tfh", {}) if engine is not None else {}
        have = sum(1 for v in cache.values() if v[1])
        out.append(_c("tf_facts", "Bigger-timeframe facts (15m / 30m / 1H, many days; history from Dhan 5-min bars, once a day per symbol)",
                      not unknown and len(_F.TF_FACTS) > 0,
                      f"{len(_F.TF_FACTS)} facts, {cfg.get('history_days')} days of history"
                      + (f"; live history ready for {have} symbol(s) today" if cache else "; fetched when a rule needs them")
                      + (f"; unknown indicators in rules/tf_facts.json: {', '.join(unknown)}" if unknown else "")))
    except Exception as e:
        out.append(_c("tf_facts", "Bigger-timeframe facts", False, str(e)[:160]))
    return out
