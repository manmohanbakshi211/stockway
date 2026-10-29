"""Historical option data from Upstox (needs the Upstox Plus plan for expired contracts).

For every expiry, the contracts that were near the money while that expiry was the nearest one are fetched as
1-minute candles (price, volume, OI), with the index's 1-minute prices and an implied volatility computed from them.
Saved per expiry in the same layout the backtest already reads (lab.data.build_days).
"""
from __future__ import annotations

import datetime as dt
import math
import time
import urllib.parse

import numpy as np
import pandas as pd

from .data import IST_OFFSET, UNDERLYINGS, hist_dir

INDEX_KEY = {"NIFTY": "NSE_INDEX|Nifty 50", "BANKNIFTY": "NSE_INDEX|Nifty Bank", "INDIAVIX": "NSE_INDEX|India VIX"}
BAND = 10                 # strikes either side of the at-the-money strike, like Dhan's ATM±10
RATE = 0.065              # risk-free rate for implied volatility


class PlusRequired(PermissionError):
    pass


class Limiter:
    """Upstox standard API limits are 50/s, 500/min, 2000/30 min; stay well under them (the live app shares them)."""
    WINDOWS = ((1.0, 10), (60.0, 400), (1800.0, 1800))

    def __init__(self):
        self.calls = []

    def wait(self):
        while True:
            now = time.time()
            self.calls = [t for t in self.calls if now - t < 1800]
            delay = 0.0
            for win, cap in self.WINDOWS:
                recent = [t for t in self.calls if now - t < win]
                if len(recent) >= cap:
                    delay = max(delay, win - (now - recent[0]) + 0.05)
            if delay == 0:
                self.calls.append(now)
                return
            time.sleep(min(delay, 5))


class UpstoxHistory:
    API = "https://api.upstox.com"

    def __init__(self, token: str):
        import requests
        self.s = requests.Session()
        self.s.headers.update({"Accept": "application/json", "Authorization": f"Bearer {token}"})
        self.lim = Limiter()

    def get(self, path, params=None, tries=5):
        for k in range(tries):
            self.lim.wait()
            try:
                r = self.s.get(self.API + path, params=params, timeout=30)
            except Exception:
                time.sleep(2 * (k + 1))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(3 * (k + 1))
                continue
            body = {}
            try:
                body = r.json()
            except Exception:
                pass
            if r.status_code == 401:
                msg = str(body.get("errors") or "")
                if "UDAPI1149" in msg or "Plus" in msg:
                    raise PlusRequired("Historical option data needs the Upstox Plus plan. If you just upgraded, "
                                       "log out and log in to Upstox again on the Live setup page.")
                raise PermissionError("Upstox login expired. Log in again on the Live setup page.")
            if r.status_code == 400 and "No data" in r.text:
                return None
            r.raise_for_status()
            return body.get("data")
        raise RuntimeError(f"Upstox did not answer {path}")

    # ---------------------------------------------------------------- endpoints
    def expiries(self, sym):
        return sorted(self.get("/v2/expired-instruments/expiries", {"instrument_key": INDEX_KEY[sym]}) or [])

    def contracts(self, sym, expiry: str):
        return self.get("/v2/expired-instruments/option/contract",
                        {"instrument_key": INDEX_KEY[sym], "expiry_date": expiry}) or []

    def expired_candles(self, key, frm: dt.date, to: dt.date):
        q = urllib.parse.quote(key, safe="")
        d = self.get(f"/v2/expired-instruments/historical-candle/{q}/1minute/{to.isoformat()}/{frm.isoformat()}") or {}
        return d.get("candles") or []

    def index_minutes(self, sym, frm: dt.date, to: dt.date):
        out = []
        a = frm
        while a <= to:                                   # 1-minute data: up to one month per request
            b = min(to, a + dt.timedelta(days=27))
            q = urllib.parse.quote(INDEX_KEY[sym], safe="")
            d = self.get(f"/v3/historical-candle/{q}/minutes/1/{b.isoformat()}/{a.isoformat()}") or {}
            out += d.get("candles") or []
            a = b + dt.timedelta(days=1)
        return out

    def index_daily(self, sym, frm: dt.date, to: dt.date):
        q = urllib.parse.quote(INDEX_KEY[sym], safe="")
        d = self.get(f"/v3/historical-candle/{q}/days/1/{to.isoformat()}/{frm.isoformat()}") or {}
        return d.get("candles") or []


