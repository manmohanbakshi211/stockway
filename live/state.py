"""In-memory market state, updated by the feed thread, read by the engine and the API."""
from __future__ import annotations

import collections
import threading
import time


class Inst:
    __slots__ = ("key", "ltp", "cp", "vol", "oi", "iv", "atp", "bid", "ask", "bidq", "askq", "tbq", "tsq",
                 "delta", "gamma", "theta", "vega", "day_o", "day_h", "day_l", "base_oi", "prev_oi", "ltq",
                 "t", "hist", "ticks", "cvd", "flow", "_side", "_pv", "_pbid", "_pask", "_pltp")

    def __init__(self, key):
        self.key = key
        for s in self.__slots__[1:]:
            setattr(self, s, None)
        self.hist = collections.deque(maxlen=46)   # one (minute, ltp, vol, oi, iv) per minute
        self.ticks = 0
        self.cvd = 0.0                              # cumulative buy minus sell volume (order flow)
        self.flow = collections.deque(maxlen=30)    # [minute, buy_vol, sell_vol, trades] per minute
        self._side = 0

    def as_dict(self):
        return {s: getattr(self, s) for s in self.__slots__ if s not in ("hist", "flow") and not s.startswith("_")}

    def flow_window(self, minutes: int, now_minute: int):
        b = s = 0.0
        n = 0
        for m, bv, sv, k in self.flow:
            if m > now_minute - minutes * 60:
                b += bv
                s += sv
                n += k
        return b, s, n

    def ago(self, minutes: int):
        """Snapshot from about `minutes` ago (or the oldest available)."""
        if not self.hist:
            return None
        idx = max(0, len(self.hist) - 1 - minutes)
        return self.hist[idx]


class Bars:
    """1-minute OHLCV bars built from ticks of one underlying."""

    def __init__(self, maxlen=420):
        self.bars = collections.deque(maxlen=maxlen)   # [minute_epoch, o, h, l, c, vol_in_bar]
        self.last_cum_vol = None

    def update(self, minute: int, px: float, cum_vol):
        dv = 0
        if cum_vol is not None:
            if self.last_cum_vol is not None and cum_vol >= self.last_cum_vol:
                dv = cum_vol - self.last_cum_vol
            self.last_cum_vol = cum_vol
        if self.bars and self.bars[-1][0] == minute:
            b = self.bars[-1]
            b[2] = max(b[2], px)
            b[3] = min(b[3], px)
            b[4] = px
            b[5] += dv
        else:
            self.bars.append([minute, px, px, px, px, dv])

    def closes(self, n=None):
        xs = [b[4] for b in self.bars]
        return xs[-n:] if n else xs


class MarketState:
    def __init__(self):
        self.lock = threading.RLock()
        self.insts: dict[str, Inst] = {}
        self.bars: dict[str, Bars] = {}       # underlying symbol -> bars
        self.und_keys: dict[str, str] = {}    # underlying instrument key -> symbol (for bar building)
        self.tick_count = 0
        self.last_tick = 0.0
        self.clock = None
        self.flow_keys: set = set()           # instruments whose trades we classify as buyer/seller initiated
        self.prints = collections.deque(maxlen=300)   # big single trades: (ts, key, qty, price, side)
        self.print_min_value = 5e7            # rupees for a "big print" (5 crore)

    def inst(self, key) -> Inst:
        i = self.insts.get(key)
        if i is None:
            i = self.insts[key] = Inst(key)
        return i

    def get(self, key) -> Inst | None:
        return self.insts.get(key)

    def apply(self, ticks: list, now_ts: float | None = None):
        now_ts = now_ts or time.time()
        minute = int(now_ts // 60) * 60
        with self.lock:
            for t in ticks:
                i = self.inst(t["key"])
                if t["key"] in self.flow_keys:
                    self._classify(i, t, minute, now_ts)
                for k, v in t.items():
                    if k == "key" or v is None:
                        continue
                    if k in ("ltp", "cp", "atp", "bid", "ask") and v == 0:
                        continue
                    setattr(i, k, v) if k in Inst.__slots__ else None
                if i.oi and i.base_oi is None:
                    i.base_oi = i.oi
                if i.ltp:
                    i.day_h = i.ltp if i.day_h is None else max(i.day_h, i.ltp)
                    i.day_l = i.ltp if i.day_l is None else min(i.day_l, i.ltp)
                    if i.day_o is None:
                        i.day_o = i.ltp
                i.t = now_ts
                i.ticks += 1
                sym = self.und_keys.get(t["key"])
                if sym and i.ltp:
                    b = self.bars.get(sym)
                    if b is None:
                        b = self.bars[sym] = Bars()
                    b.update(minute, i.ltp, i.vol)
            self.tick_count += len(ticks)
            self.last_tick = now_ts

    def _classify(self, i: Inst, t: dict, minute: int, now_ts: float):
        """Lee-Ready style: trade at/above the previous ask = buyer-initiated, at/below bid = seller, else tick rule."""
        px, vol = t.get("ltp"), t.get("vol")
        if not px or vol is None:
            return
        pv = i._pv
        i._pv = vol
        if pv is None or vol <= pv:
            i._pbid, i._pask, i._pltp = t.get("bid") or i._pbid, t.get("ask") or i._pask, px
            return
        dv = vol - pv
        side = 0
        if i._pask and px >= i._pask:
            side = 1
        elif i._pbid and px <= i._pbid:
            side = -1
        elif i._pltp:
            side = 1 if px > i._pltp else -1 if px < i._pltp else i._side
        i._side = side or i._side
        side = side or i._side
        if side:
            i.cvd += side * dv
            if not i.flow or i.flow[-1][0] != minute:
                i.flow.append([minute, 0.0, 0.0, 0])
            i.flow[-1][1 if side > 0 else 2] += dv
            i.flow[-1][3] += 1
        ltq = t.get("ltq")
        if ltq and px * ltq >= self.print_min_value:
            self.prints.append((now_ts, t["key"], ltq, px, side))
        i._pbid, i._pask, i._pltp = t.get("bid") or i._pbid, t.get("ask") or i._pask, px

    def snapshot_minute(self, minute_ts: int):
        with self.lock:
            for i in self.insts.values():
                if i.ltp is None:
                    continue
                if i.hist and i.hist[-1][0] == minute_ts:
                    continue
                i.hist.append((minute_ts, i.ltp, i.vol, i.oi, i.iv))

    def set_baselines(self, base: dict):
        """base: key -> {"prev_oi":..., "cp":...} (from REST quotes at startup)."""
        with self.lock:
            for k, v in base.items():
                i = self.inst(k)
                if v.get("prev_oi"):
                    i.prev_oi = v["prev_oi"]
                if v.get("cp") and not i.cp:
                    i.cp = v["cp"]
