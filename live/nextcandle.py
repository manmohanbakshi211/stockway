"""Next candle from the smaller ones (gun, 2.39): the Fortune chart's shadow candle.

Mandeep's cascade: once 3 one-minute candles of a 5-minute candle have formed, say how that 5-minute candle will end
(open, high, low, close, and the chance it ends green); after 2 five-minute candles, the 15-minute one; after 3
fifteen-minute candles, the hour; after 3 hours, the day. The pairs and their 'after' counts are bullets in
rules/fortune.json ("cascade").

How: every past candle of the same kind is cut at the same point (k smaller candles done) and described by the same
facts (its move so far, how far it stretched, where it sits in its range, the last small candle, volume against
normal, distance from VWAP, the trend on that timeframe, the day's move, the market's move in the same minutes). The
most similar past moments (nearest neighbours, facts scaled to the symbol's ATR) say how such candles ended: the
middle of their closes is the shadow candle's close, their 16th-84th percentile its 68% box, their usual stretch its
high / low, and the share that ended green its chance of green. Only earlier days are used for a day's predictions.

Honest: before it is shown, every pair is tested on the latest days the history never saw (direction hit rate against
a coin flip and against 'it keeps going the way it started', close error against 'it stays where it is', how often
the real close landed in the 68% box). Live, every prediction is graded when its candle closes and kept in the
nextcandle table with the facts that have no history yet (buying / order flow, OI, PCR, news, event risk), so a later
challenger can learn from them. Information only: never a signal, never a trade.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import threading
import time

import numpy as np

from .clock import IST
from .fortune import FILE as FORTUNE_FILE, INTRA, IST_SHIFT, _open_ts, atr_before, groups, ohlc

SUB_MIN = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1H": 60}
TF_MIN = {**SUB_MIN, "1D": 375}
FEATURES = ["move", "up", "dn", "pos", "last", "prev_sub", "relvol", "vwap", "trend", "slope", "prev_body", "tod", "daymove", "mkt"]
FEATURE_NAMES = {"move": "move so far", "up": "stretch up so far", "dn": "stretch down so far", "pos": "where it sits in its range",
                 "last": "the last small candle", "prev_sub": "the one before it", "relvol": "volume against normal",
                 "vwap": "distance from VWAP", "trend": "price vs its 20-candle average", "slope": "20 vs 50-candle average",
                 "prev_body": "the previous candle", "tod": "time of day", "daymove": "the day's move so far",
                 "mkt": "the market's move in the same minutes"}
DEFAULTS = {"cascade": [{"tf": "5m", "from": "1m", "after": 3}, {"tf": "15m", "from": "5m", "after": 2},
                        {"tf": "1H", "from": "15m", "after": 3}, {"tf": "1D", "from": "1H", "after": 3}],
            "neighbors": 60, "min_pool": 150, "holdout_share": 0.2, "call_min": 0.55,
            "cascade_days": {"5m": 45, "15m": 90, "1H": 240, "1D": 720},
            "track": ["NIFTY", "BANKNIFTY"], "track_opened": True, "track_in_play": 6,
            "market_ref": {"default": "NIFTY", "NIFTY": "BANKNIFTY"}, "past_shown": 80}
SQL = """CREATE TABLE IF NOT EXISTS nextcandle (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, day TEXT, sym TEXT, tf TEXT, start REAL, k INTEGER, n_sub INTEGER,
  made_ts REAL, pred TEXT, actual TEXT, facts TEXT, right INTEGER, in68 INTEGER, err REAL, naive_err REAL);
