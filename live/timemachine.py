"""Time Machine: before you take a trade, replay the rest of the day 10,000 times and check the real past.

Idea from the open-source day-trader-skill (MIT), rebuilt for NSE: 5-minute bars 09:15-15:30 (75 a day),
India time, square-off at 15:15, and Upstox history for any F&O stock or index.

1. The pool: the symbol's own full sessions from the last ~60 trading days. Every 5-minute bar becomes four
   ratios to the bar before it (open, high, low and close over the previous close).
2. One replay: start at the entry price; for each bar left until square-off, take a bar from a random past
   session at the same time of day (plus or minus 2 bars) and scale it to the current price. The stop is checked
   before the target inside a bar (the cautious choice).
3. 10,000 replays give: target first, stop first, neither (still open at square-off), and the spread of prices.
4. The real past: the same plan geometry (same time of day, same stop and target distances) on each past session.
5. The verdict weighs the reward:risk: a 2R target only needs to come first 33% of the time to break even.
It is a simulation from recent behaviour, not a prediction: news, results and events can do things the last
60 days never did.
"""
from __future__ import annotations

import datetime as dt
import math
import time
import urllib.parse
from pathlib import Path

import numpy as np
import pandas as pd

from .clock import IST

BARS = 75                     # 09:15 .. 15:25 bar starts
FLAT_BAR = 72                 # the 15:15 bar: square-off
WINDOW = 2
REPLAYS = 10_000
SEED = 19


