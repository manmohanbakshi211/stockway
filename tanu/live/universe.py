"""The live universe: which instrument keys the feed watches, in which mode, re-centred on the money as prices move.

Built from the broker's instrument list (tanu.data.instruments) for one day:
- every F&O underlying (indices and stocks) with its lot, tick and expiries;
- per underlying, the nearest-expiry options (the contract an intraday buyer trades: expiry on or after the day);
- plan(spots): underlyings + `n` strikes each side of the money (CE and PE), per the spec in universe.json, fitted
  inside the per-mode limits. A symbol's centre moves only when the price has moved `recenter_after_strikes` strikes,
  so subscriptions do not churn every tick.
The spec is a bullet (dated JSON); this code knows nothing about any one symbol.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

from ..core import log

SPEC = Path(__file__).with_name("universe.json")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_log = log.get("universe")


@dataclass(frozen=True)
class Opt:
    key: str
    sym: str
    expiry: str
    strike: float
    side: str


def load_spec(path: Path | str = SPEC) -> dict:
    spec = json.loads(Path(path).read_text())
    spec.pop("about", None)
    return spec


class Universe:
    def __init__(self, rows: list[dict], day: str, spec: dict | None = None, rank: list[str] | None = None):
        """rows: instrument list rows (Upstox format). rank: stock symbols cheapest-to-trade first (the tradeable
        list); stocks not in it come after, alphabetically."""
        self.day, self.spec = day, spec or load_spec()
        self.under: dict[str, dict] = {}          # sym -> {key, is_index, lot, tick}
        self.by_key: dict[str, str] = {}          # underlying key -> sym
        self.meta: dict[str, Opt] = {}            # option key -> Opt (nearest expiry only)
        self.chain: dict[str, dict] = {}          # sym -> {"expiry", "strikes": sorted, "keys": {(strike, side): key}}
        exp: dict[str, set] = {}
        opts: list[dict] = []
        for r in rows:
            if r.get("segment") not in ("NSE_FO", "BSE_FO") or r.get("instrument_type") not in ("CE", "PE"):
                continue
            s = r.get("underlying_symbol") or r.get("name")
            if not s or not r.get("expiry") or not r.get("underlying_key"):
                continue
            e = dt.datetime.fromtimestamp(r["expiry"] / 1000, IST).date().isoformat()
            if e < day:
                continue
            if s not in self.under:
                self.under[s] = {"key": r["underlying_key"], "is_index": r.get("underlying_type") == "INDEX",
                                 "lot": int(r.get("lot_size") or 0), "tick": (r.get("tick_size") or 5) / 100}
                self.by_key[r["underlying_key"]] = s
            exp.setdefault(s, set()).add(e)
            opts.append({"s": s, "e": e, "k": r["instrument_key"], "strike": float(r["strike_price"]),
                         "side": r["instrument_type"]})
        near = {s: min(v) for s, v in exp.items()}
        for o in opts:
            if o["e"] != near[o["s"]]:
                continue
            c = self.chain.setdefault(o["s"], {"expiry": o["e"], "strikes": set(), "keys": {}})
            c["strikes"].add(o["strike"])
            c["keys"][(o["strike"], o["side"])] = o["k"]
            self.meta[o["k"]] = Opt(o["k"], o["s"], o["e"], o["strike"], o["side"])
        for c in self.chain.values():
            c["strikes"] = sorted(c["strikes"])
        self.indices = [s for s in self.spec.get("indices", []) if s in self.under]
        stocks = sorted(s for s, u in self.under.items() if not u["is_index"])
        if self.spec.get("stocks", "all") != "all":
            stocks = [s for s in stocks if s in set(self.spec["stocks"])]
        order = {s: i for i, s in enumerate(rank or [])}
        self.stocks = sorted(stocks, key=lambda s: (order.get(s, len(order)), s))
        self.centre: dict[str, float] = {}

    # ------------------------------------------------------------------ lookups
    def expiry(self, sym: str, day: str | None = None) -> str | None:
        c = self.chain.get(sym)
        return c["expiry"] if c else None

    def lot(self, sym: str) -> int | None:
        u = self.under.get(sym)
        return u["lot"] if u and u["lot"] else None

    def around(self, sym: str, spot: float, n: int) -> list[str]:
        """Option keys of the n strikes each side of the strike nearest to spot (CE and PE)."""
        c = self.chain.get(sym)
        if not c or not c["strikes"] or spot is None:
            return []
        ks = c["strikes"]
        i = bisect.bisect_left(ks, spot)
        if i == len(ks) or (i > 0 and spot - ks[i - 1] <= ks[i] - spot):
            i -= 1
        out = []
        for k in ks[max(0, i - n): i + n + 1]:
            for side in ("CE", "PE"):
                key = c["keys"].get((k, side))
                if key:
                    out.append(key)
        return out

    def _centre(self, sym: str, spot: float) -> float:
        """The price the strike window is built around; moves only after recenter_after_strikes strikes."""
        c = self.chain.get(sym)
        old = self.centre.get(sym)
        if old is None or not c or len(c["strikes"]) < 2:
            self.centre[sym] = spot
            return spot
        gap = min(b - a for a, b in zip(c["strikes"], c["strikes"][1:])) or 1
        if abs(spot - old) >= self.spec.get("recenter_after_strikes", 2) * gap:
            self.centre[sym] = spot
        return self.centre[sym]

    # ------------------------------------------------------------------ the plan
    def plan(self, spots: dict[str, float]) -> dict[str, list[str]]:
        """{mode: [keys]} inside the limits. spots: {sym: last price}; symbols without a price yet get only their
        underlying (their options follow as soon as a price arrives)."""
        m, lim = self.spec["modes"], self.spec["limits"]
        out: dict[str, list[str]] = {mode: [] for mode in lim}
        dropped = 0

        def put(mode, keys, optional=False):
            nonlocal dropped
            room = lim.get(mode, 0) - len(out[mode])
            if len(keys) > room:
                if not optional:
                    raise ValueError(f"universe does not fit: {mode} needs {len(out[mode]) + len(keys)} > {lim.get(mode)}")
                dropped += len(keys)
                return
            out[mode].extend(keys)

        for s in self.indices:
            put(m["index"], [self.under[s]["key"]])
        for s in self.stocks:
            put(m["stock"], [self.under[s]["key"]], optional=True)
        for s in self.indices:
            if spots.get(s):
                put(m["index_option"], self.around(s, self._centre(s, spots[s]), self.spec["index_strikes_each_side"]))
        for s in self.stocks:
            if spots.get(s):
                put(m["stock_option"], self.around(s, self._centre(s, spots[s]), self.spec["stock_strikes_each_side"]),
                    optional=True)
        if dropped:
            _log.warning("universe_trimmed", dropped_keys=dropped)
        return {k: v for k, v in out.items() if v}


def diff(old: dict[str, list[str]], new: dict[str, list[str]]) -> tuple[dict[str, list[str]], list[str]]:
    """(subscribe {mode: keys}, unsubscribe keys) to go from old to new (a key changing mode is just re-subscribed)."""
    om = {k: mode for mode, ks in old.items() for k in ks}
    nm = {k: mode for mode, ks in new.items() for k in ks}
    sub: dict[str, list[str]] = {}
    for k, mode in nm.items():
        if om.get(k) != mode:
            sub.setdefault(mode, []).append(k)
    return sub, sorted(k for k in om if k not in nm)
