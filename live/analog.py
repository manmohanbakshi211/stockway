"""Scenario engine: today's chart vs every past session, and three live paths (up / sideways / down).

How it works
1. Library: every Nifty / Bank Nifty session since 2022 as 375 one-minute closes (Upstox history).
2. Matching at minute t: today's path so far, measured in units of the day's normal volatility (so a 0.5% move on a
   calm day matches a 1% move on a wild one), the recent 90 minutes weighted most, plus context (gap, distance from
   yesterday's close, VIX, range so far). The 40 closest past sessions (only sessions before today) are the analogs.
3. Forecast: what the analogs did next, rescaled to today's volatility. For 5, 15 and 60 minutes ahead:
   probability of up / sideways / down (sideways = a move under 0.43 sigma, which is a third of days on average),
   the median move and an 80% range. Paths for the chart are the analogs' median path in each of the three groups.
4. Honesty: the same procedure is replayed on past days (walk-forward, no peeking) to measure how often each call
   came true versus the plain base rate. Research says the size of moves is far more predictable than their direction
   (volatility clusters; direction effects like intraday momentum are small), and the panel shows both scores.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import threading
import time
from pathlib import Path

import numpy as np

from .clock import IST

N = 375                      # 09:15 .. 15:29
SYMS = {"NIFTY": "NSE_INDEX|Nifty 50", "BANKNIFTY": "NSE_INDEX|Nifty Bank"}
HORIZONS = (5, 15, 60)
K = 40
SIDE_Z = 0.43                # |move| < 0.43 sigma_h -> sideways (about a third of a normal distribution)
MIN_T = 15                   # need 15 minutes of today before matching
STEP = 5                     # compare paths on 5-minute points


def _day_ts(d: dt.date) -> float:
    return dt.datetime.combine(d, dt.time(9, 15), IST).timestamp()


class Library:
    """All past sessions of one index as aligned 1-minute arrays."""

    def __init__(self, sym, df, vix=None):
        import pandas as pd
        self.sym = sym
        df = df.sort_values("ts").drop_duplicates("ts")
        t = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
        df = df.assign(date=t.dt.date, m=(t.dt.hour * 60 + t.dt.minute - 555).astype(int))
        df = df[(df["m"] >= 0) & (df["m"] < N)]
        days, C, H, L = [], [], [], []
        for d, g in df.groupby("date"):
            if len(g) < 300:
                continue
            c = np.full(N, np.nan); h = np.full(N, np.nan); l = np.full(N, np.nan)
            c[g["m"].to_numpy()] = g["close"].to_numpy()
            h[g["m"].to_numpy()] = g["high"].to_numpy()
            l[g["m"].to_numpy()] = g["low"].to_numpy()
            c = pd.Series(c).ffill().bfill().to_numpy()
            h = np.where(np.isnan(h), c, h); l = np.where(np.isnan(l), c, l)
            days.append(d); C.append(c); H.append(h); L.append(l)
        self.days = days
        self.C = np.array(C) if C else np.zeros((0, N))
        self.H = np.array(H) if H else np.zeros((0, N))
        self.L = np.array(L) if L else np.zeros((0, N))
        n = len(days)
        self.prev_close = np.r_[np.nan, self.C[:-1, -1]] if n else np.array([])
        rng = np.log(self.H.max(1) / self.L.min(1)) if n else np.array([])
        pk = rng ** 2 / (4 * math.log(2))
        # normal daily volatility known before each day: Parkinson average of the previous 20 sessions
        sig = np.full(n, np.nan)
        for i in range(n):
            a = pk[max(0, i - 20):i]
            if len(a) >= 5:
                sig[i] = math.sqrt(a.mean())
        self.sigma = sig
        vmap = vix or {}
        self.vix = np.array([vmap.get(d, np.nan) for d in days], dtype=float)
        self.index = {d: i for i, d in enumerate(days)}

    def sigma_for(self, day: dt.date, prev_days: list | None = None) -> float:
        """Normal daily volatility for a day not (yet) in the library: from the last 20 sessions before it."""
        i = np.searchsorted(np.array(self.days), day)
        rng = np.log(self.H[max(0, i - 20):i].max(1) / self.L[max(0, i - 20):i].min(1))
        if len(rng) < 5:
            return float("nan")
        return float(math.sqrt((rng ** 2 / (4 * math.log(2))).mean()))


def _features(path_log, t, sig, gap_z, prev_z, vix):
    """Vector describing a session up to minute t (log prices relative to the open, in sigma units)."""
    pts = np.arange(t % STEP, t + 1, STEP)
    x = path_log[pts] / sig
    w = np.exp(-(t - pts) / 90.0)                    # recent minutes matter most
    rng = (np.max(path_log[:t + 1]) - np.min(path_log[:t + 1])) / sig
    return x, w, np.array([gap_z, prev_z, rng, (vix if vix == vix else np.nan)])


def forecast(lib: Library, closes: np.ndarray, t: int, day: dt.date, prev_close: float, sigma: float,
             vix: float | None = None, k: int = K, horizons=HORIZONS, before: dt.date | None = None) -> dict | None:
    """closes: today's 1-minute closes from 09:15 (index 0) to minute t (inclusive)."""
    if t < MIN_T or not (sigma and sigma == sigma) or len(closes) <= t:
        return None
    lim = before or day
    cand = np.array([i for i, d in enumerate(lib.days) if d < lim and lib.sigma[i] == lib.sigma[i]
                     and lib.prev_close[i] == lib.prev_close[i]])
    if len(cand) < 60:
        return None
    o = closes[0]
    today = np.log(closes[:t + 1] / o)
    gap_z = math.log(o / prev_close) / sigma
    prev_z = today[-1] / sigma + gap_z
    x0, w, ctx0 = _features(np.r_[today], t, sigma, gap_z, prev_z, vix if vix else np.nan)
    C = lib.C[cand]
    O = C[:, :1]
    P = np.log(C[:, :t + 1] / O)
    S = lib.sigma[cand][:, None]
    pts = np.arange(t % STEP, t + 1, STEP)
    X = P[:, pts] / S
    d_path = np.sqrt((((X - x0) ** 2) * w).sum(1) / w.sum())
    g = np.log(O[:, 0] / lib.prev_close[cand]) / lib.sigma[cand]
    pz = X[:, -1] + g
    rng = (P.max(1) - P.min(1)) / S[:, 0]
    d_ctx = np.sqrt(0.5 * (g - gap_z) ** 2 + 0.3 * (pz - prev_z) ** 2 + 0.3 * (rng - ctx0[2]) ** 2)
    vx = lib.vix[cand]
    d_vix = np.where(np.isnan(vx) | (vix is None) | (vix != vix if vix else True), 0.0, np.abs(vx - (vix or 0)) / 6.0)
    dist = d_path + 0.6 * d_ctx + 0.4 * d_vix
    order = np.argsort(dist)[:k]
    sel = cand[order]
    dd = dist[order]
    h = max(1e-6, float(np.median(dd)))
    wt = np.exp(-(dd / h) ** 2 / 2)
    wt = wt / wt.sum()
    spot = float(closes[t])
    scale = sigma / lib.sigma[sel]                      # analog moves in today's volatility units
    out = {"t": t, "spot": spot, "sigma_day_pct": round(sigma * 100, 3), "k": int(len(sel)), "horizons": {}}
    H_max = min(max(horizons), N - 1 - t)
    if H_max < 1:
        return None
    fwd = np.log(lib.C[sel, t + 1:t + 1 + H_max] / lib.C[sel, t:t + 1]) * scale[:, None]     # k x H
    for hz in horizons:
        if hz > H_max:
            continue
        r = fwd[:, hz - 1]
        sig_h = sigma * math.sqrt(hz / N)
        up, dn = r > SIDE_Z * sig_h, r < -SIDE_Z * sig_h
        pu, pd_ = float(wt[up].sum()), float(wt[dn].sum())
        q = _wq(r, wt, [0.1, 0.5, 0.9])
        out["horizons"][str(hz)] = {"up": round(pu, 3), "side": round(1 - pu - pd_, 3), "down": round(pd_, 3),
                                    "median_pts": round(spot * (math.exp(q[1]) - 1), 1),
                                    "lo_pts": round(spot * (math.exp(q[0]) - 1), 1), "hi_pts": round(spot * (math.exp(q[2]) - 1), 1),
                                    "side_band_pts": round(spot * SIDE_Z * sig_h, 1)}
    # three paths over the longest horizon, grouped by where the analogs ended
    end = fwd[:, -1]
    sig_e = sigma * math.sqrt(H_max / N)
    groups = {"up": end > SIDE_Z * sig_e, "side": np.abs(end) <= SIDE_Z * sig_e, "down": end < -SIDE_Z * sig_e}
    paths = {}
    for name, msk in groups.items():
        if msk.sum() == 0:
            continue
        ww = wt[msk] / wt[msk].sum()
        med = np.array([_wq(fwd[msk, j], ww, [0.5])[0] for j in range(H_max)])
        paths[name] = {"p": round(float(wt[msk].sum()), 3), "n": int(msk.sum()),
                       "path": [round(spot * math.exp(v), 2) for v in med]}
    lo = np.array([_wq(fwd[:, j], wt, [0.1])[0] for j in range(H_max)])
    hi = np.array([_wq(fwd[:, j], wt, [0.9])[0] for j in range(H_max)])
    out["paths"] = paths
    out["band"] = {"lo": [round(spot * math.exp(v), 2) for v in lo], "hi": [round(spot * math.exp(v), 2) for v in hi]}
    out["horizon_max"] = int(H_max)
    # the closest past sessions
    sims = []
    for j in range(min(6, len(sel))):
        i = sel[j]
        c = lib.C[i]
        sims.append({"date": lib.days[i].isoformat(), "similarity": round(float(100 * math.exp(-(dd[j] / (h * 2)) ** 2)), 0),
                     "next60_pct": round(float((c[min(N - 1, t + 60)] / c[t] - 1) * 100), 2),
                     "close_pct": round(float((c[-1] / c[t] - 1) * 100), 2),
                     "day_pct": round(float((c[-1] / lib.prev_close[i] - 1) * 100), 2)})
    out["similar"] = sims
    return out


