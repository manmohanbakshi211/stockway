"""Data quality guard (gun): bad ticks, crossed quotes and stale prices are caught before they become alerts or test fills.

Every few seconds (rules/dataguard.json, bullets) it looks at every live instrument:
- a jump bigger than the kind's jump_pct since the last check makes the price suspect; if it snaps back within
  confirm_s it was a bad print (recorded, never used), if it stays it was a real move (released);
- a crossed quote (bid above ask) is suspect while it lasts;
- a liquid instrument (index, stock, future) with no tick for stale_s seconds in market hours is stale.
The engine then holds alerts on a symbol whose own price is suspect or stale (mode "hold"), the test bench never opens
on a stale or suspect leg and never exits on a suspect price, and every finding goes into the day's recording (part
"quality") so backtests can leave bad ticks out. Read-only on the market data; nothing here talks to a broker.
A second feed (Kite, later) adds a cross-check as one more rule here.
"""
from __future__ import annotations

import collections
import json
import time
from pathlib import Path

from .clock import is_market_hours

FILE = Path(__file__).resolve().parents[1] / "rules" / "dataguard.json"
DEFAULTS = {"every_s": 5, "jump_pct": {"index": 1.5, "stock": 6.0, "future": 6.0, "option": 45.0}, "option_min_price": 3.0,
            "confirm_s": 10, "stale_s": {"index": 90, "future": 120, "stock": 300}, "fresh_s_bench": 180, "mode": "hold",
            "stale_share_alert": 0.3}
_C: dict = {}


def cfg() -> dict:
    try:
        mt = FILE.stat().st_mtime
        if _C.get("mt") != mt:
            raw = json.loads(FILE.read_text())
            _C.update(mt=mt, cfg={**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}, error=None)
        return _C["cfg"]
    except Exception as e:
        _C.update(cfg=dict(DEFAULTS), error=str(e)[:160])
        return _C["cfg"]


