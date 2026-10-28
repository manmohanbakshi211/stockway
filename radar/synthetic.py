"""Synthetic market data so the full pipeline can be tested offline (--demo).
Numbers are fake; they only prove the code runs end to end."""
from __future__ import annotations

import numpy as np
import pandas as pd


def make_demo(start="2015-01-01", end=None, seed=7) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    end = end or pd.Timestamp.today().normalize() - pd.Timedelta(days=1)
    days = pd.bdate_range(start, end)
    n = len(days)
    # volatility regime (persistent) drives everything
    reg = np.zeros(n)
    for i in range(1, n):
        reg[i] = 0.97 * reg[i - 1] + rng.normal(0, 0.25)
    sigma = 0.011 * np.exp(0.35 * reg)                     # Bank Nifty daily sigma
    vix = 100 * sigma * np.sqrt(252) / 1.3 * np.exp(rng.normal(0, 0.05, n))
    spx = rng.normal(0.0004, 0.6 * sigma)
    shock = np.zeros(n)
    shock[1:] = np.abs(spx[:-1]) / (0.6 * sigma[:-1]) > 2  # big US day -> bigger India day next
    eff = sigma * (1 + 0.8 * shock)
    gap = rng.normal(0, 0.35 * eff) + 0.3 * np.r_[0, spx[:-1]]
    oc = rng.normal(0.0003, eff)
    close = 15000 * np.exp(np.cumsum(gap + oc))
    open_ = close / np.exp(oc)
    wick = np.abs(rng.normal(0, 0.6 * eff, (2, n)))
    high = np.maximum(open_, close) * (1 + wick[0])
    low = np.minimum(open_, close) * (1 - wick[1])

    def ohlc(c, vol):
        o = c / np.exp(rng.normal(0, vol / 2, n))
        return pd.DataFrame({"Open": o, "High": np.maximum(o, c) * (1 + vol / 3),
                             "Low": np.minimum(o, c) * (1 - vol / 3), "Close": c}, index=days)

    out = {
        "banknifty": pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close}, index=days),
        "nifty": ohlc(8000 * np.exp(np.cumsum(0.75 * (gap + oc))), 0.008),
        "india_vix": ohlc(vix, 0.03),
        "sp500": ohlc(2000 * np.exp(np.cumsum(spx)), 0.006),
        "us_vix": ohlc(15 + 300 * np.abs(spx) + rng.normal(0, 1, n).cumsum() * 0.05, 0.03),
    }
    for name, base, vol in (("nasdaq", 5000, 0.012), ("us10y", 2.5, 0.01), ("dxy", 95, 0.004),
                            ("brent", 60, 0.02), ("usdinr", 65, 0.003), ("gold", 1200, 0.008),
                            ("nikkei", 18000, 0.012), ("hangseng", 23000, 0.012)):
        out[name] = ohlc(base * np.exp(np.cumsum(rng.normal(0, vol, n))), vol)
    for df in out.values():
        df.index.name = "date"
    return out