# ---------------------------------------------------------------- helpers
def _epoch(s) -> int:
    return int(dt.datetime.fromisoformat(str(s)).timestamp())


def _frame(candles, cols=("ts", "open", "high", "low", "close", "volume", "oi")) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=list(cols))
    rows = [[_epoch(c[0])] + [float(x) if x is not None else math.nan for x in c[1:len(cols)]] for c in candles]
    rows = [r + [math.nan] * (len(cols) - len(r)) for r in rows]
    return pd.DataFrame(rows, columns=list(cols)).sort_values("ts").drop_duplicates("ts")


def implied_vol(price, S, K, T, call, r=RATE):
    """Vectorised Black-Scholes implied volatility (bisection), in percent. NaN where no solution."""
    from scipy.stats import norm
    price, S, K, T = (np.asarray(x, dtype=float) for x in (price, S, K, T))
    call = np.asarray(call, dtype=bool)
    T = np.maximum(T, 1e-5)
    disc = np.exp(-r * T)
    intrinsic = np.where(call, np.maximum(S - K * disc, 0), np.maximum(K * disc - S, 0))
    lo, hi = np.full(price.shape, 0.01), np.full(price.shape, 3.0)

    def bs(sig):
        st = sig * np.sqrt(T)
        d1 = (np.log(S / K) + (r + 0.5 * sig * sig) * T) / st
        c = S * norm.cdf(d1) - K * disc * norm.cdf(d1 - st)
        return np.where(call, c, c - S + K * disc)
    for _ in range(40):
        mid = (lo + hi) / 2
        above = bs(mid) > price
        hi = np.where(above, mid, hi)
        lo = np.where(above, lo, mid)
    iv = (lo + hi) / 2 * 100
    bad = (price <= intrinsic + 0.05) | (iv >= 299) | (iv <= 1.1) | ~np.isfinite(price)
    return np.where(bad, np.nan, iv)


def _windows(expiries: list[str], start: dt.date, end: dt.date, monthly: bool):
    """[(expiry, first_day, last_day)]: the days on which each expiry was the nearest one."""
    ex = [dt.date.fromisoformat(e) for e in expiries]
    if monthly:                                          # keep the last expiry of each month
        by_m = {}
        for e in ex:
            by_m[(e.year, e.month)] = max(e, by_m.get((e.year, e.month), e))
        ex = sorted(by_m.values())
    out = []
    for i, e in enumerate(ex):
        first = ex[i - 1] + dt.timedelta(days=1) if i else e - dt.timedelta(days=35 if monthly else 7)
        if e < start or first > end:
            continue
        out.append((e, max(first, start), min(e, end)))
    return out


def build_expiry(up: UpstoxHistory, sym: str, expiry: dt.date, first: dt.date, last: dt.date, spot: pd.DataFrame,
                 should_stop=lambda: False, tick=lambda: None) -> pd.DataFrame | None:
    """All near-the-money contracts of one expiry, for the days it was the nearest expiry."""
    step = UNDERLYINGS[sym]["step"]
    sp = spot[(spot["ts"] >= _day_ts(first)) & (spot["ts"] < _day_ts(last) + 86400)]
    if sp.empty:
        return None
    lo = math.floor(sp["low"].min() / step) * step - BAND * step
    hi = math.ceil(sp["high"].max() / step) * step + BAND * step
    cons = up.contracts(sym, expiry.isoformat())
    tick()
    want = []
    for c in cons:
        k = float(c.get("strike_price") or 0)
        side = c.get("instrument_type")
        key = c.get("expired_instrument_key") or c.get("instrument_key")
        if lo <= k <= hi and side in ("CE", "PE") and key and abs(k / step - round(k / step)) < 1e-6:
            want.append((k, side, key))
    parts = []
    for k, side, key in sorted(want):
        if should_stop():
            return None
        cs = []
        a = first
        while a <= last:                                 # 1-minute: one month per request
            b = min(last, a + dt.timedelta(days=27))
            try:
                cs += up.expired_candles(key, a, b)
            except PermissionError:
                raise
            except Exception:
                pass                                     # one missing contract shouldn't sink the expiry
            a = b + dt.timedelta(days=1)
        tick()
        f = _frame(cs)
        if f.empty:
            continue
        f["strike"], f["side"] = k, side
        parts.append(f)
    if not parts:
        return None
    df = pd.concat(parts, ignore_index=True)
    sp1 = sp[["ts", "close"]].rename(columns={"close": "spot"}).sort_values("ts")
    df = pd.merge_asof(df.sort_values("ts"), sp1, on="ts", direction="backward", tolerance=120)
    df = df.dropna(subset=["spot"])
    df["off"] = np.round((df["strike"] - np.round(df["spot"] / step) * step) / step).astype("int16")
    exp_ts = _day_ts(expiry) + (15 * 60 + 30) * 60
    T = (exp_ts - df["ts"].to_numpy()) / (365 * 86400)
    near = df["off"].abs().to_numpy() <= 3
    iv = np.full(len(df), np.nan)
    if near.any():
        iv[near] = implied_vol(df["close"].to_numpy()[near], df["spot"].to_numpy()[near], df["strike"].to_numpy()[near],
                               T[near], (df["side"] == "CE").to_numpy()[near])
    df["iv"] = iv
    return df[["ts", "off", "side", "open", "high", "low", "close", "iv", "volume", "oi", "strike", "spot"]]


