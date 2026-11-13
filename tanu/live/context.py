"""What the live engine needs to know before the day starts, computed exactly as replay does (point-in-time):
- minutes(sym): the symbol's 1-minute history from every source Tanu has (imported history, legacy bars, Tanu's own
  tape bars, broker-filled bars), one row per minute, newest source first;
- day_context(...): the DayContext replay would build for that day (only earlier days used);
- warm(...): what the engine carries into the day (ATR, higher-timeframe EMAs), from the last few days, as a
  multi-day replay would."""
from __future__ import annotations

import datetime as dt
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from ..engine import replay
from ..engine.core import Engine
from ..state.day import N_MIN, build_contexts

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def minutes(base: Path | str, sym: str, key: str | None = None, before: str | None = None) -> pd.DataFrame:
    """Columns t, o, h, l, c (one row per minute) for days before `before` (ISO date, exclusive)."""
    base = Path(base)
    parts = []
    for pri, folder, k in ((0, base / "bars" / "upstox", key), (1, base / "bars" / "legacy", sym)):
        if not folder.exists() or not k:
            continue
        for f in sorted(folder.glob("*.parquet")):
            if before and f.stem >= before:
                continue
            d = pd.read_parquet(f, columns=["key", "t", "o", "h", "l", "c"])
            d = d[d["key"] == k]
            if len(d):
                parts.append(d.drop(columns="key").assign(_p=pri))
    h = base / "history" / f"{sym}.parquet"
    if h.exists():
        parts.append(pd.read_parquet(h, columns=["t", "o", "h", "l", "c"]).assign(_p=2))
    if not parts:
        return pd.DataFrame(columns=["t", "o", "h", "l", "c"])
    df = pd.concat(parts, ignore_index=True)
    if before:
        df = df[df["t"] < dt.datetime.fromisoformat(before + "T00:00:00+05:30").timestamp()]
    df = df.sort_values(["t", "_p"]).drop_duplicates("t", keep="first")
    return df.drop(columns="_p").reset_index(drop=True)


def day_context(mins: pd.DataFrame, day: str):
    """The DayContext of `day` from the minutes before it, or None when there are too few earlier days."""
    days, _T, _O, H, L, C = replay.day_arrays(mins)
    days = [d for d in days if d < day]
    n = len(days)
    if n == 0:
        return None
    nan = np.full((1, N_MIN), np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        ctx = build_contexts(days + [day], np.vstack([H[:n], nan]), np.vstack([L[:n], nan]), np.vstack([C[:n], nan]))
    return ctx.get(day)


class _NoOptions:
    """No option prices: signals during the warm-up are skipped, only the day state matters."""

    def atm(self, *a):
        return None

    def bar(self, *a):
        return None


def warm(mins: pd.DataFrame, sym: str, day: str, days: int = 2, setups=()) -> tuple[float | None, dict]:
    """Replays the last `days` days before `day` (no trades) and returns what the engine carries into `day`:
    (ATR, {(sym, tf, n): EMA} for the timeframes the setups use)."""
    all_days, T, O, H, L, C = replay.day_arrays(mins)
    idx = [i for i, d in enumerate(all_days) if d < day][-days:]
    if not idx:
        return None, {}
    ctxs = build_contexts(all_days, H, L, C)
    eng = Engine(list(setups), _NoOptions())
    for i in idx:
        d = all_days[i]
        if d not in ctxs:
            continue
        eng.start_day(sym, d, ctxs[d])
        for k in range(N_MIN):
            eng.on_bar(sym, float(T[i, k]), O[i, k], H[i, k], L[i, k], C[i, k])
        eng.end_day(sym)
    return eng.carry_atr.get(sym), dict(eng.carry_ema)


def warm_atr(mins: pd.DataFrame, sym: str, day: str, days: int = 2) -> float | None:
    return warm(mins, sym, day, days)[0]
