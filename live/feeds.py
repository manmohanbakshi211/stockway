"""The feed router (gun, 2.52): Upstox stays the main live feed; Kite runs next to it as a backup and a cross-check.

Mandeep, 10 Nov 2026: "let's do it" (Kite, Dhan and Upstox, each at its best). The engine talks to this router exactly
as it talked to the Upstox feed (start / stop / subscribe / unsubscribe / counts / connected / msgs / last_msg ...).
Every subscription is mirrored to Kite (the same instruments, mapped by exchange token). Then:

- Cross-check (always on, shadow): every few seconds the latest Kite price of each instrument is compared with the
  app's price (from Upstox) at the same moment: matches, mismatches beyond the tolerance, and which feed is behind.
- Failover (rules/feeds.json): 'shadow' only records when it would have switched and for how long; 'on' feeds the
  app from Kite while Upstox is quiet in market hours, and goes back as soon as Upstox speaks again; 'off' never.

Kite is optional: no keys, no login or any Kite failure leaves Upstox exactly as before (a bad backup can never break
the main feed). Market data only: nothing here places, changes or cancels an order.
"""
from __future__ import annotations

import collections
import json
import threading
import time
from pathlib import Path

FILE = Path(__file__).resolve().parents[1] / "rules" / "feeds.json"
DEFAULTS = {"kite": {"enabled": True, "max_tokens": 2900, "mode": "full"}, "failover": "shadow", "failover_after_s": 8,
            "back_after_s": 3, "cross_check_every_s": 5, "same_moment_s": 2, "stale_s": 30,
            "tol_pct": {"index": 0.05, "future": 0.15, "stock": 0.15, "option": 2.0}, "index_map": {}}
_C: dict = {}


def cfg() -> dict:
    try:
        mt = FILE.stat().st_mtime
        if _C.get("mt") != mt:
            _C.update(mt=mt, cfg={**DEFAULTS, **json.loads(FILE.read_text())}, error=None)
    except Exception as e:
        _C.update(error=str(e), cfg=_C.get("cfg") or dict(DEFAULTS))
    return _C["cfg"]


class BackupNotReady(Exception):
    """The backup can't start yet (no keys, no login today): status 'off' or 'login_needed' and a plain message."""

    def __init__(self, status: str, msg: str):
        super().__init__(msg)
        self.status = status


def kind_of(key: str, by_key: dict | None) -> str:
    """index / future / stock / option, for the cross-check tolerance."""
    if key.startswith(("NSE_INDEX|", "BSE_INDEX|")):
        return "index"
    k = (by_key or {}).get(key)
    if k:
        return {"FUT": "future", "OPT": "option", "UND": "stock", "VIX": "index"}.get(k[0], "stock")
    return "stock" if key.startswith("NSE_EQ|") else "option"