class DataGuard:
    def __init__(self, engine):
        self.e = engine
        self.prev: dict = {}                       # key -> last checked ltp
        self.suspect: dict = {}                    # key -> {"why", "since", "from", "to"}
        self.stale: dict = {}                      # key -> since
        self.kinds: dict = {}                      # key -> (kind, symbol)
        self.events = collections.deque(maxlen=200)
        self.held = collections.deque(maxlen=60)
        self.counts = collections.Counter()
        self.last_run = 0.0
        self.state: dict = {}

    # ------------------------------------------------------------ what is what
    def _map(self):
        uni = getattr(self.e, "uni", None)
        if not uni or self.kinds:
            return
        from .universe import VIX_KEY
        for s, u in uni.underlyings.items():
            if getattr(u, "key", None):
                self.kinds[u.key] = ("index" if u.is_index else "stock", s)
            if getattr(u, "fut_key", None):
                self.kinds[u.fut_key] = ("future", s)
        self.kinds[VIX_KEY] = ("index", "VIX")

    def kind(self, key) -> str:
        return (self.kinds.get(key) or ("option", None))[0]

    # ------------------------------------------------------------ the check
    def check(self, now=None, force=False):
        c = cfg()
        real = time.time()
        if not force and real - self.last_run < float(c["every_s"]):
            return
        self.last_run = real
        t = self._now()                          # the feed's clock (the simulator runs its own)
        st = getattr(self.e, "state", None)
        if st is None:
            return
        self._map()
        clock_now = now or (self.e.clock.now() if getattr(self.e, "clock", None) else None)
        live = bool(clock_now and is_market_hours(clock_now)) or getattr(self.e, "source", None) == "sim"
        with st.lock:
            snap = [(i.key, i.ltp, i.bid, i.ask, i.t) for i in st.insts.values() if i.ltp]
        jp, rows = c["jump_pct"], []
        n_liquid = n_stale = 0
        for key, ltp, bid, ask, last_t in snap:
            k = self.kind(key)
            sym = (self.kinds.get(key) or (None, None))[1]
            # crossed quote
            crossed = bool(bid and ask and bid > ask > 0)
            s = self.suspect.get(key)
            if crossed and not s:
                self.suspect[key] = {"why": "crossed quote", "since": t, "from": bid, "to": ask}
                rows.append(self._event(t, key, sym, k, "crossed quote", bid, ask))
            elif not crossed and s and s["why"] == "crossed quote":
                self.suspect.pop(key, None)
            # jumps: suspect, then a bad print (snaps back) or a real move (stays)
            p = self.prev.get(key)
            thr = float(jp.get(k, 45.0)) / 100
            if k == "option" and (ltp < float(c["option_min_price"]) or (p or 0) < float(c["option_min_price"])):
                thr = None                        # cheap options jump in % all the time
            s = self.suspect.get(key)
            if s and s["why"] == "jump":
                base = s["from"]
                if thr and base and abs(ltp / base - 1) < thr / 2:
                    self.suspect.pop(key, None)
                    self.counts["bad_prints"] += 1
                    rows.append(self._event(t, key, sym, k, "bad print (snapped back)", base, s["to"]))
                elif t - s["since"] >= float(c["confirm_s"]) * self._speed():
                    self.suspect.pop(key, None)
                    self.counts["real_moves"] += 1
                    rows.append(self._event(t, key, sym, k, "big move confirmed", base, ltp))
            elif thr and p and abs(ltp / p - 1) > thr and live:
                self.suspect[key] = {"why": "jump", "since": t, "from": p, "to": ltp}
                self.counts["jumps"] += 1
                rows.append(self._event(t, key, sym, k, "sudden jump (on hold)", p, ltp))
            if not (key in self.suspect and self.suspect[key]["why"] == "jump"):
                self.prev[key] = ltp                 # a held jump is compared with the price before it
            # stale liquid instruments
            if k in ("index", "stock", "future") and live:
                n_liquid += 1
                age = t - (last_t or t)
                if age > float((c["stale_s"] or {}).get(k, 300)) * self._speed():
                    n_stale += 1
                    if key not in self.stale:
                        self.stale[key] = t
                        self.counts["stale"] += 1
                        rows.append(self._event(t, key, sym, k, f"no tick for {int(age)} s", None, None))
                elif key in self.stale:
                    self.stale.pop(key, None)
        share = n_stale / n_liquid if n_liquid else 0.0
        self.state.update(liquid=n_liquid, stale=n_stale, stale_share=round(share, 3), suspect=len(self.suspect),
                          checked=len(snap), at=t)
        if live and n_liquid >= 10 and share >= float(c["stale_share_alert"]):
            h = getattr(self.e, "health", None)
            if h is not None:
                h.notify("stale_share", f"⚠️ Stockway: {n_stale} of {n_liquid} indices / stocks / futures have had no price "
                         "update for minutes. The live feed may be partly stuck: check the System page.")
        rec = getattr(self.e, "recorder", None)
        if rows and rec is not None:
            try:
                rec.quality(rows)
            except Exception:
                pass

    def _event(self, t, key, sym, kind, what, a, b):
        self.events.appendleft({"ts": t, "key": key, "sym": sym, "kind": kind, "what": what, "a": a, "b": b})
        return (int(t), key, sym, kind, what, a, b)

    # ------------------------------------------------------------ for the engine and the bench
    def is_bad(self, key) -> bool:
        return key in self.suspect

    def _speed(self) -> float:
        """The simulator runs its clock faster: time limits are in real seconds, so they scale with it."""
        return float(getattr(getattr(self.e, "clock", None), "speed", 1.0) or 1.0)

    def _now(self) -> float:
        clk = getattr(self.e, "clock", None)
        try:
            return float(clk.ts()) if clk is not None else time.time()
        except Exception:
            return time.time()

    def fresh(self, key, max_age: float | None = None) -> bool:
        st = getattr(self.e, "state", None)
        i = st.get(key) if st is not None else None
        if not i or not i.t:
            return False
        age = float(max_age if max_age is not None else cfg()["fresh_s_bench"]) * self._speed()
        return self._now() - i.t <= age and key not in self.suspect

    def symbol_problem(self, sym) -> str | None:
        """Why a symbol's own price can't be trusted right now (its spot or future is suspect or stale), or None."""
        uni = getattr(self.e, "uni", None)
        u = uni.underlyings.get(sym) if uni else None
        if not u:
            return None
        keys = [u.key] + ([u.fut_key] if u.fut_key else [])
        st = getattr(self.e, "state", None)
        live_keys = [k for k in keys if st is not None and st.get(k) and st.get(k).ltp]
        for k in live_keys:
            s = self.suspect.get(k)
            if s:
                return f"{s['why']} on {'the future' if k == u.fut_key else 'the price'} ({s['from']:g} → {s['to']:g})"
        if live_keys and all(k in self.stale for k in live_keys):
            return "no live price update for minutes"
        return None

    def hold(self, a: dict) -> bool:
        """True when an alert should wait because its symbol's data is suspect (mode "hold"); "warn" only marks it."""
        why = self.symbol_problem(a.get("sym"))
        if not why:
            return False
        self.counts["alerts_held" if cfg()["mode"] == "hold" else "alerts_marked"] += 1
        if cfg()["mode"] != "hold":
            a.setdefault("data", {})["data_check"] = why
            return False
        self.held.appendleft({"ts": self._now(), "sym": a.get("sym"), "kind": a.get("kind"), "title": a.get("title"), "why": why})
        return True

    def view(self) -> dict:
        now = self._now()
        return {"state": dict(self.state), "counts": dict(self.counts), "mode": cfg()["mode"], "error": _C.get("error"),
                "suspect": [{"key": k, "sym": (self.kinds.get(k) or (None, None))[1], "kind": self.kind(k), **v,
                             "secs": round(now - v["since"])} for k, v in list(self.suspect.items())[:15]],
                "stale": [{"key": k, "sym": (self.kinds.get(k) or (None, None))[1], "kind": self.kind(k), "secs": round(now - v)}
                          for k, v in list(self.stale.items())[:15]],
                "events": list(self.events)[:15], "held": list(self.held)[:15]}
