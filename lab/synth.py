"""Synthetic history in Dhan's rolling-option format, ONLY for testing the pipeline offline.
Results on this data mean nothing about real markets."""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd
from scipy.stats import norm

from .data import IST_OFFSET, N_MIN, OFFSETS, UNDERLYINGS, hist_dir

BASE = {"NIFTY": (24000.0, 0.13), "BANKNIFTY": (52000.0, 0.15)}


def _bs(S, K, T, sig, call):
    T = np.maximum(T, 1e-6)
    st = sig * np.sqrt(T)
    d1 = (np.log(S / K) + 0.5 * sig * sig * T) / st
    d2 = d1 - st
    c = S * norm.cdf(d1) - K * norm.cdf(d2)
    return np.where(call, c, c - S + K)


def _expiries(days, sym):
    out = []
    for d in days:
        if sym == "NIFTY":
            wd = 1 if d >= dt.date(2025, 9, 1) else 3
            nxt = d + dt.timedelta(days=(wd - d.weekday()) % 7)
        else:
            m_end = dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1) - dt.timedelta(days=1)
            wd = 1 if d >= dt.date(2025, 9, 1) else 3
            nxt = m_end - dt.timedelta(days=(m_end.weekday() - wd) % 7)
            if nxt < d:
                n2 = dt.date(m_end.year + (m_end.month == 12), m_end.month % 12 + 1, 28) + dt.timedelta(days=4)
                m2 = n2.replace(day=1) - dt.timedelta(days=1)
                nxt = m2 - dt.timedelta(days=(m2.weekday() - wd) % 7)
        out.append(nxt)
    return out


def generate(data_dir, start=dt.date(2025, 1, 1), end=dt.date(2026, 3, 31), seed=7):
    rng = np.random.default_rng(seed)
    days = [d.date() for d in pd.bdate_range(start, end)]
    hd = hist_dir(data_dir)
    mins = np.arange(N_MIN)
    shape = 1.4 - 0.8 * np.sin(np.pi * mins / N_MIN)          # busier open and close
    shape = shape / np.sqrt((shape ** 2).mean())
    vix_rows = []
    for sym in UNDERLYINGS:
        S0, base_sig = BASE[sym]
        step = UNDERLYINGS[sym]["step"]
        exps = _expiries(days, sym)
        S = S0
        daily, frames = [], {}
        regime = base_sig
        for d, ex in zip(days, exps):
            regime = float(np.clip(regime * math.exp(rng.normal(0, 0.08)) + 0.05 * (base_sig - regime), 0.07, 0.35))
            iv_day = regime * 1.12                               # options priced a bit above what usually happens
            real = regime * math.exp(rng.normal(0, 0.35))
            drift = rng.normal(0, 0.25) * real / math.sqrt(252) if rng.random() < 0.3 else 0.0
            gap = rng.normal(0, 0.3 * real / math.sqrt(252))
            r = rng.normal(0, real / math.sqrt(252 * N_MIN), N_MIN) * shape + drift / N_MIN
            path = S * math.exp(gap) * np.exp(np.cumsum(r))
            o = path[0]
            close_dt = dt.datetime.combine(ex, dt.time(15, 30))
            t_min = np.array([(close_dt - dt.datetime.combine(d, dt.time(9, 15)) - dt.timedelta(minutes=int(m))).total_seconds()
                              for m in mins]) / (365 * 86400)
            ts = np.array([int(dt.datetime.combine(d, dt.time(9, 15)).timestamp()) - IST_OFFSET + int(m) * 60 for m in mins])
            # make the timestamps IST-consistent regardless of the machine's timezone
            ts = np.array([int((dt.datetime(d.year, d.month, d.day, 9, 15) - dt.datetime(1970, 1, 1)).total_seconds()) - IST_OFFSET + 60 * int(m) for m in mins])
            atm = np.round(path / step) * step
            for off in OFFSETS:
                K = atm + off * step
                mny = np.log(K / path)
                for side in ("CE", "PE"):
                    iv = iv_day + 0.6 * np.abs(mny) + (0.15 * -mny).clip(0)
                    px = _bs(path, K, t_min, iv, side == "CE")
                    px = np.maximum(0.05, np.round(px * 20) / 20)
                    h = px * (1 + np.abs(rng.normal(0, 0.004, N_MIN)))
                    l = px * (1 - np.abs(rng.normal(0, 0.004, N_MIN)))
                    frames.setdefault(f"{d:%Y-%m}", []).append(pd.DataFrame({
                        "ts": ts, "off": off, "side": side, "open": px, "high": h, "low": l, "close": px,
                        "iv": iv * 100, "volume": 1000.0, "oi": 1e5, "strike": K, "spot": path}))
            daily.append({"date": d, "open": o, "high": path.max(), "low": path.min(), "close": path[-1]})
            if sym == "NIFTY":
                vix_rows.append({"date": d, "open": iv_day * 100 * 0.95, "high": iv_day * 100, "low": iv_day * 90, "close": iv_day * 100 * 0.97})
            S = path[-1]
        (hd / sym).mkdir(parents=True, exist_ok=True)
        for ym, parts in frames.items():
            df = pd.concat(parts, ignore_index=True)
            df["off"] = df["off"].astype("int8")
            df.to_parquet(hd / sym / f"raw_{ym}.parquet", compression="zstd", index=False)
        pd.DataFrame(daily).to_parquet(hd / sym / "daily.parquet", index=False)
    (hd / "INDIAVIX").mkdir(parents=True, exist_ok=True)
    pd.DataFrame(vix_rows).to_parquet(hd / "INDIAVIX" / "daily.parquet", index=False)
    (hd / "SYNTHETIC").write_text("synthetic test data")
