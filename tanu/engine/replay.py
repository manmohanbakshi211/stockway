"""History replay: feed past 1-minute underlying bars through the engine, day by day, exactly as live would."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

from ..core import log
from ..state.day import N_MIN, build_contexts
from .core import Engine

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_log = log.get("replay")


def day_arrays(minutes: pd.DataFrame, min_bars: int = 300):
    """minutes: columns t (epoch, minute start), o, h, l, c. Returns (days, T, O, H, L, C) with arrays [n_days, 375];
    missing minutes are carried forward (flagged in counts)."""
    t = pd.to_datetime(minutes["t"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    df = minutes.assign(day=t.dt.date.astype(str), m=(t.dt.hour * 60 + t.dt.minute - 555).astype(int))
    df = df[(df["m"] >= 0) & (df["m"] < N_MIN)]
    days, T, O, H, L, C = [], [], [], [], [], []
    for d, g in df.groupby("day", sort=True):
        if len(g) < min_bars:
            continue
        idx = g["m"].to_numpy()
        base = dt.datetime.fromisoformat(d + "T09:15:00+05:30").timestamp()
        arrs = []
        for col in ("o", "h", "l", "c"):
            a = np.full(N_MIN, np.nan)
            a[idx] = g[col].to_numpy(dtype=float)
            arrs.append(a)
        c = pd.Series(arrs[3]).ffill().bfill().to_numpy()
        o, h, l = (np.where(np.isnan(x), c, x) for x in arrs[:3])
        days.append(d)
        T.append(base + np.arange(N_MIN) * 60)
        O.append(o), H.append(h), L.append(l), C.append(c)
    return days, np.array(T), np.array(O), np.array(H), np.array(L), np.array(C)


def run(engine: Engine, sym: str, minutes: pd.DataFrame, only_days: set | None = None) -> int:
    days, T, O, H, L, C = day_arrays(minutes)
    ctxs = build_contexts(days, H, L, C)
    n = 0
    for i, d in enumerate(days):
        if d not in ctxs or (only_days is not None and d not in only_days):
            continue
        engine.start_day(sym, d, ctxs[d])
        for k in range(N_MIN):
            engine.on_bar(sym, float(T[i, k]), O[i, k], H[i, k], L[i, k], C[i, k])
        engine.end_day(sym)
        n += 1
    _log.info("replayed", sym=sym, days=n, trades=len(engine.closed), signals=len(engine.signals))
    return n


def load_minutes(path: Path | str) -> pd.DataFrame:
    return pd.read_parquet(path, columns=["t", "o", "h", "l", "c"])
