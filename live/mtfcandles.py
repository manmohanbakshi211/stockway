"""Multi-timeframe candle scanner (gun): every candle pattern on every timeframe, the moment each candle closes.

Intraday timeframes (15m, 30m, 1H, 75m, and 5m for the stocks the older 5-minute scanner doesn't cover) are built from
the live 1-minute candles of every index and F&O stock, joined to yesterday's recorded 1-minute candles so the first
candles of the day have context. Daily and weekly candles come from the daily history (finished days and weeks only).
Patterns are the bullets in live/candles.py (the same code the Lab and the chart use); which timeframes run and which
are paper-tested are bullets in rules/candle_tfs.json.

What it does with each closed candle:
- records every pattern found (the day's recording, part "candles": data for backtests);
- keeps the latest candle per symbol and timeframe, so every test-bench signal gets facts like "the last 1H candle
  was a bullish engulfing" (cdl_1h_al: +1 agrees with the signal, -1 against, 0 none);
- sends the alertable patterns (with their key-level rule) to the test bench as trials: kind candle_<pattern>,
  version = timeframe. No alert, no paper account, no broker: on trial until the data says otherwise.
A candle that is still forming is never a pattern; now_view() shows how it looks so far, as information only.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np

from .clock import IST

FILE = Path(__file__).resolve().parents[1] / "rules" / "candle_tfs.json"
DEFAULTS = {"intraday_min": [15, 30, 60, 75], "daily": True, "weekly": True, "five_min_all_stocks": True,
            "bench": ["15m", "30m", "1H", "75m", "1D", "1W"], "max_bench_per_close": 120}
TF_NAME = {5: "5m", 15: "15m", 30: "30m", 60: "1H", 75: "75m"}
NO_DIRECTION = {"doji", "inside_bar", "nr7"}
PLAIN = {"doji": "Doji", "inside_bar": "Inside bar", "nr7": "Narrowest of 7 (NR7)", "doji_dir": "Dragonfly / gravestone doji"}
KEEP = 60                                               # candles handed to the patterns (they look back ~14 at most)            # patterns that only say "pause", not which way
_C: dict = {}


def cfg() -> dict:
    try:
        mt = FILE.stat().st_mtime
        if _C.get("mt") != mt:
            raw = json.loads(FILE.read_text())
            _C.update(mt=mt, cfg={**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}, error=None)
        return _C["cfg"]
    except Exception as e:
        _C.update(cfg=dict(DEFAULTS), error=str(e)[:160])
        return _C["cfg"]


def session_start(day: dt.date) -> float:
    return dt.datetime.combine(day, dt.time(9, 15), IST).timestamp()


def resample(rows, minutes: int, t0: float) -> np.ndarray:
    """1-minute rows [t, o, h, l, c, v] (one session, sorted) -> N-minute candles aligned to that session's open.
    Returns an array of [t, o, h, l, c, v]."""
    a = np.asarray([r[:6] for r in rows if r[0] >= t0], dtype=float)
    if not len(a):
        return np.zeros((0, 6))
    a = a[np.argsort(a[:, 0], kind="stable")]
    k = ((a[:, 0] - t0) // (60 * minutes)).astype(int)
    starts = np.r_[0, np.flatnonzero(np.diff(k)) + 1]
    ends = np.r_[starts[1:] - 1, len(a) - 1]
    return np.column_stack([t0 + k[starts] * 60.0 * minutes, a[starts, 1], np.maximum.reduceat(a[:, 2], starts),
                            np.minimum.reduceat(a[:, 3], starts), a[ends, 4], np.add.reduceat(a[:, 5], starts)])


def daily_weekly(d_ohlc: list, today: dt.date) -> tuple[np.ndarray, np.ndarray]:
    """Finished daily candles and finished weekly candles (before today's week) as [t, o, h, l, c, v] arrays."""
    rows = sorted((r for r in d_ohlc or [] if str(r[0])[:10] < today.isoformat()), key=lambda r: str(r[0]))
    if not rows:
        return np.zeros((0, 6)), np.zeros((0, 6))
    days = [dt.date.fromisoformat(str(r[0])[:10]) for r in rows]
    d = np.asarray([[session_start(x), *map(float, r[1:6])] for x, r in zip(days, rows)])
    cur = today.isocalendar()[:2]
    wk: dict = {}
    for x, r in zip(days, d):
        k = x.isocalendar()[:2]
        if k >= cur:
            continue
        w = wk.get(k)
        wk[k] = r.copy() if w is None else np.array([w[0], w[1], max(w[2], r[2]), min(w[3], r[3]), r[4], w[5] + r[5]])
    w = np.asarray([wk[k] for k in sorted(wk)]) if wk else np.zeros((0, 6))
    return d, w


def _prior_hi_lo(h, l, n):
    """Highest high / lowest low of the n candles before each one (NaN where unknown): support / resistance."""
    hi, lo = np.full(len(h), np.nan), np.full(len(l), np.nan)
    for i in range(1, len(h)):
        hi[i], lo[i] = np.max(h[max(0, i - n):i]), np.min(l[max(0, i - n):i])
    return hi, lo


class CandleScanner:
    def __init__(self, engine, log=None):
        self.e = engine
        self.log = log or engine.log
        self.done: dict = {}                  # timeframe minutes -> index of the last candle processed today
        self.latest: dict = {}                # (sym, tf) -> {"t", "patterns": {name: v}, "ideas": [(name, d)], "close"}
        self.day = None
        self.daily_done = None
        self._prev: dict | None = None         # yesterday's 1-minute candles per symbol (from the recording)
        self._prev_day = None
        self.counts = {"closes": 0, "patterns": 0, "bench": 0}
        self.last_run = {}

    # ------------------------------------------------------------ data
    def prev_rows(self, sym) -> tuple[list, float | None]:
        """Yesterday's 1-minute candles of a symbol (the last recorded day before today), and that day's open time."""
        today = self.e.clock.now().date()
        if self._prev is None or self.day != today:
            self._prev, self._prev_day = {}, None
            try:
                d = Path(self.e.data_dir) / "recordings"
                files = sorted(f for f in d.glob(f"{self.e.source}_*_bars.parquet") if f.stem.split("_")[1] < today.isoformat())
                if files:
                    import pandas as pd
                    df = pd.read_parquet(files[-1])
                    self._prev_day = dt.date.fromisoformat(files[-1].stem.split("_")[1])
                    for s, g in df.groupby("sym"):
                        self._prev[s] = g[["t", "o", "h", "l", "c", "v"]].values.tolist()
            except Exception as ex:
                self.log(f"Candle scanner: yesterday's candles not loaded ({ex})")
        return self._prev.get(sym) or [], (session_start(self._prev_day) if self._prev_day else None)

    def bars(self, sym, minutes: int, upto: float | None = None) -> np.ndarray:
        """Candles of `minutes` for a symbol: yesterday's then today's, only 1-minute candles that started before `upto`."""
        today = self.e.clock.now().date()
        t0 = session_start(today)
        rows = [r for r in self.e.charts.today_bars(sym) if upto is None or r[0] < upto]
        out = []
        prev, p0 = self.prev_rows(sym)
        if prev and p0:
            out.append(resample(prev, minutes, p0))
        out.append(resample(rows, minutes, t0))
        out = [x for x in out if len(x)]
        return np.vstack(out) if out else np.zeros((0, 6))

    def _levels(self, sym, n):
        m = self.e.metrics.get(sym) or {}
        ctx = self.e.charts.context(sym, m)
        vals = [ctx.get("pdh"), ctx.get("pdl"), ctx.get("pdc"), ctx.get("or_high"), ctx.get("or_low"), m.get("vwap")]
        return [float(v) for v in vals if isinstance(v, (int, float)) and np.isfinite(v)]

    # ------------------------------------------------------------ the scan
    def tick(self, now):
        c = cfg()
        today = now.date()
        if self.day != today:
            self.day, self.done, self.latest, self.daily_done = today, {}, {}, None
        t0 = session_start(today)
        el = now.timestamp() - t0 - 2                      # 2 s after a candle closes, so its last minute is complete
        if el < 0:
            return
        tfs = sorted(set(int(x) for x in c["intraday_min"]) | ({5} if c.get("five_min_all_stocks") else set()))
        for tf in tfs:
            k = int(el // (tf * 60))
            if k < 1 or self.done.get(tf) == k:
                continue
            self.done[tf] = k
            self._close(tf, t0 + k * tf * 60, now, c)
            return                                         # one timeframe per loop: when several close together the
                                                           # others follow a second later, the live loop never stalls
        if self.daily_done != today and now.strftime("%H:%M") >= "09:20":
            self.daily_done = today
            self._daily(now, c)

    def _close(self, tf, boundary, now, c):
        from . import candles as CDL, registry
        from lab import rules
        name = TF_NAME.get(tf, f"{tf}m")
        spec = registry.candle_spec()
        older_5m = set(rules.stock_list("lab_first"))
        bench_on = name in set(c.get("bench") or [])
        found = []
        n_sym = 0
        for sym, m in list(self.e.metrics.items()):
            if tf == 5 and (m.get("is_index") or sym in older_5m):
                continue                                   # the 5-minute scanner (chartdata) already covers these
            try:
                b = self.bars(sym, tf, upto=boundary)
                if len(b) < 3 or b[-1][0] != boundary - tf * 60:
                    continue
                n_sym += 1
                b = b[-KEEP:]                              # patterns look back at most ~14 candles
                o, h, l, cl = b[:, 1], b[:, 2], b[:, 3], b[:, 4]
                pats, ideas = CDL.last_bar_scan(o, h, l, cl, self._levels(sym, len(b)), spec)
                self.latest[(sym, name)] = {"t": float(b[-1][0]), "patterns": pats, "ideas": [(x["name"], x["d"]) for x in ideas],
                                            "close": float(cl[-1])}
                for p, v in pats.items():
                    found.append((sym, name, float(b[-1][0]), p, v, any(x["name"] == p for x in ideas), None, float(cl[-1])))
                for x in ideas:
                    found.append((sym, name, float(b[-1][0]), x["name"], x["d"], True, x.get("lvl_atr"), float(cl[-1]), x))
            except Exception as ex:
                if self.e._every(f"cdl_err:{sym}", 1800, clock_time=False):
                    self.log(f"Candle scanner {sym} {name} skipped: {ex}")
        self._record(now, found)
        self.counts["closes"] += 1
        sent = self._bench([f for f in found if len(f) == 9], name, now, c) if bench_on else 0
        self.last_run[name] = {"at": now.strftime("%H:%M"), "symbols": n_sym, "patterns": sum(1 for f in found if len(f) == 8),
                               "ideas": sum(1 for f in found if len(f) == 9), "bench": sent}

    def _daily(self, now, c):
        from . import candles as CDL, registry
        spec = registry.candle_spec()
        today = now.date()
        found = {"1D": [], "1W": []}
        for sym in list(self.e.metrics):
            dly = self.e.daily.get(sym) or {}
            if not dly.get("d_ohlc"):
                continue
            try:
                d, w = daily_weekly(dly["d_ohlc"], today)
                for name, b, n, on in (("1D", d, 20, c.get("daily")), ("1W", w, 10, c.get("weekly"))):
                    if not on or len(b) < 3:
                        continue
                    b = b[-KEEP:]
                    o, h, l, cl = b[:, 1], b[:, 2], b[:, 3], b[:, 4]
                    hi, lo = _prior_hi_lo(h, l, n)
                    pats, ideas = CDL.last_bar_scan(o, h, l, cl, [hi, lo], spec)
                    self.latest[(sym, name)] = {"t": float(b[-1][0]), "patterns": pats, "ideas": [(x["name"], x["d"]) for x in ideas],
                                                "close": float(cl[-1])}
                    for p, v in pats.items():
                        found[name].append((sym, name, float(b[-1][0]), p, v, any(x["name"] == p for x in ideas), None, float(cl[-1])))
                    for x in ideas:
                        found[name].append((sym, name, float(b[-1][0]), x["name"], x["d"], True, x.get("lvl_atr"), float(cl[-1]), x))
            except Exception as ex:
                if self.e._every(f"cdl_d_err:{sym}", 3600, clock_time=False):
                    self.log(f"Candle scanner {sym} daily skipped: {ex}")
        first_of_week = False
        try:                                               # weekly candles are tested once, on the week's first session
            prev = max((dt.date.fromisoformat(str(r[0])[:10]) for s in self.e.daily.values() if isinstance(s, dict)
                        for r in (s.get("d_ohlc") or [])[-3:] if str(r[0])[:10] < today.isoformat()), default=None)
            first_of_week = bool(prev and prev.isocalendar()[:2] < today.isocalendar()[:2])
        except Exception:
            pass
        for name, rows in found.items():
            self._record(now, rows)
            sent = 0
            if name in set(c.get("bench") or []) and (name == "1D" or first_of_week):
                sent = self._bench([f for f in rows if len(f) == 9], name, now, c)
            self.last_run[name] = {"at": now.strftime("%H:%M"), "symbols": len({f[0] for f in rows}),
                                   "patterns": sum(1 for f in rows if len(f) == 8), "ideas": sum(1 for f in rows if len(f) == 9), "bench": sent}

    def _record(self, now, found):
        rows = [(int(now.timestamp()), f[0], f[1], f[2], f[3], int(f[4]), int(bool(f[5])), f[6], f[7]) for f in found if len(f) == 8]
        self.counts["patterns"] += len(rows)
        rec = getattr(self.e, "recorder", None)
        if rows and rec is not None:
            try:
                rec.candles(rows)
            except Exception:
                pass

    def _bench(self, ideas, name, now, c) -> int:
        from . import registry
        if getattr(self.e, "bench", None) is None or not ideas:
            return 0
        names = registry.names("candle")
        ideas = sorted(ideas, key=lambda f: (f[6] if f[6] is not None else 9e9))[:int(c.get("max_bench_per_close") or 120)]
        n = 0
        for sym, tf, _, p, d, _, _, _, x in ideas:
            try:
                n += self.e.bench_signal(sym, "up" if d > 0 else "down", now, "candle_" + p, tf,
                                         f"{names.get(p, p)} ({tf} candle)")
            except Exception:
                pass
        self.counts["bench"] += n
        return n

    # ------------------------------------------------------------ for signals and screens
    def facts(self, sym, direction) -> dict:
        """The latest closed candle on each timeframe, signed with a signal's direction: cdl_<tf>_al = +1 a pattern on it
        agrees, -1 one disagrees, 0 none; cdl_al_sum adds them up over all timeframes."""
        d = 1 if direction == "up" else -1 if direction == "down" else 0
        if not d:
            return {}
        now = self.e.clock.now().timestamp()
        out, tot = {}, 0
        for tf in ("5m", "15m", "30m", "1H", "75m", "1D", "1W"):
            x = self.latest.get((sym, tf))
            if not x:
                continue
            if tf not in ("1D", "1W"):
                mins = {v: k for k, v in TF_NAME.items()}[tf]
                if now - x["t"] > mins * 60 * 2:
                    continue                               # an old candle says nothing about now
            # the alertable patterns (with their key-level rule) first; otherwise any directional pattern on it
            sig = sum(v for _, v in x["ideas"]) if x["ideas"] else \
                sum(v for k, v in x["patterns"].items() if k not in NO_DIRECTION)
            s = int(np.sign(sig)) * d
            out[f"cdl_{tf.lower()}_al"] = s
            tot += s
        if out:
            out["cdl_al_sum"] = tot
        return out

    def now_view(self, sym) -> dict:
        """Per timeframe: the last closed candle's patterns and how the forming one looks so far (not a signal)."""
        from . import candles as CDL, registry
        names = {**PLAIN, **registry.names("candle")}
        now = self.e.clock.now()
        t0 = session_start(now.date())
        rows = []
        c = cfg()
        for tf in sorted(set(int(x) for x in c["intraday_min"]) | {5}):
            name = TF_NAME.get(tf, f"{tf}m")
            b = self.bars(sym, tf)
            if len(b) < 3:
                continue
            start = b[-1][0]
            forming = start + tf * 60 > now.timestamp() and start >= t0
            closed = b[:-1] if forming else b
            last = dict(CDL.on_last_bar(closed[:, 1], closed[:, 2], closed[:, 3], closed[:, 4])) if len(closed) >= 3 else {}
            so_far = dict(CDL.on_last_bar(b[:, 1], b[:, 2], b[:, 3], b[:, 4])) if forming else {}
            rows.append({"tf": name, "last_t": float(closed[-1][0]) if len(closed) else None,
                         "last": [{"pattern": k, "name": names.get(k, k.replace("_", " ")), "d": v} for k, v in last.items()],
                         "forming": [{"pattern": k, "name": names.get(k, k.replace("_", " ")), "d": v} for k, v in so_far.items()] if forming else None,
                         "mins_left": round((start + tf * 60 - now.timestamp()) / 60, 1) if forming else None})
        for name in ("1D", "1W"):
            x = self.latest.get((sym, name))
            if x:
                rows.append({"tf": name, "last_t": x["t"], "forming": None, "mins_left": None,
                             "last": [{"pattern": k, "name": names.get(k, k.replace("_", " ")), "d": v} for k, v in x["patterns"].items()]})
        return {"sym": sym, "rows": rows, "note": "Closed candles only count as patterns; 'forming' is how the open candle looks so far and can change before it closes."}

    def status(self) -> dict:
        return {"counts": dict(self.counts), "last": dict(self.last_run), "error": _C.get("error")}