def _wq(x, w, qs):
    i = np.argsort(x)
    xs, ws = x[i], np.cumsum(w[i])
    ws = ws / ws[-1]
    return [float(np.interp(q, ws, xs)) for q in qs]


def base_rates(lib: Library, t: int, hz: int, before: dt.date) -> dict:
    """How often, unconditionally, the next hz minutes from minute t went up / sideways / down before `before`."""
    idx = [i for i, d in enumerate(lib.days) if d < before and lib.sigma[i] == lib.sigma[i]]
    if not idx or t + hz >= N:
        return {}
    r = np.log(lib.C[idx, t + hz] / lib.C[idx, t]) / (lib.sigma[idx] * math.sqrt(hz / N))
    return {"up": float((r > SIDE_Z).mean()), "side": float((np.abs(r) <= SIDE_Z).mean()), "down": float((r < -SIDE_Z).mean())}


def evaluate(lib: Library, last_n: int = 250, checkpoints=range(30, 331, 30), log=print) -> dict:
    """Walk-forward replay: for each of the last N sessions and each checkpoint, forecast with only earlier sessions."""
    res = {str(h): {"brier": [], "brier_base": [], "hit": [], "hit_base": [], "inside": [], "conf_hit": [], "ratio": []} for h in HORIZONS}
    days = lib.days[-last_n:]
    for d in days:
        i = lib.index[d]
        if lib.sigma[i] != lib.sigma[i] or lib.prev_close[i] != lib.prev_close[i]:
            continue
        for t in checkpoints:
            f = forecast(lib, lib.C[i], t, d, lib.prev_close[i], lib.sigma[i], lib.vix[i] if lib.vix[i] == lib.vix[i] else None)
            if not f:
                continue
            for hz in HORIZONS:
                hs = f["horizons"].get(str(hz))
                if not hs or t + hz >= N:
                    continue
                br = base_rates(lib, t, hz, d)
                r = math.log(lib.C[i, t + hz] / lib.C[i, t]) / (lib.sigma[i] * math.sqrt(hz / N))
                actual = "up" if r > SIDE_Z else "down" if r < -SIDE_Z else "side"
                p = [hs["up"], hs["side"], hs["down"]]
                b = [br.get("up", 1 / 3), br.get("side", 1 / 3), br.get("down", 1 / 3)]
                y = [actual == "up", actual == "side", actual == "down"]
                R = res[str(hz)]
                R["brier"].append(sum((pi - yi) ** 2 for pi, yi in zip(p, y)))
                R["brier_base"].append(sum((pi - yi) ** 2 for pi, yi in zip(b, y)))
                call = ["up", "side", "down"][int(np.argmax(p))]
                R["hit"].append(call == actual)
                R["hit_base"].append(["up", "side", "down"][int(np.argmax(b))] == actual)
                move = lib.C[i, t + hz] - lib.C[i, t]
                R["inside"].append(hs["lo_pts"] <= move <= hs["hi_pts"])
                half = (hs["hi_pts"] - hs["lo_pts"]) / 2
                if half > 0:
                    R["ratio"].append(abs(move - (hs["hi_pts"] + hs["lo_pts"]) / 2) / half)
                if max(hs["up"], hs["down"]) >= 0.5:
                    R["conf_hit"].append((("up" if hs["up"] >= hs["down"] else "down") == actual))
    out = {"days": len(days), "from": days[0].isoformat() if days else None, "to": days[-1].isoformat() if days else None,
           "checkpoints": list(checkpoints), "horizons": {}}
    for hz, R in res.items():
        n = len(R["brier"])
        if not n:
            continue
        b, b0 = float(np.mean(R["brier"])), float(np.mean(R["brier_base"]))
        out["horizons"][hz] = {"n": n, "skill_pct": round((1 - b / b0) * 100, 1) if b0 else None,
                               "hit_pct": round(float(np.mean(R["hit"])) * 100, 1), "hit_base_pct": round(float(np.mean(R["hit_base"])) * 100, 1),
                               "range_inside_pct": round(float(np.mean(R["inside"])) * 100, 1),
                               "band_mult": round(float(np.quantile(R["ratio"], 0.8)), 3) if R["ratio"] else 1.0,
                               "confident_n": len(R["conf_hit"]),
                               "confident_hit_pct": round(float(np.mean(R["conf_hit"])) * 100, 1) if R["conf_hit"] else None}
    return out


