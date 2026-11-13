"""Option prices for the live engine: the same interface as tanu.engine.options.OptionHistory (expiry / atm / bar),
fed with the live minute bars of the watched option keys, so the engine cannot tell live from replay (ADR-0004)."""
from __future__ import annotations

import collections


class LiveOptions:
    def __init__(self, universe):
        self.u = universe
        self.bars: dict[tuple[str, float, str, int], tuple] = {}             # (sym, strike, side, t) -> (o, h, l, c)
        self.by_t: dict[tuple[str, int, str], list[float]] = collections.defaultdict(list)

    def add_bar(self, key: str, t: int, o: float, h: float, l: float, c: float) -> bool:
        meta = self.u.meta.get(key)
        if meta is None:
            return False
        self.bars[(meta.sym, meta.strike, meta.side, int(t))] = (o, h, l, c)
        self.by_t[(meta.sym, int(t), meta.side)].append(meta.strike)
        return True

    def expiry(self, sym: str, day: str) -> str | None:
        return self.u.expiry(sym, day)

    def atm(self, sym: str, day: str, t: float, spot: float, side: str) -> float | None:
        strikes = self.by_t.get((sym, int(t), side))
        if not strikes:
            return None
        return min(strikes, key=lambda k: (abs(k - spot), k))

    def bar(self, sym: str, day: str, strike: float, side: str, t: float):
        return self.bars.get((sym, float(strike), side, int(t)))
