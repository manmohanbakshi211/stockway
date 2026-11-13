"""The live loop: ticks in, one engine step per closed minute, exactly as replay feeds bars (ADR-0004).

- on_ticks(ticks) (called from the feed's receiving thread) adds ticks to the minute bars and keeps last prices;
- step(now) closes every session minute whose end + grace has passed: option bars go to the option book first, then
  each traded index gets engine.on_bar(...) for that minute (a minute with no trade repeats the last close, as replay
  carries prices forward); then the chain snapshot, the subscription re-centre and the status file;
- the clock is the wall clock live, or the exchange time of the ticks in a rehearsal (so a past day can be played
  at any speed with the same result).
Paper only: the engine's listeners (accounts, journal, alerts) never reach a broker (ADR-0001)."""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
from pathlib import Path

import pandas as pd

from ..core import log
from ..data.facts import Facts
from ..state.day import N_MIN
from . import universe as U
from .bars import MinuteBars

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_log = log.get("live")


class LiveLoop:
    def __init__(self, engine, universe, contexts: dict, day: str, *, out_dir: Path | str, feed=None,
                 clock: str = "wall", grace_s: float = 2.0, facts: Facts | None = None, chain_every: int = 1,
                 now=time.time, detect=None):
        self.eng, self.u, self.day, self.feed = engine, universe, day, feed
        self.detect = detect                              # display-only detectors (ADR-0012); never trade
        self.opts = engine.opt
        self.clock, self.grace, self.now = clock, grace_s, now
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.facts = facts or Facts()
        sess = self.facts.session(day)
        if sess is None:
            raise ValueError(f"{day} is not a trading day")
        self.t0 = int(sess[0].timestamp())
        self.n_min = min(N_MIN, int((sess[1].timestamp() - self.t0) // 60))
        self.traded = [s for s in universe.indices if s in contexts and any(s in st.symbols for st in engine.setups)]
        self.ctx = contexts
        self.bars = MinuteBars()
        self.lock = threading.RLock()
        self.next_m = 0                                   # next session minute to close (0 = 09:15)
        self.ex_time = 0.0
        self.last_c: dict[str, float] = {}
        self.spots: dict[str, float] = {}
        self.subs: dict[str, list[str]] = {}
        self.started: set = set()
        self.done = False
        self.chain_rows: list[dict] = []
        self.chain_every = max(1, chain_every)
        self.counts = {"ticks": 0, "msgs": 0, "minutes": 0, "late": 0, "bars": 0}
        self.started_at = now()
        self._chain_part = 0

    # ------------------------------------------------------------------ ticks
    def on_ticks(self, ticks: list[dict]) -> None:
        with self.lock:
            t_recv = self.now()
            latest = self.bars.add(ticks, t_recv)
            self.counts["msgs"] += 1
            self.counts["ticks"] += len(ticks)
            first = False
            for tk in ticks:
                s = self.u.by_key.get(tk["key"])
                if s and tk.get("ltp"):
                    first = first or s not in self.spots
                    self.spots[s] = tk["ltp"]
            self.ex_time = max(self.ex_time, latest)
            if first:                                     # a symbol's first price: watch its options right away
                self._recentre()
            if self.clock == "exchange":
                self.step()

    # ------------------------------------------------------------------ minutes
    def step(self, now: float | None = None) -> int:
        """Closes every minute that is due; returns how many were closed."""
        with self.lock:
            clock = self.ex_time if self.clock == "exchange" else (self.now() if now is None else now)
            n = 0
            while not self.done and self.next_m < self.n_min and clock >= self.t0 + (self.next_m + 1) * 60 + self.grace:
                self._close_minute(self.next_m)
                n += 1
            return n

    def preload(self, und: dict[str, dict[int, tuple]], now: float) -> int:
        """Feeds the session minutes that ended before `now` from underlying bars {sym: {t: (o, h, l, c)}} (a start
        after the open). Ticks of those minutes that arrive later count as late."""
        with self.lock:
            n = 0
            while self.next_m < self.n_min and now >= self.t0 + (self.next_m + 1) * 60:
                t = self.t0 + self.next_m * 60
                for s in self.traded:
                    b = und.get(s, {}).get(t)
                    if b is None and s not in self.last_c:
                        continue
                    o, h, l, c = b if b is not None else (self.last_c[s],) * 4
                    if s not in self.started:
                        self.eng.start_day(s, self.day, self.ctx[s])
                        self.started.add(s)
                    self.eng.on_bar(s, float(t), o, h, l, c)
                    self.last_c[s] = c
                    self.spots[s] = c
                self.next_m += 1
                n += 1
            self.bars.closed_upto = max(self.bars.closed_upto, self.t0 + self.next_m * 60)
            self.counts["minutes"] = self.next_m
            self.counts["preloaded"] = n
            return n

    def finish(self) -> None:
        """Closes whatever minutes are left (end of a rehearsal, or after the close if ticks stopped)."""
        with self.lock:
            while not self.done and self.next_m < self.n_min:
                self._close_minute(self.next_m)

    def _close_minute(self, m: int) -> None:
        t = self.t0 + m * 60
        rows = self.bars.close(t + 60)
        self.counts["late"] = self.bars.late
        und = {}
        for r in rows:
            if r["t"] < self.t0:                          # pre-open ticks: kept on the tape, not in session bars
                continue
            self.counts["bars"] += 1
            if self.opts.add_bar(r["key"], r["t"], r["o"], r["h"], r["l"], r["c"]):
                continue
            s = self.u.by_key.get(r["key"])
            if s and r["t"] == t:
                und[s] = r
        for s in self.traded:
            r = und.get(s)
            if r is not None:
                o, h, l, c = r["o"], r["h"], r["l"], r["c"]
            elif s in self.last_c:
                o = h = l = c = self.last_c[s]
            else:
                continue                                  # no price yet today
            if s not in self.started:
                self.eng.start_day(s, self.day, self.ctx[s])
                self.started.add(s)
            self.eng.on_bar(s, float(t), o, h, l, c)
            self.last_c[s] = c
        if self.detect is not None:
            try:
                self.detect.on_minute(t, rows, self.bars.last)
            except Exception as e:                        # a detector can never stop the live day
                self.counts["detect_errors"] = self.counts.get("detect_errors", 0) + 1
                _log.warning("detect_minute_failed", error=str(e)[:200])
        if m % self.chain_every == 0:
            self._snapshot(t + 60)
        self.next_m = m + 1
        self.counts["minutes"] = self.next_m
        if self.next_m >= self.n_min:
            for s in list(self.started):
                self.eng.end_day(s)
            self.done = True
            self._flush_chain(final=True)
            _log.info("live_day_done", day=self.day, **self.counts)
        else:
            self._recentre()
            if self.next_m % 30 == 0:
                self._flush_chain()
        self.write_status()

    # ------------------------------------------------------------------ chain snapshots (for measured costs)
    def _snapshot(self, ts: int) -> None:
        for key, meta in self.u.meta.items():
            q = self.bars.last.get(key)
            if not q or q.get("bid") is None:
                continue
            self.chain_rows.append({"sym": meta.sym, "ts": ts, "expiry": meta.expiry, "side": meta.side,
                                    "strike": meta.strike, "bid": q.get("bid"), "ask": q.get("ask"), "ltp": q.get("ltp"),
                                    "oi": q.get("oi"), "iv": q.get("iv"), "spot": self.spots.get(meta.sym)})

    def _flush_chain(self, final: bool = False) -> None:
        d = self.out / "chains" / self.day
        if self.chain_rows:
            d.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(self.chain_rows).to_parquet(d / f"part{self._chain_part:03d}.parquet", index=False)
            self._chain_part += 1
            self.chain_rows = []
        if final and d.exists():
            parts = sorted(d.glob("part*.parquet"))
            if parts:
                out = self.out / "chains" / f"{self.day}.parquet"
                pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True).to_parquet(out, compression="zstd", index=False)

    # ------------------------------------------------------------------ subscriptions
    def plan(self) -> dict[str, list[str]]:
        return self.u.plan(self.spots)

    def _recentre(self) -> None:
        if self.feed is None:
            return
        new = self.plan()
        sub, unsub = U.diff(self.subs, new)
        if not sub and not unsub:
            return
        if unsub:
            self.feed.unsubscribe(unsub)
        for mode, keys in sub.items():
            self.feed.subscribe(keys, mode)
        self.subs = new
        _log.info("subscriptions_changed", added=sum(len(v) for v in sub.values()), removed=len(unsub))

    def subscribe_initial(self) -> dict:
        with self.lock:
            self.subs = self.plan()
            for mode, keys in self.subs.items():
                self.feed.subscribe(keys, mode)
            return {m: len(k) for m, k in self.subs.items()}

    # ------------------------------------------------------------------ status (read by the app)
    def status(self) -> dict:
        e = self.eng
        status_of = {s.id: s.status for s in e.setups}
        sig = [{"setup": s["setup"], "status": status_of.get(s["setup"]), "sym": s["sym"], "d": s["d"], "t": s["t"],
                "spot": s["spot"], "day_type": s.get("day"), "skipped": s.get("skipped")} for s in e.signals]
        opens = [{"setup": p.setup, "sym": p.sym, "d": p.d, "strike": p.strike, "side": p.side, "entry_t": p.entry_t,
                  "entry": p.entry, "stop": p.stop, "last": (self.opts.bar(p.sym, p.day, p.strike, p.side,
                                                                           self.t0 + (self.next_m - 1) * 60) or [None] * 4)[3]}
                 for p in e.open]
        feed = None
        if self.feed is not None:
            f = self.feed
            feed = {"connected": bool(getattr(f, "connected", False)), "msgs": getattr(f, "msgs", 0),
                    "last_msg_ago_s": round(self.now() - f.last_msg, 1) if getattr(f, "last_msg", 0) else None,
                    "lag_ms": round(f.lag_ms) if getattr(f, "lag_ms", None) is not None else None,
                    "subscribed": f.counts() if hasattr(f, "counts") else {}}
        return {"day": self.day, "at": dt.datetime.fromtimestamp(self.now(), IST).isoformat(timespec="seconds"),
                "clock": self.clock, "minute": self.next_m, "of": self.n_min, "done": self.done,
                "traded": self.traded, "spots": {s: self.spots.get(s) for s in self.u.indices}, "counts": self.counts,
                "feed": feed, "signals": sig[-50:], "open": opens, "closed_today": len(e.closed),
                "detect": ({**getattr(self.detect, "counts", {}), "failed": getattr(self.detect, "failed", {})}
                           if self.detect is not None else None)}

    def write_status(self) -> None:
        p = self.out / "status.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.status(), default=str))
        tmp.replace(p)
