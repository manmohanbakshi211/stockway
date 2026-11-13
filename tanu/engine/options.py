"""Option prices for history replay, from the legacy expired-option files (read in place, never changed):
<history>/<SYM>/raw_<expiry>.parquet with columns ts, off, side, open, high, low, close, iv, volume, oi, strike, spot
(1-minute bars of the strikes around the money, from the previous expiry up to this one).

For a day the nearest expiry whose file holds that day is used (the contract an intraday buyer trades)."""
from __future__ import annotations

import collections
import datetime as dt
from pathlib import Path

import pandas as pd

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


class OptionHistory:
    def __init__(self, history_dir: Path | str, cache_files: int = 3):
        self.dir = Path(history_dir)
        self._files: dict[str, list[tuple[str, Path]]] = {}
        self._span: dict[Path, tuple[str, str]] = {}
        self._cache: collections.OrderedDict = collections.OrderedDict()
        self._day: dict = {}
        self.cache_files = cache_files

    def _list(self, sym: str) -> list[tuple[str, Path]]:
        if sym not in self._files:
            self._files[sym] = sorted((f.stem[4:], f) for f in (self.dir / sym).glob("raw_*.parquet"))
        return self._files[sym]

    def _span_of(self, f: Path) -> tuple[str, str]:
        if f not in self._span:
            ts = pd.read_parquet(f, columns=["ts"])["ts"]
            a, b = (dt.datetime.fromtimestamp(int(x), IST).date().isoformat() for x in (ts.min(), ts.max()))
            self._span[f] = (a, b)
        return self._span[f]

    def file_for(self, sym: str, day: str) -> Path | None:
        for exp, f in self._list(sym):
            if exp >= day:
                a, b = self._span_of(f)
                if a <= day <= b:
                    return f
        return None

    def expiry(self, sym: str, day: str) -> str | None:
        """The expiry date of the contract used on that day (the file's name), or None."""
        f = self.file_for(sym, day)
        return f.stem[4:] if f else None

    def days(self, sym: str) -> set[str]:
        out = set()
        for _, f in self._list(sym):
            ts = pd.read_parquet(f, columns=["ts"])["ts"].drop_duplicates()
            out |= {dt.datetime.fromtimestamp(int(x), IST).date().isoformat() for x in ts}
        return out

    def _load_day(self, sym: str, day: str):
        key = (sym, day)
        if key in self._day:
            return self._day[key]
        f = self.file_for(sym, day)
        if f is None:
            self._day[key] = None
            return None
        if f not in self._cache:
            self._cache[f] = pd.read_parquet(f, columns=["ts", "side", "open", "high", "low", "close", "strike"])
            while len(self._cache) > self.cache_files:
                self._cache.popitem(last=False)
        df = self._cache[f]
        a = dt.datetime.fromisoformat(day + "T00:00:00+05:30").timestamp()
        d = df[(df["ts"] >= a) & (df["ts"] < a + 86400)]
        bars = {(float(r.strike), r.side, int(r.ts)): (r.open, r.high, r.low, r.close) for r in d.itertuples(index=False)}
        by_t = collections.defaultdict(list)
        for (k, sd, t) in bars:
            by_t[(t, sd)].append(k)
        self._day = {k: v for k, v in self._day.items() if k[0] != sym}      # keep one day per symbol in memory
        self._day[key] = (bars, by_t)
        return self._day[key]

    def atm(self, sym: str, day: str, t: float, spot: float, side: str) -> float | None:
        d = self._load_day(sym, day)
        if d is None:
            return None
        strikes = d[1].get((int(t), side))
        if not strikes:
            return None
        return min(strikes, key=lambda k: (abs(k - spot), k))

    def bar(self, sym: str, day: str, strike: float, side: str, t: float):
        d = self._load_day(sym, day)
        return None if d is None else d[0].get((float(strike), side, int(t)))