def _bar_of(ts_or_dt) -> int:
    t = ts_or_dt if isinstance(ts_or_dt, dt.datetime) else dt.datetime.fromtimestamp(ts_or_dt, IST)
    return int(((t.hour * 60 + t.minute) - 555) // 5)


def sessions_from_minutes(df: pd.DataFrame) -> tuple[list, np.ndarray]:
    """1-minute (or 5-minute) rows ts/open/high/low/close -> full sessions as an array (n, 75, 4) of o,h,l,c."""
    df = df.sort_values("ts").drop_duplicates("ts")
    t = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    df = df.assign(date=t.dt.date, bar=((t.dt.hour * 60 + t.dt.minute - 555) // 5).astype(int))
    df = df[(df["bar"] >= 0) & (df["bar"] < BARS)]
    g = df.groupby(["date", "bar"]).agg(o=("open", "first"), h=("high", "max"), l=("low", "min"), c=("close", "last")).reset_index()
    days, arr = [], []
    for d, x in g.groupby("date"):
        if len(x) != BARS:
            continue                       # half days, gaps, halts: left out
        days.append(d)
        arr.append(x.sort_values("bar")[["o", "h", "l", "c"]].to_numpy(dtype=float))
    return days, (np.array(arr) if arr else np.zeros((0, BARS, 4)))


def run(sess: np.ndarray, days: list, *, entry: float, stop: float, target: float, direction: str, start_bar: int,
        end_bar: int = FLAT_BAR, replays: int = REPLAYS, seed: int = SEED) -> dict:
    """sess: (n, 75, 4) past full sessions (before the trade's day)."""
    d = 1 if direction == "up" else -1
    risk = abs(entry - stop)
    if risk <= 0 or (d > 0 and not stop < entry < target) or (d < 0 and not target < entry < stop):
        raise ValueError("The plan does not make sense: the stop and the target must be on opposite sides of the entry.")
    n = len(sess)
    if n < 20:
        raise ValueError(f"Only {n} full sessions of history: need at least 20.")
    start_bar = max(1, min(start_bar, BARS - 1))
    end_bar = max(start_bar, min(end_bar, BARS - 1))
    r_mult = abs(target - entry) / risk
    # ratios to the previous bar's close
    prev_c = np.concatenate([sess[:, :1, 0], sess[:, :-1, 3]], axis=1)          # first bar: its own open
    ratio = sess / prev_c[:, :, None]
    rng = np.random.default_rng(seed)
    nb = end_bar - start_bar + 1
    bars = np.arange(start_bar, end_bar + 1)
    pick_s = rng.integers(0, n, size=(replays, nb))
    pick_b = np.clip(bars[None, :] + rng.integers(-WINDOW, WINDOW + 1, size=(replays, nb)), 1, BARS - 1)
    R = ratio[pick_s, pick_b]                                                       # replays x nb x 4
    closes = entry * np.cumprod(R[:, :, 3], axis=1)
    prev = np.concatenate([np.full((replays, 1), entry), closes[:, :-1]], axis=1)
    highs, lows = prev * R[:, :, 1], prev * R[:, :, 2]
    if d > 0:
        hit_stop, hit_tgt = lows <= stop, highs >= target
    else:
        hit_stop, hit_tgt = highs >= stop, lows <= target
    first_stop = np.where(hit_stop.any(1), hit_stop.argmax(1), nb + 1)
    first_tgt = np.where(hit_tgt.any(1), hit_tgt.argmax(1), nb + 1)
    stop_first = first_stop <= first_tgt
    stop_first &= first_stop <= nb
    tgt_first = (first_tgt < first_stop) & (first_tgt <= nb)
    neither = ~(stop_first | tgt_first)
    end_px = closes[:, -1]
    r_end = np.where(tgt_first, r_mult, np.where(stop_first, -1.0, (end_px - entry) * d / risk))
    q = np.percentile(end_px, [5, 25, 50, 75, 95])
    fan = np.percentile(closes, [5, 50, 95], axis=0)
    mc = {"replays": replays, "seed": seed, "bars": int(nb), "target_first": round(float(tgt_first.mean() * 100), 1),
          "stop_first": round(float(stop_first.mean() * 100), 1), "neither": round(float(neither.mean() * 100), 1),
          "green": round(float(((tgt_first) | (neither & ((end_px - entry) * d > 0))).mean() * 100), 1),
          "avg_r": round(float(r_end.mean()), 3), "close_pct": [round(float(x), 2) for x in q],
          "fan": {"p5": [round(float(x), 2) for x in fan[0]], "p50": [round(float(x), 2) for x in fan[1]], "p95": [round(float(x), 2) for x in fan[2]]}}
    # the real past: same time of day, same distances
    up_t, dn_t = (target / entry - 1), (stop / entry - 1)
    hist = {"target": 0, "stop": 0, "neither": 0, "r": []}
    for s in range(n):
        e = sess[s, start_bar - 1, 3]
        tg, sp = e * (1 + up_t), e * (1 + dn_t)
        res = None
        for j in range(start_bar, end_bar + 1):
            h_, l_ = sess[s, j, 1], sess[s, j, 2]
            if (d > 0 and l_ <= sp) or (d < 0 and h_ >= sp):
                res = ("stop", -1.0)
                break
            if (d > 0 and h_ >= tg) or (d < 0 and l_ <= tg):
                res = ("target", r_mult)
                break
        if res is None:
            res = ("neither", (sess[s, end_bar, 3] - e) * d / abs(e - sp))
        hist[res[0]] += 1
        hist["r"].append(res[1])
    hist_out = {"sessions": n, "from": str(days[0]) if days else None, "to": str(days[-1]) if days else None,
                "target": hist["target"], "stop": hist["stop"], "neither": hist["neither"],
                "avg_r": round(float(np.mean(hist["r"])), 3)}
    return {"plan": {"entry": entry, "stop": stop, "target": target, "direction": direction, "r": round(r_mult, 2),
                     "start_bar": start_bar, "end_bar": end_bar},
            "mc": mc, "history": hist_out, "verdict": verdict(mc, r_mult, hist_out)}


def verdict(mc, r_mult, hist) -> dict:
    sided = mc["target_first"] + mc["stop_first"]
    need = 100 / (1 + r_mult)                       # % of decided replays the target must win to break even
    got = mc["target_first"] / sided * 100 if sided else 0.0
    gap = got - need
    if not sided:
        line = "Almost no replay reached the stop or the target: the close decides this trade."
    elif abs(gap) < 5:
        line = (f"Close to a coin flip" if abs(r_mult - 1) < 0.05 else "Close to break-even") + \
               f": the target came first in {got:.0f}% of decided replays; it needs {need:.0f}%."
    elif gap > 0:
        line = ("Strongly toward the target" if gap >= 15 else "Leans toward the target") + \
               f": {got:.0f}% of decided replays reached the target first (break-even {need:.0f}%)."
    else:
        line = ("Strongly toward the stop" if gap <= -15 else "Leans toward the stop") + \
               f": only {got:.0f}% of decided replays reached the target first (break-even {need:.0f}%)."
    notes = []
    if mc["neither"] > 40:
        notes.append(f"{mc['neither']:.0f}% of replays hit neither by square-off: the 15:15 price decides those.")
    if (mc["avg_r"] > 0) != (gap > 0) and sided:
        notes.append(f"Average result {mc['avg_r']:+.2f}R points the other way, because of the replays that hit neither.")
    hd = hist["target"] + hist["stop"]
    if hd >= 10:
        hg = hist["target"] / hd * 100
        if (hg - need > 5) != (gap > 5) or (hg - need < -5) != (gap < -5):
            notes.append(f"The real past disagrees: target first in {hg:.0f}% of {hd} decided sessions.")
    grade = "edge" if gap >= 5 and mc["avg_r"] > 0 else "against" if gap <= -5 and mc["avg_r"] < 0 else "flip"
    return {"line": line, "notes": notes, "grade": grade, "needed_pct": round(need, 1), "got_pct": round(got, 1)}


class TimeMachine:
    """Loads (and caches for the day) a symbol's last ~60 sessions of 5-minute bars, then runs plans."""

    def __init__(self, engine):
        self.e = engine
        self.dir = Path(engine.data_dir) / "history" / "tm"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.mem = {}

    def history(self, sym: str, day: dt.date):
        k = (sym, day)
        if k in self.mem:
            return self.mem[k]
        f = self.dir / f"{sym}.parquet"
        df = None
        if f.exists() and dt.datetime.fromtimestamp(f.stat().st_mtime, IST).date() == day:
            df = pd.read_parquet(f)
        if df is None:
            df = self._fetch(sym, day)
            if df is not None and len(df):
                df.to_parquet(f, index=False)
        if df is None:
            mf = Path(self.e.data_dir) / "history" / sym / "minutes.parquet"     # indices: the scenario library
            if mf.exists():
                df = pd.read_parquet(mf)
                df = df[df["ts"] >= df["ts"].max() - 120 * 86400]
        if df is None or df.empty:
            raise ValueError("No history for this symbol: log in to Upstox (the Time Machine reads its 5-minute bars).")
        t = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
        days, sess = sessions_from_minutes(df[t < day])
        days, sess = days[-60:], sess[-60:]
        self.mem = {kk: v for kk, v in self.mem.items() if kk[1] == day}
        self.mem[k] = (days, sess)
        return days, sess

    def _fetch(self, sym, day):
        tok = self.e.tokens.load() if getattr(self.e, "tokens", None) else None
        if not tok or not self.e.uni:
            return None
        u = self.e.uni.underlyings.get(sym)
        if not u:
            return None
        from lab.upstox_hist import UpstoxHistory, _frame
        up = UpstoxHistory(tok["access_token"])
        q = urllib.parse.quote(u.key, safe="")
        rows = []
        end = day - dt.timedelta(days=1)
        for k in range(4):                          # about 4 months, one month a request
            to = end - dt.timedelta(days=30 * k)
            frm = to - dt.timedelta(days=29)
            d = up.get(f"/v3/historical-candle/{q}/minutes/5/{to.isoformat()}/{frm.isoformat()}") or {}
            rows += d.get("candles") or []
        f = _frame(rows, ("ts", "open", "high", "low", "close", "volume"))
        return f if len(f) else None

    def check(self, sym, *, entry, stop, target, direction, when: dt.datetime | None = None) -> dict:
        now = when or self.e.clock.now()
        days, sess = self.history(sym, now.date())
        bar = _bar_of(now) + 1                      # the next bar after the signal
        out = run(sess, days, entry=entry, stop=stop, target=target, direction=direction, start_bar=bar)
        out["sym"] = sym
        out["as_of"] = now.isoformat(timespec="minutes")
        return out
