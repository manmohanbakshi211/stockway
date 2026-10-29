"""Upstox historical options: IV, expiry windows, and building backtest days from fake API answers."""
import datetime as dt

import numpy as np
import pandas as pd
from scipy.stats import norm

from lab import upstox_hist as U
from lab.data import build_days, iter_days


def _bs(S, K, T, sig, call, r=U.RATE):
    st = sig * np.sqrt(T)
    d1 = (np.log(S / K) + (r + 0.5 * sig * sig) * T) / st
    c = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d1 - st)
    return c if call else c - S + K * np.exp(-r * T)


def test_implied_vol_roundtrip():
    S, T = 25000.0, 3 / 365
    ks = np.array([24800, 25000, 25200.0])
    px = np.array([_bs(S, k, T, 0.14, True) for k in ks])
    iv = U.implied_vol(px, np.full(3, S), ks, np.full(3, T), np.ones(3, bool))
    assert np.allclose(iv, 14.0, atol=0.05)


def test_windows_weekly_and_monthly():
    ex = ["2025-04-03", "2025-04-09", "2025-04-17", "2025-04-24", "2025-05-29"]
    w = U._windows(ex, dt.date(2025, 4, 5), dt.date(2025, 4, 30), monthly=False)
    assert [(str(e), str(a), str(b)) for e, a, b in w] == [("2025-04-09", "2025-04-05", "2025-04-09"),
        ("2025-04-17", "2025-04-10", "2025-04-17"), ("2025-04-24", "2025-04-18", "2025-04-24"), ("2025-05-29", "2025-04-25", "2025-04-30")]
    m = U._windows(ex, dt.date(2025, 4, 1), dt.date(2025, 5, 31), monthly=True)
    assert [str(e) for e, _, _ in m] == ["2025-04-24", "2025-05-29"]


class FakeUp:
    """Two trading days before a 2025-04-24 expiry, Nifty around 24,000."""
    def __init__(self):
        self.days = [dt.date(2025, 4, 23), dt.date(2025, 4, 24)]

    def _ts(self, d, m):
        return dt.datetime(d.year, d.month, d.day, 9, 15, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))) + dt.timedelta(minutes=m)

    def contracts(self, sym, expiry):
        return [{"strike_price": k, "instrument_type": s, "expired_instrument_key": f"NSE_FO|{int(k)}{s}|24-04-2025"}
                for k in range(23000, 25050, 50) for s in ("CE", "PE")]

    def expired_candles(self, key, frm, to):
        k = float(key.split("|")[1][:-2]); call = key.split("|")[1].endswith("CE")
        out = []
        for d in self.days:
            if frm <= d <= to:
                for m in range(0, 375, 1):
                    S = 24000 + m * 0.5
                    T = max((dt.datetime(2025, 4, 24, 15, 30) - dt.datetime(d.year, d.month, d.day, 9, 15) - dt.timedelta(minutes=m)).total_seconds(), 60) / (365 * 86400)
                    p = round(float(_bs(S, k, T, 0.13, call)), 2)
                    out.append([self._ts(d, m).isoformat(), p, p, p, p, 100, 5000])
        return out


def test_build_expiry_makes_backtest_days(tmp_path):
    up = FakeUp()
    spot = pd.DataFrame([{"ts": int(up._ts(d, m).timestamp()), "open": 24000 + m * 0.5, "high": 24000 + m * 0.5,
                          "low": 24000 + m * 0.5, "close": 24000 + m * 0.5} for d in up.days for m in range(375)])
    df = U.build_expiry(up, "NIFTY", dt.date(2025, 4, 24), up.days[0], up.days[1], spot)
    assert df is not None and set(df["side"]) == {"CE", "PE"}
    assert df["strike"].min() >= 23500 and df["strike"].max() <= 24700          # only ATM±10 around the range
    atm = df[(df["off"] == 0) & (df["side"] == "CE")]
    assert atm["iv"].between(11, 15).mean() > 0.9
    f = tmp_path / "history" / "NIFTY" / "raw_2025-04-24.parquet"
    f.parent.mkdir(parents=True)
    df.to_parquet(f, index=False)
    days = build_days(df, "NIFTY")
    assert [d.date for d in days] == up.days
    assert len(list(iter_days(tmp_path, "NIFTY", dt.date(2025, 4, 1), dt.date(2025, 4, 30)))) == 2
    assert not np.isnan(days[0].price(24000, "CE", 5))