class FeedRouter:
    def __init__(self, primary, on_ticks, log=print, backup_factory=None, state_get=None, is_open=None, by_key=None,
                 mode_fn=None):
        """primary: the Upstox feed (its on_ticks is taken over). backup_factory(on_ticks, on_status) -> (KiteFeed, map)
        or raises / returns None: called in its own thread so the main feed never waits for Kite."""
        self.primary = primary
        primary.on_ticks = self._on_primary
        self.on_ticks, self.log = on_ticks, log
        self.backup_factory, self.state_get = backup_factory, state_get
        self.is_open = is_open or (lambda: True)
        self.by_key = by_key or {}
        self.mode_fn = mode_fn                  # your setting (Live setup) wins over rules/feeds.json
        self.backup = None
        self.k2u: dict = {}                     # Kite token -> Upstox key
        self.u2k: dict = {}                     # Upstox key -> Kite token
        self.backup_status, self.backup_msg = "off", "Kite not set up"
        self.kite_last: dict = {}               # Upstox key -> (ltp, ltt_ms, received)
        self.primary_last = 0.0
        self.active = "upstox"
        self.switches = 0
        self.covered_s = 0.0
        self.would = collections.deque(maxlen=50)   # shadow: when Kite would have taken over, and for how long
        self._out_since = None
        self.cross: dict = {}
        self.stop_flag = threading.Event()
        self.lock = threading.Lock()

    # ------------------------------------------------ the feed interface the engine uses (delegated to Upstox)
    @property
    def connected(self):
        return self.primary.connected or (self.active == "kite" and self.backup is not None and self.backup.connected)

    @property
    def msgs(self):
        return self.primary.msgs

    @property
    def last_msg(self):
        b = self.backup.last_msg if (self.active == "kite" and self.backup is not None) else 0.0
        return max(self.primary.last_msg or 0.0, b)

    @property
    def lag_ms(self):
        return getattr(self.primary, "lag_ms", None)

    @property
    def market_status(self):
        return getattr(self.primary, "market_status", {})

    @property
    def subs(self):
        return self.primary.subs

    def counts(self) -> dict:
        return self.primary.counts()

    def start(self):
        self.primary.start()
        self.stop_flag.clear()
        threading.Thread(target=self._start_backup, daemon=True, name="kite-start").start()
        threading.Thread(target=self._watch, daemon=True, name="feed-router").start()

    def stop(self):
        self.stop_flag.set()
        self.primary.stop()
        if self.backup is not None:
            try:
                self.backup.stop()
            except Exception:
                pass

    def subscribe(self, keys, mode):
        self.primary.subscribe(keys, mode)
        self._mirror(keys, True)

    def unsubscribe(self, keys):
        self.primary.unsubscribe(keys)
        self._mirror(keys, False)

    # ------------------------------------------------ the backup
    def _start_backup(self):
        c = cfg()
        if not (c.get("kite") or {}).get("enabled", True) or self.backup_factory is None:
            self.backup_status, self.backup_msg = "off", "Kite switched off (rules/feeds.json)"
            return
        try:
            self.backup_status, self.backup_msg = "starting", "Reading Kite's instrument list…"
            got = self.backup_factory(self._on_backup, self._backup_status)
            if not got:
                if self.backup_status == "starting":
                    self.backup_status, self.backup_msg = "off", "Kite not set up"
                return
            feed, mapping = got
            with self.lock:
                self.u2k = dict(mapping)
                self.k2u = {v: k for k, v in mapping.items()}
            self.backup = feed
            feed.start()
            self._mirror(sorted(self._all_primary_keys()), True)
            self.backup_status, self.backup_msg = "running", f"{len(self.u2k):,} instruments matched to Kite"
        except BackupNotReady as e:
            self.backup, self.backup_status, self.backup_msg = None, e.status, str(e)
        except Exception as e:
            self.backup, self.backup_status, self.backup_msg = None, "error", f"Kite backup not started: {e}"[:200]
            self.log(self.backup_msg)

    def _backup_status(self, msg):
        self.log(msg)
        if "rejected" in msg:
            self.backup_status, self.backup_msg = "login_needed", "Kite login expired: log in with Kite again"

    def _all_primary_keys(self) -> set:
        out = set()
        for v in (getattr(self.primary, "subs", {}) or {}).values():
            out |= set(v)
        return out

    def _mirror(self, keys, add: bool):
        if self.backup is None:
            return
        toks = [self.u2k[k] for k in keys if k in self.u2k]
        if add:
            room = int((cfg().get("kite") or {}).get("max_tokens") or 2900) - len(self.backup.tokens)
            toks = [t for t in toks if t not in self.backup.tokens][:max(0, room)]
            self.backup.subscribe(toks)
        else:
            self.backup.unsubscribe(toks)

    # ------------------------------------------------ ticks
    def _on_primary(self, ticks):
        self.primary_last = time.time()
        if self.active == "kite":                       # Upstox is back
            if self._out_since:
                self.covered_s += time.time() - self._out_since
                self._out_since = None
            self.active = "upstox"
            self.log("Upstox feed is back: prices from Upstox again")
        self.on_ticks(ticks)

    def _on_backup(self, raw_ticks):
        from .kite import to_tick
        now = time.time()
        out = []
        for r in raw_ticks:
            k = self.k2u.get(r.get("token"))
            if not k or not r.get("ltp"):
                continue
            t = to_tick(r, k)
            self.kite_last[k] = (t["ltp"], t.get("ltt"), now)
            out.append(t)
        if out and self.active == "kite":
            self.on_ticks(out)

    # ------------------------------------------------ failover and the cross-check, every second
    def _watch(self):
        last_cross = 0.0
        while not self.stop_flag.wait(1.0):
            try:
                self.tick_failover(time.time())
            except Exception as e:
                self.log(f"Feed failover check: {e}")
            c = cfg()
            if time.time() - last_cross >= float(c.get("cross_check_every_s") or 5):
                last_cross = time.time()
                try:
                    self.cross_check()
                except Exception as e:
                    self.cross = {**self.cross, "error": str(e)[:160]}

    def mode(self) -> str:
        m = None
        try:
            m = self.mode_fn() if self.mode_fn else None
        except Exception:
            m = None
        m = m or cfg().get("failover", "shadow")
        return m if m in ("on", "off", "shadow") else "shadow"

    def tick_failover(self, now: float):
        c = cfg()
        mode = self.mode()
        if mode == "off":
            return
        kite_ok = self.backup is not None and self.backup.connected and now - (self.backup.last_msg or 0) < 5
        p_last = max(self.primary_last, getattr(self.primary, "last_msg", 0) or 0)
        quiet = (now - p_last) >= float(c.get("failover_after_s") or 8) if p_last else False
        if not self.is_open():
            quiet = False
        if quiet and kite_ok and self._out_since is None:
            self._out_since = now - float(c.get("failover_after_s") or 8)
            if mode == "on":
                self.active = "kite"
                self.switches += 1
                self.log("Upstox feed quiet: prices from Kite until it is back (backup)")
            else:
                self.would.appendleft({"at": self._out_since, "secs": None})
        elif self._out_since is not None and not quiet:
            secs = now - self._out_since
            if self.active == "kite":
                self.covered_s += secs
                self.active = "upstox"
            elif self.would and self.would[0]["secs"] is None:
                self.would[0]["secs"] = round(secs, 1)
            self._out_since = None

    def cross_check(self, now: float | None = None) -> dict:
        """Kite vs the app's price (Upstox) at the same moment, for every instrument both have."""
        c = cfg()
        now = now or time.time()
        tol, same, stale = c.get("tol_pct") or {}, float(c.get("same_moment_s") or 2), float(c.get("stale_s") or 30)
        n = match = mism = up_behind = kite_behind = 0
        examples = []
        if self.state_get is None:
            return self.cross
        for k, (kp, kt, krx) in list(self.kite_last.items()):
            i = self.state_get(k)
            if i is None or not getattr(i, "ltp", None) or not kt:
                continue
            ut = getattr(i, "ltt", None)
            if not ut:
                continue
            gap = (ut - kt) / 1000.0
            if gap <= -stale:
                up_behind += 1
                if len(examples) < 8:
                    examples.append({"key": k, "what": "Upstox behind", "secs": round(-gap)})
                continue
            if gap >= stale:
                kite_behind += 1
                continue
            if abs(gap) > same:
                continue
            n += 1
            d = abs(i.ltp - kp) / kp * 100 if kp else 0.0
            if d <= float(tol.get(kind_of(k, self.by_key), 0.15)):
                match += 1
            else:
                mism += 1
                if len(examples) < 8:
                    examples.append({"key": k, "what": "price differs", "upstox": i.ltp, "kite": kp, "pct": round(d, 2)})
        self.cross = {"at": now, "compared": n, "match": match, "mismatch": mism, "upstox_behind": up_behind,
                      "kite_behind": kite_behind, "match_pct": round(match / n * 100, 1) if n else None,
                      "examples": examples, "kite_keys": len(self.kite_last)}
        return self.cross

    # ------------------------------------------------ view
    def view(self) -> dict:
        b = self.backup
        c = cfg()
        return {"main": "Upstox", "active": "Kite" if self.active == "kite" else "Upstox", "failover": self.mode(),
                "backup": {"name": "Kite", "status": self.backup_status, "message": self.backup_msg,
                           "connected": bool(b and b.connected), "msgs": b.msgs if b else 0,
                           "last_msg_age": round(time.time() - b.last_msg, 1) if b and b.last_msg else None,
                           "instruments": len(b.tokens) if b else 0, "matched": len(self.u2k), "error": b.error if b else None},
                "switches": self.switches, "covered_s": round(self.covered_s, 1),
                "would_switch": [dict(x) for x in list(self.would)[:10]], "cross": self.cross, "file_error": _C.get("error")}
