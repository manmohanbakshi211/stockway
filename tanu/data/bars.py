"""1-minute bars built from the tick tape (derived data: always rebuildable from the tape, ADR-0006).

A tick belongs to the minute of its exchange trade time (`ltt`), or of its receive time when the broker sent none.
Per instrument and minute: o / h / l / c of the traded price, vol (traded in that minute, from the cumulative day
volume), cum_vol, last oi / bid / ask, number of ticks. Only minutes inside the day's session are kept.
Columns: key, t (minute start, epoch seconds), o, h, l, c, vol, cum_vol, oi, bid, ask, ticks, src ("tape").
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..brokers.upstox.decode import decode
from ..core import log
from . import tape as T
from .facts import Facts

COLS = ["key", "t", "o", "h", "l", "c", "vol", "cum_vol", "oi", "bid", "ask", "ticks", "src"]
_log = log.get("bars")


def from_records(records, window: tuple[float, float] | None = None) -> pd.DataFrame:
    """records: (receive_time, kind, payload) as read from a tape."""
    acc: dict = {}
    for t_recv, kind, raw in records:
        if kind != T.KIND_MSG:
            continue
        try:
            msg = decode(raw)
        except Exception:
            continue
        for tk in msg["ticks"]:
            p = tk.get("ltp")
            if not p:
                continue
            ts = tk["ltt"] / 1000 if tk.get("ltt") else t_recv
            m = int(ts // 60) * 60
            if window and not (window[0] <= m < window[1]):
                continue
            b = acc.get((tk["key"], m))
            if b is None:
                acc[(tk["key"], m)] = b = [p, p, p, p, None, None, None, None, 0]
            else:
                b[1], b[2], b[3] = max(b[1], p), min(b[2], p), p
            if tk.get("vol") is not None:
                b[4] = tk["vol"] if b[4] is None else max(b[4], tk["vol"])
            if tk.get("oi") is not None:
                b[5] = tk["oi"]
            if tk.get("bid") is not None:
                b[6], b[7] = tk["bid"], tk.get("ask")
            b[8] += 1
    if not acc:
        return pd.DataFrame(columns=COLS)
    rows = [(k, m, *v) for (k, m), v in acc.items()]
    df = pd.DataFrame(rows, columns=["key", "t", "o", "h", "l", "c", "cum_vol", "oi", "bid", "ask", "ticks"])
    df = df.sort_values(["key", "t"], kind="stable").reset_index(drop=True)
    prev = df.groupby("key")["cum_vol"].shift(1)
    df["vol"] = (df["cum_vol"] - prev.fillna(0)).where(df["cum_vol"].notna())
    df["src"] = "tape"
    return df[COLS]


def build_day(tape_dir: Path | str, source: str, day: str, out_dir: Path | str, facts: Facts | None = None) -> Path:
    """Bars for one recorded day -> <out_dir>/<source>/<day>.parquet (replaced: derived data)."""
    sess = (facts or Facts()).session(day)
    window = (sess[0].timestamp(), sess[1].timestamp()) if sess else None
    df = from_records(T.read_day(tape_dir, source, day), window)
    out = Path(out_dir) / source / f"{day}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    df.to_parquet(tmp, compression="zstd", index=False)
    tmp.replace(out)
    _log.info("bars_built", source=source, day=day, rows=len(df), instruments=int(df["key"].nunique()) if len(df) else 0)
    return out
