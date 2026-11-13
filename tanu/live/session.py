"""One market day of the live path, end to end: universe -> contexts -> engine (+ paper accounts, journal, alerts) ->
feed process -> live loop. Used by the supervisor (`python -m tanu run`, after cutover) and by the rehearsal (a past
day through a local fake broker). The same Engine class as replay (ADR-0004)."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from ..core import log
from ..data.facts import Facts
from ..data.feed import FeedProcess
from ..engine.core import Engine
from ..evaluate.run import SPREAD_PCT
from ..journal.store import Journal
from ..paper.accounts import Accounts
from ..setups import registry
from . import context
from .loop import LiveLoop
from .options import LiveOptions
from .universe import Universe

_log = log.get("session")


class Session:
    def __init__(self, base: Path | str, day: str, rows: list[dict], *, url: str, token: str, out_dir: Path | str,
                 journal_path: Path | str, tape_dir: Path | str | None, clock: str = "wall", spec: dict | None = None,
                 rank: list[str] | None = None, sender=None, evidence: dict | None = None, warm_days: int = 2,
                 lots: str = "universe", min_free_gb: float = 10.0, grace_s: float = 2.0, facts: Facts | None = None):
        self.base, self.day, self.out = Path(base), day, Path(out_dir)
        self.facts = facts or Facts()
        self.u = Universe(rows, day, spec, rank)
        self.setups, self.bad_setups = registry.load()
        self.ctx, self.notes = {}, []
        for s in self.u.indices:
            mins = context.minutes(self.base, s, self.u.under[s]["key"], before=day)
            c = context.day_context(mins, day)
            if c is None:
                self.notes.append(f"{s}: not enough history before {day}; not traded today")
                continue
            self.ctx[s] = (c, mins)
        lot_map = {s: self.u.lot(s) for s in self.u.under if self.u.lot(s)} if lots == "universe" else None
        self.engine = Engine(self.setups, LiveOptions(self.u), spread_pct=SPREAD_PCT, facts=self.facts, lots=lot_map)
        for s, (c, mins) in self.ctx.items():
            if warm_days:
                a, ema = context.warm(mins, s, day, warm_days, self.setups)
                if a:
                    self.engine.carry_atr[s] = a
                self.engine.carry_ema.update(ema)
        self.journal = Journal(journal_path)
        self.accounts = Accounts(self.journal, facts=self.facts)
        self.engine.listeners.append(self.accounts)
        self.alerts = None
        if sender is not None:
            from ..notify.alerts import Alerts
            self.alerts = Alerts(sender, self.accounts, evidence=evidence)
            self.engine.listeners.append(self.alerts)
        self.feed = FeedProcess(token, url=url, tape_dir=tape_dir, min_free_gb=min_free_gb)
        self.loop = LiveLoop(self.engine, self.u, {s: c for s, (c, _m) in self.ctx.items()}, day, out_dir=self.out,
                             feed=self.feed, clock=clock, grace_s=grace_s, facts=self.facts)
        self.feed.on_ticks = self.loop.on_ticks
        self._stop = threading.Event()
        self._timer: threading.Thread | None = None
        _log.info("session_ready", day=day, indices=self.u.indices, traded=self.loop.traded, stocks=len(self.u.stocks),
                  options=len(self.u.meta), setups=len(self.setups), notes=self.notes)

    def backfill(self, fetch, now: float) -> int:
        """Started after the open: feeds the session's minutes so far from broker 1-minute candles (fetch(key) ->
        [[iso_time, o, h, l, c, ...], ...]) so the day state is the same as if Tanu had been on since 09:15."""
        import datetime as dt
        bars = {}
        for s in self.loop.traded:
            try:
                rows = fetch(self.u.under[s]["key"])
            except Exception as e:
                self.notes.append(f"{s}: morning candles failed ({e}); day state starts now")
                continue
            bars[s] = {int(dt.datetime.fromisoformat(r[0]).timestamp()): tuple(map(float, r[1:5])) for r in rows}
        return self.loop.preload(bars, now)

    def start(self) -> dict:
        self.feed.start()
        subs = self.loop.subscribe_initial()
        if self.loop.clock == "wall":
            self._timer = threading.Thread(target=self._tick, daemon=True, name="tanu-minute-clock")
            self._timer.start()
        self.loop.write_status()
        _log.info("session_started", day=self.day, subscribed=subs)
        return subs

    def _tick(self) -> None:
        warned = False
        while not self._stop.is_set() and not self.loop.done:
            self.loop.step()
            silent = time.time() - (self.feed.last_msg or self.loop.started_at)
            if silent > 30 and not warned and self.loop.next_m > 0:
                warned = True
                _log.warning("feed_silent", seconds=round(silent))
                if self.alerts:
                    self.alerts.system(f"no prices for {round(silent)} s (feed {'up' if self.feed.connected else 'down'})")
            elif silent < 5:
                warned = False
            self._stop.wait(0.5)

    def wait_done(self, timeout: float) -> bool:
        end = time.time() + timeout
        while time.time() < end and not self.loop.done:
            time.sleep(0.2)
        return self.loop.done

    def stop(self) -> None:
        self._stop.set()
        self.feed.stop()
        if not self.loop.done:
            self.loop.write_status()
        _log.info("session_stopped", day=self.day, **self.loop.counts)