# ---------------------------------------------------------------- live service
class Scenarios:
    def __init__(self, engine):
        self.e = engine
        self.dir = Path(engine.data_dir) / "history"
        self.libs = {}
        self.loaded_at = {}
        self.eval = {}
        self.lock = threading.Lock()
        self.cache = {}
        self.busy = False
        self._load_eval()

    def _file(self, sym):
        return self.dir / sym / "minutes.parquet"

    def _load_eval(self):
        f = Path(self.e.data_dir) / "lab" / "scenario_eval.json"
        if f.exists():
            try:
                self.eval = json.loads(f.read_text())
            except Exception:
                self.eval = {}

    def _vix_map(self):
        f = self.dir / "INDIAVIX" / "daily.parquet"
        if not f.exists():
            return {}
        import pandas as pd
        v = pd.read_parquet(f)
        return {pd.Timestamp(d).date(): float(o) for d, o in zip(v["date"], v["open"])}

    def lib(self, sym) -> Library | None:
        f = self._file(sym)
        if sym not in SYMS or not f.exists():
            return None
        mt = f.stat().st_mtime
        if self.loaded_at.get(sym) != mt:
            with self.lock:
                if self.loaded_at.get(sym) != mt:
                    import pandas as pd
                    self.libs[sym] = Library(sym, pd.read_parquet(f), self._vix_map())
                    self.loaded_at[sym] = mt
        return self.libs.get(sym)

    def status(self) -> dict:
        out = {}
        for s in SYMS:
            f = self._file(s)
            lib = self.libs.get(s)
            out[s] = {"have": f.exists(), "days": len(lib.days) if lib else None,
                      "from": lib.days[0].isoformat() if lib and lib.days else None,
                      "to": lib.days[-1].isoformat() if lib and lib.days else None}
        return {"symbols": out, "eval": self.eval, "busy": self.busy}

    def ensure_history(self, token: str, log=print, force=False):
        """Download/extend 1-minute index history (Upstox v3, from Jan 2022). A few hundred requests the first time."""
        if self.busy:
            return
        self.busy = True
        try:
            import pandas as pd
            from lab.upstox_hist import UpstoxHistory, _frame
            up = UpstoxHistory(token)
            end = dt.datetime.now(IST).date() - dt.timedelta(days=1)
            for sym in SYMS:
                f = self._file(sym)
                old = pd.read_parquet(f) if f.exists() else None
                start = dt.date(2022, 1, 1)
                if old is not None and len(old):
                    last = dt.datetime.fromtimestamp(float(old["ts"].max()), IST).date()
                    if last >= end and not force:
                        continue
                    start = last + dt.timedelta(days=1)
                if start > end:
                    continue
                log(f"Scenario engine: downloading {sym} 1-minute history {start} → {end}")
                rows = up.index_minutes(sym, start, end)
                new = _frame(rows, ("ts", "open", "high", "low", "close", "volume"))
                df = pd.concat([old, new]) if old is not None else new
                df = df.drop_duplicates("ts").sort_values("ts")
                f.parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(f, index=False)
                log(f"Scenario engine: {sym} history has {df['ts'].nunique():,} minutes")
            # VIX daily for context
            vf = self.dir / "INDIAVIX" / "daily.parquet"
            if not vf.exists() or time.time() - vf.stat().st_mtime > 86400:
                from lab.upstox_hist import _save_daily
                _save_daily(up, self.dir, "INDIAVIX", dt.date(2022, 1, 1), end, log)
            for sym in SYMS:
                self.lib(sym)
        finally:
            self.busy = False

    def run_eval(self, log=print):
        out = {}
        for sym in SYMS:
            lib = self.lib(sym)
            if lib and len(lib.days) > 300:
                log(f"Scenario engine: testing on {sym}'s last 250 sessions")
                out[sym] = evaluate(lib, 250)
        if out:
            out["generated"] = dt.datetime.now(IST).isoformat(timespec="seconds")
            out["_ts"] = time.time()
            d = Path(self.e.data_dir) / "lab"
            d.mkdir(parents=True, exist_ok=True)
            (d / "scenario_eval.json").write_text(json.dumps(out))
            self.eval = out
        return out

    def live(self, sym, rows, day: dt.date, prev_close: float | None, vix: float | None) -> dict | None:
        """rows: today's 1-minute bars [[t, o, h, l, c, v], ...]."""
        lib = self.lib(sym)
        if not lib or not rows:
            return None
        t0 = _day_ts(day)
        closes = np.full(N, np.nan)
        for r in rows:
            m = int((r[0] - t0) // 60)
            if 0 <= m < N:
                closes[m] = r[4]
        valid = np.where(~np.isnan(closes))[0]
        if not len(valid):
            return None
        t = int(valid[-1])
        key = (sym, day, t)
        if key in self.cache:
            return self.cache[key]
        import pandas as pd
        c = pd.Series(closes[:t + 1]).ffill().bfill().to_numpy()
        sigma = lib.sigma[lib.index[day]] if day in lib.index else lib.sigma_for(day)
        pc = prev_close or (lib.C[lib.index[day] - 1, -1] if day in lib.index and lib.index[day] > 0 else lib.C[-1, -1])
        f = forecast(lib, c, t, day, pc, sigma, vix)
        if f:
            f["t0"] = t0
            ev = (self.eval or {}).get(sym) or {}
            f["eval"] = ev or None
            # widen/narrow the 80% ranges by what the replay showed they needed
            for hz, hs in f["horizons"].items():
                mlt = ((ev.get("horizons") or {}).get(hz) or {}).get("band_mult")
                if mlt and 0.5 < mlt < 3:
                    mid, half = (hs["hi_pts"] + hs["lo_pts"]) / 2, (hs["hi_pts"] - hs["lo_pts"]) / 2 * mlt
                    hs["lo_pts"], hs["hi_pts"], hs["calibrated"] = round(mid - half, 1), round(mid + half, 1), True
            mlt = ((ev.get("horizons") or {}).get("60") or {}).get("band_mult")
            if mlt and 0.5 < mlt < 3 and f.get("band"):
                sp = f["spot"]
                for j in range(len(f["band"]["lo"])):
                    lo, hi = f["band"]["lo"][j], f["band"]["hi"][j]
                    mid, half = (lo + hi) / 2, (hi - lo) / 2 * mlt
                    f["band"]["lo"][j], f["band"]["hi"][j] = round(mid - half, 2), round(mid + half, 2)
        self.cache = {k: v for k, v in self.cache.items() if k[1] == day and k[2] >= t - 2}
        self.cache[key] = f
        return f