CREATE INDEX IF NOT EXISTS nextcandle_day ON nextcandle(source, tf, day);"""
_C: dict = {}


def cfg() -> dict:
    try:
        mt = FORTUNE_FILE.stat().st_mtime
        if _C.get("mt") != mt:
            raw = json.loads(FORTUNE_FILE.read_text())
            _C.update(mt=mt, cfg={**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}, error=None)
    except Exception as e:
        _C.update(cfg=_C.get("cfg") or dict(DEFAULTS), error=str(e)[:160])
    return _C["cfg"]


def pairs() -> dict:
    """target tf -> {"from": sub tf, "after": n} (a broken entry is skipped)."""
    out = {}
    for p in cfg().get("cascade") or []:
        try:
            tf, sub, k = str(p["tf"]), str(p["from"]), int(p["after"])
            if tf in TF_MIN and sub in SUB_MIN and SUB_MIN[sub] < TF_MIN[tf] and k >= 1:
                out[tf] = {"from": sub, "after": k}
        except Exception:
            continue
    return out


def _day(t):
    return dt.datetime.fromtimestamp(t, IST).date()


def _ema(x, n):
    a, out = 2 / (n + 1), np.empty(len(x))
    if not len(x):
        return out
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


# ------------------------------------------------------------------ facts of a candle cut at k small candles
def table(rows: list, tf: str, sub: str, ks, now: float | None = None, mkt: list | None = None, start_from: int = 20) -> dict:
    """rows: bars at the small timeframe [t, o, h, l, c, v] (oldest first). Returns, for every candle of `tf` and every k in
    ks: its facts after k small candles and (for finished candles) how it ended. now: small bars that end after it are
    not done yet, and the candle holding it is the forming one. mkt: the market reference's bars (same small tf)."""
    sm = SUB_MIN[sub] * 60
    if now is not None:
        rows = [r for r in rows if r[0] + sm <= now + 1e-6]
    grps = groups(rows, tf)
    if len(grps) < start_from + 1:
        return {"X": np.zeros((0, len(FEATURES))), "Y": np.zeros((0, 4)), "meta": [], "cands": [], "atr": []}
    cands = np.asarray([ohlc(s, r) for s, r in grps], float)
    atr = atr_before(cands)
    cl = cands[:, 4]
    e20, e50 = _ema(cl, 20), _ema(cl, 50)
    vol = cands[:, 5]
    tf_s = TF_MIN[tf] * 60
    # day open and running VWAP (TWAP when there is no volume, e.g. an index) at each small bar
    day_open, vw = {}, {}
    acc = {}
    for r in rows:
        d = _day(r[0])
        day_open.setdefault(d, r[1])
        pv, v, n, s = acc.get(d, (0.0, 0.0, 0, 0.0))
        tp = (r[2] + r[3] + r[4]) / 3
        pv, v, n, s = pv + tp * (r[5] or 0), v + (r[5] or 0), n + 1, s + tp
        acc[d] = (pv, v, n, s)
        vw[r[0]] = pv / v if v > 0 else s / n
    mk = {}
    if mkt:
        mg = groups([r for r in mkt if now is None or r[0] + sm <= now + 1e-6], tf)
        mc = np.asarray([ohlc(s, r) for s, r in mg], float) if mg else np.zeros((0, 6))
        matr = atr_before(mc) if len(mc) else []
        for j, (s, r) in enumerate(mg):
            mk[s] = (r, matr[j] if j < len(matr) else np.nan)
    X, Y, meta = [], [], []
    last_i = len(grps) - 1
    for i in range(start_from, len(grps)):
        start, subs = grps[i]
        a = atr[i]
        if not a or not np.isfinite(a) or a <= 0:
            continue
        done = (now is None) or (start + tf_s <= now + 1e-6) or i < last_i
        n_sub = len(subs)
        for k in ks:
            if k >= n_sub and done:
                continue                                  # nothing left to predict (or a short last candle)
            if not done and k != n_sub:
                continue                                  # the forming candle: only its current k
            sk = subs[:k]
            if len(sk) < k or k < 1:
                continue
            o, c = sk[0][1], sk[-1][4]
            h, l = max(x[2] for x in sk), min(x[3] for x in sk)
            d = _day(start)
            v_so_far = sum((x[5] or 0) for x in sk)
            base_v = float(np.mean(vol[max(0, i - 20):i])) if i > 0 else 0.0
            exp_k = k / max(1, round(tf_s / sm))
            relvol = (v_so_far / (base_v * exp_k)) if base_v > 0 else 1.0
            mkt_mv = 0.0
            if mk.get(start):
                ms, matr_i = mk[start]
                msk = ms[:k]
                if len(msk) == k and matr_i and np.isfinite(matr_i) and matr_i > 0:
                    mkt_mv = (msk[-1][4] - msk[0][1]) / matr_i
            tod = ((start - _open_ts(d)) / 22500.0) if tf in INTRA else d.weekday() / 4.0
            x = [(c - o) / a, (h - o) / a, (o - l) / a, ((c - l) / (h - l)) if h > l else 0.5,
                 (sk[-1][4] - sk[-1][1]) / a, ((sk[-2][4] - sk[-2][1]) / a) if k >= 2 else 0.0,
                 min(5.0, relvol), (c - vw.get(sk[-1][0], c)) / a,
                 (cl[i - 1] - e20[i - 1]) / a, (e20[i - 1] - e50[i - 1]) / a, (cl[i - 1] - cands[i - 1][1]) / a,
                 tod, (c - day_open.get(d, o)) / a, max(-5.0, min(5.0, mkt_mv))]
            if done:
                H, L, C = cands[i][2], cands[i][3], cands[i][4]
                Y.append([(C - c) / a, max(0.0, H - h) / a, max(0.0, l - L) / a, 1.0 if C > o else 0.0])
            else:
                Y.append([np.nan] * 4)
            X.append(x)
            meta.append({"start": float(start), "day": d.isoformat(), "k": k, "n_sub": n_sub, "o": o, "h": h, "l": l, "c": c,
                         "atr": float(a), "done": bool(done), "i": i})
    return {"X": np.asarray(X, float).reshape(-1, len(FEATURES)), "Y": np.asarray(Y, float).reshape(-1, 4), "meta": meta,
            "cands": cands, "atr": atr}


