"""Daily market data from Yahoo Finance, cached as Parquet."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

COLS = ["Open", "High", "Low", "Close"]


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=COLS)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[[c for c in COLS if c in df.columns]].copy()
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    df.index = idx.normalize()
    df.index.name = "date"
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df.dropna(subset=["Close"])
    # Yahoo sometimes returns Open=0 or High<Low on Indian indices; repair what we can
    for c in ("Open", "High", "Low"):
        if c in df.columns:
            df[c] = df[c].where(df[c] > 0, df["Close"])
    df["High"] = df[["Open", "High", "Low", "Close"]].max(axis=1)
    df["Low"] = df[["Open", "High", "Low", "Close"]].min(axis=1)
    return df.astype(float)


def _download(symbol: str, start: str) -> pd.DataFrame:
    import yfinance as yf

    raw = yf.download(symbol, start=start, auto_adjust=False, progress=False, threads=False)
    return _clean(raw)


def load_ticker(name: str, symbol: str, start: str, cache_dir: str, refresh: bool = True) -> pd.DataFrame:
    path = Path(cache_dir) / f"mkt_{name}.parquet"
    cached = pd.read_parquet(path) if path.exists() else None
    if cached is not None and not refresh:
        return cached
    if cached is not None and not cached.empty:
        last = cached.index.max()
        if last.date() >= dt.date.today() - dt.timedelta(days=1):
            return cached
        fresh = _download(symbol, (last - pd.Timedelta(days=10)).strftime("%Y-%m-%d"))
        df = pd.concat([cached, fresh])
        df = df[~df.index.duplicated(keep="last")].sort_index()
    else:
        df = _download(symbol, start)
    if df.empty:
        if cached is not None:
            print(f"  ! {name} ({symbol}): download failed, using cache")
            return cached
        raise RuntimeError(f"No data for {name} ({symbol}). Check internet / symbol.")
    df.to_parquet(path)
    return df


def load_all(cfg: dict, refresh: bool = True) -> dict[str, pd.DataFrame]:
    out = {}
    for name, symbol in cfg["tickers"].items():
        try:
            out[name] = load_ticker(name, symbol, cfg["start_date"], cfg["cache_dir"], refresh)
            print(f"  {name:<10} {symbol:<10} {len(out[name]):>5} days  "
                  f"{out[name].index.min().date()} -> {out[name].index.max().date()}")
            if name == "banknifty":
                d = out[name]
                flat = ((d["High"] - d["Low"]) <= 0).mean()
                same_open = (d["Open"] == d["Close"].shift(1)).mean()
                if flat > 0.01 or same_open > 0.05:
                    print(f"  ! data check: {flat:.1%} flat candles, {same_open:.1%} opens equal to prior close. "
                          f"Yahoo's Indian index data has gaps; consider NSE bhavcopy/broker data (README).")
        except Exception as e:  # global tickers are optional; Bank Nifty is not
            if name in ("banknifty", "nifty", "india_vix"):
                raise
            print(f"  ! skipping {name}: {e}")
    return out