def _day_ts(d: dt.date) -> int:
    """Epoch seconds of 00:00 IST on day d."""
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp()) - IST_OFFSET


def _save_daily(up: UpstoxHistory, hd, sym, start, end, log):
    rows = []
    a = start - dt.timedelta(days=60)
    while a <= end:
        b = min(end, a + dt.timedelta(days=360))
        rows += up.index_daily(sym, a, b)
        a = b + dt.timedelta(days=1)
    f = _frame(rows, ("ts", "open", "high", "low", "close"))
    if f.empty:
        log(f"Daily {sym}: no data")
        return
    f["date"] = [dt.datetime.utcfromtimestamp(t + IST_OFFSET).date() for t in f["ts"]]
    (hd / sym).mkdir(parents=True, exist_ok=True)
    f[["date", "open", "high", "low", "close"]].to_parquet(hd / sym / "daily.parquet", index=False)


def download(token: str, data_dir, syms=("NIFTY", "BANKNIFTY"), start: dt.date | None = None, end: dt.date | None = None,
             log=print, should_stop=lambda: False, progress=lambda done, total: None):
    """Fetch every expiry in [start, end] that isn't on disk yet. Resumable: finished expiries are kept."""
    up = UpstoxHistory(token)
    end = end or dt.date.today() - dt.timedelta(days=1)
    start = start or dt.date(end.year - 3, end.month, 1)
    start = max(start, dt.date(2022, 1, 1))              # Upstox 1-minute index data starts in 2022
    hd = hist_dir(data_dir)
    for s in list(syms) + ["INDIAVIX"]:
        _save_daily(up, hd, s, start, end, log)
    jobs = []
    for sym in syms:
        ex = up.expiries(sym)
        if not ex:
            log(f"{sym}: Upstox returned no expired contracts")
            continue
        log(f"{sym}: expired contracts from {ex[0]} to {ex[-1]}")
        for e, a, b in _windows(ex, start, end, monthly=UNDERLYINGS[sym]["flag"] == "MONTH"):
            f = hd / sym / f"raw_{e.isoformat()}.parquet"
            if not f.exists():
                jobs.append((sym, e, a, b, f))
    if not jobs:
        log("Everything is already downloaded.")
        return
    step_est = {"NIFTY": 75, "BANKNIFTY": 100}
    total = sum(step_est[j[0]] for j in jobs)
    done = [0]

    def tick():
        done[0] += 1
        progress(min(done[0], total - 1), total)
    log(f"{len(jobs)} expiries to fetch (about {total:,} requests; roughly {max(1, total // 1800 * 30)} minutes or more)")
    for sym, e, a, b, f in jobs:
        if should_stop():
            log("Stopped. Finished expiries are kept; run again to continue.")
            return
        sp = _frame(up.index_minutes(sym, a, b), ("ts", "open", "high", "low", "close", "volume"))
        try:
            df = build_expiry(up, sym, e, a, b, sp, should_stop, tick)
        except PermissionError:
            raise
        except Exception as ex:
            log(f"  {sym} expiry {e}: {ex}")
            continue
        if df is None or df.empty:
            if not should_stop():
                log(f"{sym} expiry {e}: no data")
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(f, compression="zstd", index=False)
        log(f"{sym} expiry {e} ({a:%d %b}–{b:%d %b %Y}): {df['strike'].nunique()} strikes, {len(df):,} rows")
    progress(total, total)
    log("Download complete.")