# ------------------------------------------------------------------ similar past moments
def knn(Xp: np.ndarray, Yp: np.ndarray, x: np.ndarray, k: int) -> dict | None:
    """The k most similar past moments (facts scaled by the pool's spread) and how their candles ended."""
    if len(Xp) < max(10, k // 2):
        return None
    sd = Xp.std(axis=0)
    w = np.where(sd > 1e-9, 1.0 / np.where(sd > 1e-9, sd, 1.0), 0.0)
    d = np.sqrt((((Xp - x) * w) ** 2).sum(axis=1))
    kk = min(k, len(d))
    idx = np.argpartition(d, kk - 1)[:kk]
    Y = Yp[idx]
    r = Y[:, 0]
    return {"n": int(kk), "rest": [float(np.percentile(r, p)) for p in (16, 50, 84)], "up": float(np.median(Y[:, 1])),
            "dn": float(np.median(Y[:, 2])), "green": float(Y[:, 3].mean())}


def candle_from(m: dict, nb: dict) -> dict:
    """The shadow candle: open as it is, close at the similar moments' middle, high / low stretched by their usual stretch."""
    a, c = m["atr"], m["c"]
    close = c + nb["rest"][1] * a
    return {"open": round(m["o"], 2), "close": round(close, 2), "high": round(max(m["h"] + nb["up"] * a, close, m["o"]), 2),
            "low": round(min(m["l"] - nb["dn"] * a, close, m["o"]), 2), "close_lo": round(c + nb["rest"][0] * a, 2),
            "close_hi": round(c + nb["rest"][2] * a, 2), "p_green": round(nb["green"] * 100), "similar": nb["n"]}


def backtest(t: dict, after: int, neighbors: int, share: float, call_min: float) -> dict:
    """Predictions for the latest days (holdout) using only earlier days, at the first prediction point (k = after)."""
    meta, X, Y = t["meta"], t["X"], t["Y"]
    if not meta:
        return {"n": 0}
    days = sorted({m["day"] for m in meta})
    test_days = set(days[-max(1, int(round(len(days) * share))):]) if len(days) >= 5 else set()
    day_arr = np.asarray([m["day"] for m in meta])
    k_arr = np.asarray([m["k"] for m in meta])
    res, shown = [], []
    for j, m in enumerate(meta):
        if m["day"] not in test_days or m["k"] != after or not m["done"]:
            continue
        mask = (day_arr < m["day"]) & (k_arr == after)
        nb = knn(X[mask], Y[mask], X[j], neighbors)
        if nb is None:
            continue
        pc = candle_from(m, nb)
        a, C = m["atr"], m["c"] + Y[j][0] * m["atr"]
        o = m["o"]
        green = C > o
        call = "up" if pc["p_green"] >= call_min * 100 else "down" if pc["p_green"] <= (1 - call_min) * 100 else None
        mom = "up" if m["c"] > o else "down" if m["c"] < o else None
        res.append({"call": call, "right": (call == ("up" if green else "down")) if call else None,
                    "mom_right": (mom == ("up" if green else "down")) if mom else None,
                    "err": abs(pc["close"] - C) / a, "naive": abs(m["c"] - C) / a, "in68": pc["close_lo"] <= C <= pc["close_hi"]})
        shown.append({"time": int(m["start"] + IST_SHIFT), **pc, "actual_close": round(C, 2), "right": res[-1]["right"]})
    return {**summary(res), "test_days": len(test_days), "first_test_day": min(test_days) if test_days else None, "shown": shown}


def summary(res: list) -> dict:
    if not res:
        return {"n": 0}
    calls = [r for r in res if r["call"]]
    hit = [r["right"] for r in calls]
    mom = [bool(r["mom_right"]) for r in calls if r.get("mom_right") is not None]   # the plain rule, on the same candles
    err, naive = float(np.mean([r["err"] for r in res])), float(np.mean([r["naive"] for r in res]))
    n = len(hit)
    p = (sum(hit) / n) if n else None
    pm = (sum(mom) / len(mom)) if mom else None
    se = math.sqrt(max(p * (1 - p), 0.05) / n) if n else None
    bar = pm if pm is not None else 0.5             # the edge must beat 'it keeps the colour it has so far', not a coin flip
    return {"n": len(res), "calls": n, "hit_pct": round(p * 100) if p is not None else None,
            "edge": bool(p is not None and n >= 30 and p - bar > 2 * se), "mom_pct": round(pm * 100) if pm is not None else None,
            "extra_pts": round((p - bar) * 100) if p is not None else None,
            "err_atr": round(err, 2), "naive_atr": round(naive, 2), "skill_pct": round((1 - err / naive) * 100) if naive > 0 else None,
            "in68_pct": round(np.mean([r["in68"] for r in res]) * 100)}


def words(tf: str, sub: str, after: int, s: dict, where: str) -> list:
    """The smart report: what the numbers mean, in plain words."""
    if not s or not s.get("n"):
        return [f"{tf}: nothing graded {where} yet."]
    out = []
    if s.get("calls"):
        if s.get("mom_pct") is not None:
            verdict = (f"the other facts add {s['extra_pts']} points: a real edge so far" if s.get("edge") else
                       f"the other facts add {s['extra_pts']:+d} points: nothing reliable beyond what the chart already shows")
            out.append(f"{tf} after {after} × {sub}: green / red called right {s['hit_pct']}% of {s['calls']} times {where}. "
                       f"The plain rule 'it keeps the colour it has so far' was right {s['mom_pct']}% on the same candles: {verdict}.")
        else:
            out.append(f"{tf} after {after} × {sub}: green / red called right {s['hit_pct']}% of {s['calls']} times {where}.")
    else:
        out.append(f"{tf}: no direction call {where}: the similar moments were split.")
    if s.get("skill_pct") is not None:
        better = s["skill_pct"] > 0
        out.append(f"Close off by {s['err_atr']} ATR on average vs {s['naive_atr']} if you assume it stays put: "
                   f"{abs(s['skill_pct'])}% {'better' if better else 'worse'}.")
    if s.get("in68_pct") is not None:
        ok = 55 <= s["in68_pct"] <= 80
        out.append(f"The real close landed inside the 68% box {s['in68_pct']}% of the time{'' if ok else ' (should be about 68%)'}"
                   f"{': an honest range' if ok else ''}.")
    return out


# ------------------------------------------------------------------ the live tracker
class NextCandle:
    """Builds each (symbol, timeframe)'s pool of past moments once a day (in its own thread), predicts the forming
    candle as its small candles close, grades every prediction when the candle closes, and serves the chart / report."""

    def __init__(self, engine):
        self.e = engine
        self.lock = threading.Lock()
        self.pools: dict = {}                 # (sym, tf, day) -> {"t": table, "bt": backtest, "built": ts} | {"error"}
        self.building: set = set()
        self.queue: list = []
        self.cur: dict = {}                   # (sym, tf) -> {"start", "k", "pred", "first", "facts"}
        self.opened: dict = {}                # sym -> last time its chart was opened today
        self.status = {"predicted": 0, "graded": 0, "errors": {}}
        self._worker = None
        try:
            with engine.store.lock:
                engine.store.db.executescript(SQL)
                engine.store.db.commit()
        except Exception:
            pass

    # ---------------- data
    def _now(self) -> float:
        try:
            if self.e.running and self.e.clock:
                return self.e.clock.ts()
        except Exception:
            pass
        return time.time()

    def _today_rows(self, sym, sub) -> list:
        try:
            live = self.e.charts.today_bars(sym) if (self.e.metrics or {}).get(sym) else []
        except Exception:
            live = []
        if not live:
            return []
        from .mtf import resample
        d0 = _open_ts(_day(live[-1][0]))
        live = [list(r[:6]) for r in live if d0 <= r[0] < d0 + 6.5 * 3600]
        return live if SUB_MIN[sub] == 1 else resample(live, SUB_MIN[sub])

    def _rows(self, sym, sub, days, today: str) -> list:
        """History at the small timeframe (Dhan / Yahoo / the app's own) before today, plus today's live bars."""
        mtf = getattr(self.e, "mtf", None)
        if mtf is None:
            from .mtf import MTF
            mtf = self.e.mtf = MTF(self.e)
        base = SUB_MIN[sub]
        rows, _ = mtf.long_rows(sym, base, days)
        hist = [list(r[:6]) for r in (rows or []) if _day(r[0]).isoformat() < today]
        return hist + self._today_rows(sym, sub)

    def _mkt_sym(self, sym) -> str | None:
        ref = cfg().get("market_ref") or {}
        m = ref.get(sym, ref.get("default"))
        return m if m and m != sym else None

    # ---------------- pools (once a day, in the background)
    def pool(self, sym, tf, today: str, wait: bool = False) -> dict | None:
        k = (sym, tf, today)
        with self.lock:
            p = self.pools.get(k)
            if p is None and k not in self.building:
                self.building.add(k)
                self.queue.append(k)
        if p is None:
            self._kick()
            if wait:
                t0 = time.time()
                while time.time() - t0 < 120:
                    with self.lock:
                        p = self.pools.get(k)
                    if p is not None:
                        break
                    time.sleep(0.2)
        return p

    def _kick(self):
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._work, daemon=True, name="nextcandle-pools")
            self._worker.start()

    def _work(self):
        while True:
            with self.lock:
                if not self.queue:
                    self._worker = None
                    return
                k = self.queue.pop(0)
            try:
                p = self._build(*k)
            except Exception as ex:
                p = {"error": str(ex)[:160], "built": time.time()}
            with self.lock:
                if len(self.pools) > 120:
                    self.pools.clear()
                self.pools[k] = p
                self.building.discard(k)

    def _build(self, sym, tf, today) -> dict:
        pr = pairs().get(tf)
        if not pr:
            return {"error": f"{tf} is not in the cascade", "built": time.time()}
        c = cfg()
        sub, after = pr["from"], pr["after"]
        days = int((c.get("cascade_days") or {}).get(tf, 60))
        rows = [r for r in self._rows(sym, sub, days, today) if _day(r[0]).isoformat() < today]
        ms = self._mkt_sym(sym)
        mrows = [r for r in self._rows(ms, sub, days, today) if _day(r[0]).isoformat() < today] if ms else None
        n_sub = max(2, round(TF_MIN[tf] / SUB_MIN[sub]))
        ks = list(range(after, n_sub + 1))
        t = table(rows, tf, sub, ks, mkt=mrows)
        bt = backtest(t, after, int(c["neighbors"]), float(c["holdout_share"]), float(c["call_min"]))
        return {"t": t, "bt": bt, "built": time.time(), "sub": sub, "after": after, "mkt": ms, "rows": len(rows)}

    # ---------------- live: predict as small candles close, grade when the candle closes
    def tracked(self) -> list:
        c = cfg()
        out = list(c.get("track") or [])
        now = time.time()
        if c.get("track_opened", True):
            out += [s for s, t in self.opened.items() if now - t < 6 * 3600]
        n = int(c.get("track_in_play") or 0)
        if n:
            out += sorted(getattr(self.e, "in_play", None) or [])[:n]
        uni = getattr(getattr(self.e, "uni", None), "underlyings", None) or {}
        seen, res = set(), []
        for s in out:
            if s not in seen and (not uni or s in uni):
                seen.add(s)
                res.append(s)
        return res

    def tick(self, now: float | None = None):
        """Called every few seconds by the scanner loop: cheap (pools are built in their own thread)."""
        now = now or self._now()
        today = _day(now).isoformat()
        try:
            self.cast().ensure()
        except Exception as ex:
            self.status["errors"]["cast"] = str(ex)[:120]
        for sym in self.tracked():
            for tf in pairs():
                try:
                    self._step(sym, tf, now, today)
                except Exception as ex:
                    self.status["errors"][f"{sym} {tf}"] = str(ex)[:120]
                try:                                     # 2.41: the candle forecast, isolated (it never stops the cascade)
                    self._cast_step(sym, tf, now, today)
                except Exception as ex:
                    self.status["errors"][f"cast {sym} {tf}"] = str(ex)[:120]

    def cast(self):
        """2.41: the candle forecast (live/candlecast.py): colour with a strength ladder, shape, patterns, 90% zone."""
        if getattr(self, "_cast", None) is None:
            from .candlecast import Cast
            self._cast = Cast(self.e)
        return self._cast

    def _cast_step(self, sym, tf, now, today):
        from . import candlecast as CC
        cc = CC.cfg()
        if not cc.get("on", True) or sym not in cc["symbols"]:
            return
        sub = pairs()[tf]["from"]
        live_rows = self._today_rows(sym, sub)
        if not live_rows:
            return
        need = max(60, 3 * round(TF_MIN[tf] / SUB_MIN[sub]) * 30)
        tail = self._hist_tail(sym, sub, today, need)
        ms = self._mkt_sym(sym)
        mrows = (self._hist_tail(ms, sub, today, need) + self._today_rows(ms, sub)) if ms else None
        self.cast().step(sym, tf, sub, tail + live_rows, mrows, now, today)

    def _step(self, sym, tf, now, today):
        p = self.pool(sym, tf, today)
        if not p or p.get("error") or not len(p["t"]["X"]):
            return
        pr = pairs()[tf]
        sub, after = pr["from"], pr["after"]
        k_cur = self.cur.get((sym, tf))
        # today's facts (history tail for context + today's small bars)
        live_rows = self._today_rows(sym, sub)
        if not live_rows:
            return
        need = max(60, 3 * round(TF_MIN[tf] / SUB_MIN[sub]) * 30)
        tail = self._hist_tail(sym, sub, today, need)
        ms = p.get("mkt")
        mrows = (self._hist_tail(ms, sub, today, need) + self._today_rows(ms, sub)) if ms else None
        t = table(tail + live_rows, tf, sub, list(range(after, 64)), now=now, mkt=mrows,
                  start_from=max(1, len(groups(tail, tf)) - 2))
        forming = [(j, m) for j, m in enumerate(t["meta"]) if not m["done"]]
        # grade candles that have finished since their prediction
        if k_cur and (not forming or forming[-1][1]["start"] != k_cur["start"]):
            fin = next((m for m in t["meta"] if m["done"] and m["start"] == k_cur["start"]), None)
            cands = t["cands"]
            if fin is not None:
                i = fin["i"]
                self._grade(sym, tf, k_cur, {"open": float(cands[i][1]), "high": float(cands[i][2]), "low": float(cands[i][3]),
                                              "close": float(cands[i][4])}, today)
            self.cur.pop((sym, tf), None)
        if not forming:
            return
        j, m = forming[-1]
        if m["k"] < after:
            return
        if k_cur and k_cur["start"] == m["start"] and k_cur["k"] == m["k"]:
            return                                       # nothing new since the last small candle
        T = p["t"]
        mask = np.asarray([x["k"] == m["k"] for x in T["meta"]])
        nb = knn(T["X"][mask], T["Y"][mask], t["X"][j], int(cfg()["neighbors"]))
        if nb is None:
            return
        pred = {**candle_from(m, nb), "k": m["k"], "n_sub": round(TF_MIN[tf] / SUB_MIN[sub]), "made": now,
                "so_far": round(m["c"], 2)}
        first = k_cur["first"] if k_cur and k_cur["start"] == m["start"] else dict(pred)
        facts = self._facts(sym, now)
        self.cur[(sym, tf)] = {"start": m["start"], "k": m["k"], "pred": pred, "first": first, "facts": facts,
                               "atr": m["atr"], "c": m["c"], "o": m["o"]}
        self.status["predicted"] += 1

    def _hist_tail(self, sym, sub, today, n) -> list:
        key = ("tail", sym, sub, today)
        hit = self.pools.get(key)
        if hit is None:
            mtf = getattr(self.e, "mtf", None)
            rows = []
            if mtf is not None:
                try:
                    days = max(3, int(n * SUB_MIN[sub] / 375) + 3)
                    rows, _ = mtf.long_rows(sym, SUB_MIN[sub], days)
                except Exception:
                    rows = []
            hit = [list(r[:6]) for r in rows if _day(r[0]).isoformat() < today][-n:]
            self.pools[key] = hit
        return hit

    def _facts(self, sym, now) -> dict:
        """Facts with no history yet (order flow, OI, PCR, news, event risk...): recorded for a later challenger."""
        f = getattr(self.e, "signal_facts", None)
        try:
            raw = f(sym, "up", dt.datetime.fromtimestamp(now, IST)) if callable(f) else {}
        except Exception:
            raw = {}
        return {k: v for k, v in (raw or {}).items() if isinstance(v, (int, float, bool)) or v is None}

    def _grade(self, sym, tf, cur, act, today):
        rows = []
        for which in ("first", "pred"):
            pr = cur.get(which)
            if not pr:
                continue
            if which == "pred" and cur["first"] and pr["k"] == cur["first"]["k"]:
                continue                                  # only one prediction was made for this candle
            green = act["close"] > act["open"]
            call = "up" if pr["p_green"] >= cfg()["call_min"] * 100 else "down" if pr["p_green"] <= (1 - cfg()["call_min"]) * 100 else None
            a = cur["atr"] or 1.0
            rows.append((self.e.source, today, sym, tf, cur["start"], pr["k"], pr["n_sub"], pr["made"], json.dumps(pr), json.dumps(act),
                         json.dumps(cur.get("facts") or {}), None if not call else int(call == ("up" if green else "down")),
                         int(pr["close_lo"] <= act["close"] <= pr["close_hi"]), round(abs(pr["close"] - act["close"]) / a, 3),
                         round(abs(cur["c"] - act["close"]) / a, 3) if which == "first" else None))
        if not rows:
            return
        with self.e.store.lock:
            self.e.store.db.executemany("INSERT INTO nextcandle(source, day, sym, tf, start, k, n_sub, made_ts, pred, actual, facts, "
                                        "right, in68, err, naive_err) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            self.e.store.db.commit()
        self.status["graded"] += len(rows)

    # ---------------- the chart and the smart report
    def live_stats(self, tf, source=None, sym=None, day=None) -> dict:
        q = "SELECT right, in68, err, naive_err, k, pred, actual FROM nextcandle WHERE source=? AND tf=?"
        args = [source or self.e.source or "upstox", tf]
        if sym:
            q += " AND sym=?"
            args.append(sym)
        if day:
            q += " AND day=?"
            args.append(day)
        try:
            with self.e.store.lock:
                rs = self.e.store.db.execute(q, args).fetchall()
        except Exception:
            rs = []
        pr = pairs().get(tf) or {}
        res = []
        for r in rs:
            if r[4] != pr.get("after") or r[3] is None:
                continue
            p_, a_ = json.loads(r[5]) if r[5] else {}, json.loads(r[6]) if r[6] else {}
            so, o_ = p_.get("so_far"), p_.get("open")
            mom = None
            if so is not None and o_ is not None and so != o_ and a_.get("close") is not None:
                mom = (so > o_) == (a_["close"] > a_["open"])
            res.append({"call": "y" if r[0] is not None else None, "right": bool(r[0]) if r[0] is not None else None,
                        "mom_right": mom, "err": r[2], "naive": r[3], "in68": bool(r[1])})
        return summary(res)

    def recent(self, sym, tf, n=12) -> list:
        try:
            with self.e.store.lock:
                rs = self.e.store.db.execute("SELECT start, k, pred, actual, right, in68 FROM nextcandle WHERE sym=? AND tf=? "
                                             "ORDER BY start DESC, k LIMIT ?", (sym, tf, n)).fetchall()
        except Exception:
            rs = []
        out = []
        for s, k, p, a, r, i in rs:
            p, a = json.loads(p), json.loads(a)
            out.append({"time": int(s + IST_SHIFT), "k": k, "pred": p, "actual": a, "right": None if r is None else bool(r),
                        "in68": bool(i)})
        return out

    def graded(self, sym, tf, k, n=400) -> list:
        """2.40.1: every live prediction made at k small candles (the first call), oldest first, with the real close: the
        Fortune chart's own market from live predictions."""
        try:
            with self.e.store.lock:
                rs = self.e.store.db.execute("SELECT start, pred, actual FROM nextcandle WHERE sym=? AND tf=? AND k=? "
                                             "ORDER BY start DESC LIMIT ?", (sym, tf, k, n)).fetchall()
        except Exception:
            rs = []
        out = []
        for st, p, a in reversed(rs):
            p, a = json.loads(p), json.loads(a)
            out.append({"time": int(st + IST_SHIFT), **{x: p.get(x) for x in ("open", "high", "low", "close", "close_lo", "close_hi",
                                                                               "p_green", "similar")},
                        "actual_close": a.get("close"), "live": True})
        return out

    def view(self, sym, tf) -> dict:
        """The cascade's view plus the 2.41 candle forecast (its own block: a failure there never hides the chart)."""
        out = self._view(sym, tf)
        if pairs().get(tf):
            try:
                out["cast"] = self.cast().view(sym, tf)
            except Exception as ex:
                out["cast"] = {"on": True, "status": "error", "error": str(ex)[:160]}
        return out

    def _view(self, sym, tf) -> dict:
        """Everything the Fortune chart needs for the shadow next candle and the smart report (cheap: no building here)."""
        pr = pairs().get(tf)
        if not pr:
            return {"cascade": False}
        self.opened[sym] = time.time()
        now = self._now()
        today = _day(now).isoformat()
        p = self.pool(sym, tf, today)
        out = {"cascade": True, "tf": tf, "from": pr["from"], "after": pr["after"],
               "n_sub": round(TF_MIN[tf] / SUB_MIN[pr["from"]]), "factors": [FEATURE_NAMES[f] for f in FEATURES],
               "recorded": ["buying / order flow", "OI change", "PCR", "news", "event risk", "breadth", "VIX"]}
        if p is None:
            return {**out, "status": "building"}
        if p.get("error"):
            return {**out, "status": "error", "error": p["error"]}
        cur = self.cur.get((sym, tf))
        if cur and self.e.running:
            out["now"] = {"time": int(cur["start"] + IST_SHIFT), **cur["pred"]}
        bt = p["bt"] or {}
        shown = (bt.get("shown") or [])[-int(cfg().get("past_shown") or 80):]
        live = self.live_stats(tf, sym=sym)
        today_s = self.live_stats(tf, sym=sym, day=today)
        rep = words(tf, pr["from"], pr["after"], bt, f"on {bt.get('test_days', 0)} days it never saw")
        if live.get("n"):
            rep += words(tf, pr["from"], pr["after"], live, "live")
        lv = self.graded(sym, tf, pr["after"])
        seen = {x["time"] for x in lv}
        shown = [x for x in shown if x["time"] not in seen] + lv          # unseen days, then every live first call
        out.update(status="ready", past=shown, backtest={k: v for k, v in bt.items() if k != "shown"}, live=live, today=today_s,
                   report=rep, recent=self.recent(sym, tf), pool=len(p["t"]["meta"]), history_rows=p.get("rows"),
                   market_ref=p.get("mkt"))
        return out
