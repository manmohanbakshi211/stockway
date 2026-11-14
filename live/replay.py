"""2.76 Replay a past day (the idea copied from Tanu's rehearsal: a past market day through the whole live path).

A day the live scanner recorded (data/recordings/upstox_<day>_*.parquet) is played back through the same engine: the
same scanner, alerts, risk check, paper trades, test bench and trials, on a replay clock 5-30x faster than real time.
- Index and stock prices: the day's 1-minute candles (open, the two extremes, close inside each minute).
- Futures, options, VIX, Sensex: the minute snapshots; an option only in the recorded full chains: those chains.
- Full option chains (what the scanner asks the broker for): the recorded chains, never one from later on the clock.
- Headlines: the recorded news, as they were seen that day.
- Daily history and IV history: the copies the scanner had that morning (IV history cut before that day).
Nothing is fetched from a broker for the replayed day.

Kept apart like the simulator: source 'replay' (alerts, paper trades, bench, trials; cleared when the next replay
starts), its own recording folder (data/replay), no Telegram, no automatic AI notes, never a real order.
Honest limits: one price path per minute (not every tick); world markets and FII figures are today's; the models in use
were trained after that day, so a replay is a rehearsal of the system, not a fair test (the Lab's holdout is)."""
from __future__ import annotations

import datetime as dt
import gzip
import json
import threading
import time
import urllib.parse
from pathlib import Path

import numpy as np

from .clock import IST, Clock

STEP_S = 5.0                       # replay clock seconds per step
SPEEDS = (5, 10, 20, 30)
DEFAULT_SPEED = 10
PARTS = ("minutes", "bars", "chains")
MAX_WAIT_S = 8.0                   # a replay minute waits at most this long (real seconds) for one analysis pass
COLS = ("ltp", "vol", "oi", "iv", "bid", "ask", "tbq", "tsq", "cvd")
STORE_TABLES = ("alerts", "paper", "outcomes", "strat_trades", "plan_outcomes", "trial_alerts", "nextcandle",
                "candlecast", "line_touches", "mind_reads")


def _rec_dir(data_dir) -> Path:
    return Path(data_dir) / "recordings"


def _part(data_dir, day: str, part: str) -> Path:
    return _rec_dir(data_dir) / f"upstox_{day}_{part}.parquet"


def _regular(day: dt.date) -> bool:
    from . import session as SESSION
    s = SESSION.session(day)
    return bool(s and s["kind"] == "regular" and s["open"].time() == dt.time(9, 15) and s["close"].time() == dt.time(15, 30))


def previous_session(day: dt.date) -> dt.date | None:
    from . import session as SESSION
    d = day
    for _ in range(12):
        d -= dt.timedelta(days=1)
        if SESSION.session(d):
            return d
    return None


def speed_of(v) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return float(DEFAULT_SPEED)
    return float(min(SPEEDS, key=lambda s: abs(s - v)))


def days(data_dir, today: dt.date | None = None) -> list:
    """Recorded real-market days a replay can play: finished recordings (parquet) of regular sessions, newest first."""
    today = today or dt.datetime.now(IST).date()
    out = []
    for f in sorted(_rec_dir(data_dir).glob("upstox_*_minutes.parquet"), reverse=True):
        day = f.name[len("upstox_"):len("upstox_") + 10]
        try:
            d = dt.date.fromisoformat(day)
        except ValueError:
            continue
        ps = [_part(data_dir, day, p) for p in PARTS]
        if d > today or not all(p.exists() for p in ps) or not _regular(d):
            continue
        out.append({"day": day, "label": d.strftime("%a %d %b %Y"), "mb": round(sum(p.stat().st_size for p in ps) / 1e6, 1),
                    "news": _part(data_dir, day, "news").exists()})
    return out


def prepare_store(store, day: str, data_dir=None, log=print) -> dict:
    """Before a replay: the last replay's rows go (alerts, paper trades, grades: source 'replay' only), and the day's
    morning copies are put in place: daily history as of that morning, IV history cut before that day."""
    gone = {}
    with store.lock:
        for t in STORE_TABLES:
            try:
                gone[t] = store.db.execute(f"DELETE FROM {t} WHERE source='replay' OR source LIKE 'replay#%'").rowcount
            except Exception:
                pass
        store.db.commit()
    daily = store.get(f"daily:upstox:{day}") or {}
    if daily:
        store.put(f"daily:replay:{day}", daily)
    hist = store.get("ivhist:upstox", {}) or {}
    store.put("ivhist:replay", {s: [r for r in rows if str(r[0]) < day] for s, rows in hist.items()})
    if data_dir is not None:                        # the last replay's own recording (data/replay), never the real ones
        for f in (Path(data_dir) / "replay" / "recordings").glob("replay_*"):
            try:
                f.unlink()
            except Exception:
                pass
    return {"cleared": gone, "daily": bool(daily)}


# ---------------------------------------------------------------- the recorded day in memory
class Recording:
    """One recorded day as arrays (a full day of ~3,400 instruments is about 100 MB)."""

    def __init__(self, data_dir, day: str, daily: dict | None = None, log=print):
        import pandas as pd
        self.data_dir, self.day, self.log = Path(data_dir), day, log
        self.d = dt.date.fromisoformat(day)
        self.daily = daily or {}
        self.open_ts = dt.datetime.combine(self.d, dt.time(9, 15), IST).timestamp()
        self.close_ts = dt.datetime.combine(self.d, dt.time(15, 30), IST).timestamp()
        m = pd.read_parquet(_part(data_dir, day, "minutes"), columns=["ts", "key", *COLS])
        m = m[m["ltp"].notna() & (m["ltp"] > 0)].sort_values("ts", kind="stable").reset_index(drop=True)
        self.m_ts = m["ts"].to_numpy(np.int64)
        self.m_key = m["key"].to_numpy(object)
        self.m_val = m[list(COLS)].to_numpy(np.float64)
        self.m_by_key = dict(m.groupby("key", sort=False).indices)
        b = pd.read_parquet(_part(data_dir, day, "bars"))
        b = b.drop_duplicates(["sym", "t"], keep="last").sort_values(["sym", "t"])
        self.bars = {}
        for s, g in b.groupby("sym", sort=False):
            a = g[["t", "o", "h", "l", "c", "v"]].to_numpy(np.float64)
            a = a[(a[:, 0] >= self.open_ts) & (a[:, 0] < self.close_ts)]
            if len(a):
                self.bars[s] = a
        c = pd.read_parquet(_part(data_dir, day, "chains"))
        c = c[c["key"].notna()].sort_values("ts", kind="stable").reset_index(drop=True)
        c["expiry"] = c["expiry"].astype(str).str[:10]
        self.c = c
        self.c_ts = c["ts"].to_numpy(np.int64)
        self.c_by_key = dict(c.groupby("key", sort=False).indices)
        self.c_snaps = {}                                     # (sym, expiry) -> sorted snapshot times
        for (s, e), g in c.groupby(["sym", "expiry"], sort=False):
            self.c_snaps[(s, str(e))] = np.unique(g["ts"].to_numpy(np.int64))
        self.c_group = dict(c.groupby(["sym", "expiry", "ts"], sort=False).indices)
        self.news = []
        f = _part(data_dir, day, "news")
        if f.exists():
            try:
                n = pd.read_parquet(f).sort_values("seen")
                for r in n.itertuples(index=False):
                    self.news.append({"ts": float(r.ts), "seen": float(r.seen), "source": r.source, "title": r.title,
                                      "link": r.link, "syms": [x for x in str(r.syms or "").split(",") if x],
                                      "tone": int(r.tone or 0)})
            except Exception as e:
                log(f"Replay: recorded news not read ({e})")
        self.lots = {}
        f = _part(data_dir, day, "metrics")
        if f.exists():                                        # the lot sizes of that day (they change over time)
            try:
                x = pd.read_parquet(f).sort_values("ts").groupby("sym").first()
                for s, r in x.iterrows():
                    lot = (json.loads(r["data"]) or {}).get("lot")
                    if lot:
                        self.lots[s] = int(lot)
            except Exception as e:
                log(f"Replay: lot sizes of that day not read ({e})")
        self.prev = {}                                        # key -> (close, oi) at the end of the previous session
        self.prev_day = previous_session(self.d)
        if self.prev_day is not None and _part(data_dir, self.prev_day.isoformat(), "minutes").exists():
            try:
                y = pd.read_parquet(_part(data_dir, self.prev_day.isoformat(), "minutes"), columns=["ts", "key", "ltp", "oi"])
                y = y[y["ltp"] > 0].sort_values("ts").groupby("key").last()
                self.prev = {k: (float(r.ltp), (float(r.oi) if r.oi == r.oi and r.oi else None)) for k, r in y.iterrows()}
            except Exception as e:
                log(f"Replay: previous day's closes not read ({e})")
        self._hist_cache = {}

    # ------------------------------------------------------------ prices at a time (never later)
    def bar_at(self, sym, ts):
        """The 1-minute candle recorded for the minute running at `ts` and the price inside it: open, then the extreme
        on the side away from the close, the other extreme, then the close (a quarter of the minute each).
        Returns (price, stage 0-3, bar), or None when no candle was recorded for that minute (the snapshots carry it)."""
        a = self.bars.get(sym)
        if a is None:
            return None
        minute = int(ts // 60) * 60
        i = int(np.searchsorted(a[:, 0], minute, side="left"))
        if i >= len(a) or a[i, 0] != minute:
            return None
        bar = a[i]
        stage = min(3, int((ts - minute) // 15))
        o, h, lo, c = (float(x) for x in bar[1:5])
        path = (o, lo, h, c) if c >= o else (o, h, lo, c)
        return path[stage], stage, bar

    def path_vol(self, key, bar, stage):
        """The day's volume inside the minute: the snapshot at the minute's start plus the candle's volume so far."""
        r = self.row_at(key, bar[0])
        base = (r or {}).get("vol")
        if base is None:
            return None
        return float(base + (bar[5] if bar[5] == bar[5] else 0) * (stage + 1) / 4)

    def row_at(self, key, ts):
        """The latest minute snapshot of `key` at or before `ts` (a dict of COLS), else the latest chain row."""
        idx = self.m_by_key.get(key)
        if idx is not None and len(idx):
            j = int(np.searchsorted(self.m_ts[idx], ts, side="right")) - 1
            if j >= 0:
                p = idx[j]
                return {c: _f(v) for c, v in zip(COLS, self.m_val[p])} | {"ts": int(self.m_ts[p])}
        idx = self.c_by_key.get(key)
        if idx is not None and len(idx):
            j = int(np.searchsorted(self.c_ts[idx], ts, side="right")) - 1
            if j >= 0:
                r = self.c.iloc[int(idx[j])]
                return {"ltp": _f(r["ltp"]), "vol": _f(r["vol"]), "oi": _f(r["oi"]), "iv": _f(r["iv"]), "bid": _f(r["bid"]),
                        "ask": _f(r["ask"]), "delta": _f(r["delta"]), "ts": int(r["ts"])}
        return None

    def cp(self, key, sym=None):
        """The previous session's close: the daily history of that morning (indices, stocks), else the previous
        recorded day's last price (futures, options, VIX)."""
        if sym and sym in self.daily:
            c = ((self.daily.get(sym) or {}).get("prev") or {}).get("c")
            if c:
                return float(c)
        p = self.prev.get(key)
        if p:
            return p[0]
        if key.endswith("India VIX"):
            closes = (self.daily.get("_vix") or {}).get("closes") or []
            return float(closes[-1]) if closes else None
        return None

    def chain(self, sym, expiry: str, ts):
        snaps = self.c_snaps.get((sym, expiry))
        if snaps is None:
            return None, []
        j = int(np.searchsorted(snaps, ts, side="right")) - 1
        if j < 0:
            return None, []
        t = int(snaps[j])
        idx = self.c_group.get((sym, expiry, t))
        return t, ([] if idx is None else self.c.iloc[idx])

    def history_bars(self, sym, frm: dt.date, to: dt.date) -> list:
        """Earlier recorded days' 1-minute candles of `sym` (from frm to `to`, before the replayed day)."""
        import pandas as pd
        out = []
        d = frm
        while d <= to and d < self.d:
            f = _part(self.data_dir, d.isoformat(), "bars")
            if f.exists():
                if d not in self._hist_cache:
                    try:
                        self._hist_cache[d] = pd.read_parquet(f).drop_duplicates(["sym", "t"], keep="last")
                    except Exception:
                        self._hist_cache[d] = None
                b = self._hist_cache[d]
                if b is not None:
                    o = dt.datetime.combine(d, dt.time(9, 15), IST).timestamp()
                    g = b[(b["sym"] == sym) & (b["t"] >= o) & (b["t"] < o + 375 * 60)].sort_values("t")
                    out += [[float(r.t), float(r.o), float(r.h), float(r.l), float(r.c), float(r.v or 0)] for r in g.itertuples()]
            d += dt.timedelta(days=1)
        return out


def _f(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


def universe_for(data_dir, rec: Recording, log=print):
    """That day's instruments: the instrument list nearest that day (this computer keeps the latest one), plus every
    contract the day's recorded chains hold that the list no longer has (expired since), with that day's lot sizes."""
    from . import universe as U
    d = Path(data_dir) / "instruments"
    fs = sorted(d.glob("NSE_*.json.gz"))
    if not fs:
        raise RuntimeError("No instrument list on this computer yet: start the live scanner once (it downloads it).")
    after = [f for f in fs if f.name[4:14] >= rec.day]
    pick = after[0] if after else fs[-1]
    uni = U.build(json.load(gzip.open(pick)), rec.d)
    added = 0
    for r in rec.c.drop_duplicates("key").itertuples(index=False):
        if r.key in uni.by_key or r.sym not in uni.underlyings:
            continue
        u = uni.underlyings[r.sym]
        try:
            e = dt.date.fromisoformat(str(r.expiry)[:10])
        except ValueError:
            continue
        o = U.Option(key=r.key, symbol=r.sym, kind=str(r.side), strike=float(r.strike), expiry=e, lot=u.lot)
        u.chains.setdefault(e, {}).setdefault(o.strike, {})[o.kind] = o
        uni.by_key[o.key] = ("OPT", r.sym, o)
        added += 1
    for s, u in uni.underlyings.items():
        u.expiries = sorted(u.chains)
        lot = rec.lots.get(s)
        if lot and lot != u.lot:
            u.lot = lot
            for strikes in u.chains.values():
                for pair in strikes.values():
                    for o in pair.values():
                        o.lot = lot
    log(f"Replay universe: {len(uni.indices)} indices, {len(uni.stocks)} stocks (list {pick.name[4:14]}, "
        f"{added} expired contracts added from the recorded chains)")
    return uni


# ---------------------------------------------------------------- clock, REST and feed
class ReplayClock(Clock):
    """The replayed day's time, moved forward only by the replay feed (5 seconds a step), so no minute is skipped."""

    def __init__(self, day: dt.date, speed: float = DEFAULT_SPEED, start: dt.time = dt.time(9, 15)):
        self.speed = float(speed)
        self.t = dt.datetime.combine(day, start, IST).timestamp()

    def now(self) -> dt.datetime:
        return dt.datetime.fromtimestamp(self.t, IST)

    def ts(self) -> float:
        return self.t

    def set(self, t: float):
        self.t = max(self.t, float(t))


class RecordingREST:
    """Same interface as UpstoxREST, answered from the recording at the replay clock's time (never later)."""

    def __init__(self, rec: Recording, clock: ReplayClock, uni):
        self.rec, self.clock, self.uni = rec, clock, uni
        self.limiter = None
        self.und = {u.key: s for s, u in uni.underlyings.items()}

    def profile(self):
        return {"user_name": f"Replay {self.rec.day}", "user_id": "REPLAY"}

    def quotes(self, keys):
        now, out = self.clock.ts(), {}
        for k in keys:
            sym = self.und.get(k)
            q = None
            b = self.rec.bar_at(sym, now) if sym else None
            if b:
                q = {"last_price": b[0], "volume": self.rec.path_vol(k, b[2], b[1]), "ohlc": {}, "oi": 0}
            if q is None:
                r = self.rec.row_at(k, now)
                if not r:
                    continue
                q = {"last_price": r["ltp"], "volume": r.get("vol"), "oi": r.get("oi"), "ohlc": {}}
            cp = self.rec.cp(k, sym)
            prev = self.rec.prev.get(k)
            q.update(instrument_token=k, prev_close_price=cp, previous_oi=(prev[1] if prev else None), average_price=None,
                     net_change=round(q["last_price"] - cp, 2) if cp else 0, last_trade_time=str(int(now * 1000)))
            out[k] = q
        return out

    def option_chain(self, underlying_key, expiry):
        sym = self.und.get(underlying_key)
        t, rows = self.rec.chain(sym, expiry.isoformat() if hasattr(expiry, "isoformat") else str(expiry), self.clock.ts())
        if t is None or len(rows) == 0:
            return []
        by = {}
        for r in rows.itertuples(index=False):
            by.setdefault(float(r.strike), {})[r.side] = r
        spot = float(rows["spot"].iloc[0]) if "spot" in rows else None
        out = []
        for k in sorted(by):
            row = {"expiry": str(expiry), "strike_price": k, "underlying_key": underlying_key, "underlying_spot_price": spot}
            for side, name in (("CE", "call_options"), ("PE", "put_options")):
                r = by[k].get(side)
                if r is None:
                    continue
                oi, ch = _f(r.oi), _f(r.oi_chg)
                prev = self.rec.prev.get(r.key)
                row[name] = {"instrument_key": r.key,
                             "market_data": {"ltp": _f(r.ltp), "volume": _f(r.vol), "oi": oi,
                                             "close_price": prev[0] if prev else None, "bid_price": _f(r.bid), "bid_qty": 0,
                                             "ask_price": _f(r.ask), "ask_qty": 0,
                                             "prev_oi": (oi - ch) if oi is not None and ch is not None else (prev[1] if prev else None)},
                             "option_greeks": {"vega": None, "theta": None, "gamma": None, "delta": _f(r.delta),
                                               "iv": _f(r.iv), "pop": None}}
            out.append(row)
        ce = sum(((r.get("call_options") or {}).get("market_data") or {}).get("oi") or 0 for r in out)
        pe = sum(((r.get("put_options") or {}).get("market_data") or {}).get("oi") or 0 for r in out)
        for r in out:
            r["pcr"] = round(pe / ce, 4) if ce else 0
        return out

    def daily_candles(self, key, days=40):
        """Only used when that morning's daily history was not saved: rebuilt from it where it exists."""
        sym = self.und.get(key)
        d = (self.rec.daily.get(sym) or {}) if sym else {}
        rows = d.get("d_ohlc") or []
        if not rows and key.endswith("India VIX"):
            closes = (self.rec.daily.get("_vix") or {}).get("closes") or []
            rows = [[None, c, c, c, c, 0] for c in closes]
        out = []
        for r in rows[-int(days):]:
            day = r[0] or ""
            out.append([f"{day}T00:00:00+05:30", r[1], r[2], r[3], r[4], r[5] if len(r) > 5 else 0, 0])
        return list(reversed(out))

    def get(self, path, params=None):
        """The historical candles the charts ask for: earlier recorded days, and the replayed day up to the clock."""
        p = [urllib.parse.unquote(x) for x in str(path).split("?")[0].split("/")]
        if "historical-candle" not in p:
            return {}
        if "intraday" in p:
            key = p[p.index("intraday") + 1]
            sym = self.und.get(key)
            a = self.rec.bars.get(sym) if sym else None
            if a is None:
                return {"candles": []}
            done = a[a[:, 0] + 60 <= self.clock.ts()]
            return {"candles": [[_iso(r[0]), r[1], r[2], r[3], r[4], r[5], 0] for r in done[::-1]]}
        i = p.index("historical-candle")
        key, to, frm = p[i + 1], p[-2], p[-1]
        sym = self.und.get(key)
        try:
            rows = self.rec.history_bars(sym, dt.date.fromisoformat(frm), dt.date.fromisoformat(to)) if sym else []
        except ValueError:
            rows = []
        return {"candles": [[_iso(r[0]), r[1], r[2], r[3], r[4], r[5], 0] for r in reversed(rows)]}


def _iso(t) -> str:
    return dt.datetime.fromtimestamp(float(t), IST).isoformat()


class RecordingFeed:
    """Same interface as UpstoxFeed / SimFeed. Each step moves the replay clock 5 seconds and sends what happened in
    them for the subscribed instruments; at every new minute it waits (at most 8 s) for one full analysis pass, so a
    slow computer slows the replay instead of skipping minutes."""

    def __init__(self, rec: Recording, clock: ReplayClock, uni, on_ticks, on_status=print, on_news=None, gate=None):
        self.rec, self.clock, self.on_ticks, self.on_status = rec, clock, on_ticks, on_status
        self.on_news, self.gate = on_news, gate
        self.und = {u.key: s for s, u in uni.underlyings.items() if s in rec.bars}
        self.subs = {"full": set(), "option_greeks": set(), "ltpc": set()}
        self.lock = threading.Lock()
        self.stop_flag = threading.Event()
        self.connected = False
        self.msgs = 0
        self.last_msg = 0.0
        self.thread = None
        self.done = False
        self.sent = {}                     # und key -> (minute, stage) last sent
        self.news_i = 0
        self.waits = 0
        self.end_ts = rec.close_ts + STEP_S

    def start(self):
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="replay-feed")
        self.thread.start()

    def stop(self):
        self.stop_flag.set()

    def subscribe(self, keys, mode):
        with self.lock:
            for m in self.subs:
                self.subs[m].difference_update(keys)
            self.subs[mode].update(k for k in keys if k)

    def unsubscribe(self, keys):
        with self.lock:
            for m in self.subs:
                self.subs[m].difference_update(keys)

    def counts(self):
        return {m: len(v) for m, v in self.subs.items()}

    def progress(self) -> float:
        span = self.rec.close_ts - self.rec.open_ts
        return round(max(0.0, min(1.0, (self.clock.ts() - self.rec.open_ts) / span)) * 100, 1)

    def ticks(self, t0: float, t1: float) -> list:
        """What the feed sends for the clock moving from t0 to t1 (t1 is the clock now)."""
        with self.lock:
            subs = set().union(*self.subs.values())
        out = []
        ltt = int(t1 * 1000)
        barred = set()
        for k in subs & self.und.keys():                       # index and stock prices from the 1-minute candles
            b = self.rec.bar_at(self.und[k], t1)
            if not b:
                continue
            barred.add(k)
            tag = (int(b[2][0]), b[1])
            if self.sent.get(k) == tag:
                continue
            self.sent[k] = tag
            t = {"key": k, "ltp": b[0], "ltt": ltt}
            v = self.rec.path_vol(k, b[2], b[1])
            if v is not None:
                t["vol"] = v
            out.append(t)
        lo, hi = np.searchsorted(self.rec.m_ts, [t0, t1], side="right")
        if hi > lo:                                            # minute snapshots that fell in this step
            keys, vals = self.rec.m_key[lo:hi], self.rec.m_val[lo:hi]
            for k, v in zip(keys, vals):
                if k not in subs:
                    continue
                t = {c: float(x) for c, x in zip(COLS, v) if x == x}     # plain floats, as the real feed sends
                if k in barred:                                # the candle path carries price and volume
                    t.pop("ltp", None), t.pop("vol", None), t.pop("bid", None), t.pop("ask", None)
                    if not t:
                        continue
                t["key"], t["ltt"] = k, ltt
                out.append(t)
        lo, hi = np.searchsorted(self.rec.c_ts, [t0, t1], side="right")
        if hi > lo:                                            # options only the full chains recorded
            part = self.rec.c.iloc[lo:hi]
            for r in part.itertuples(index=False):
                if r.key in subs and r.key not in self.rec.m_by_key and _f(r.ltp):
                    t = {"key": r.key, "ltp": _f(r.ltp), "vol": _f(r.vol), "oi": _f(r.oi), "iv": _f(r.iv), "bid": _f(r.bid),
                         "ask": _f(r.ask), "delta": _f(r.delta), "ltt": ltt}
                    out.append({k: v for k, v in t.items() if v is not None})
        return out

    def news(self, t0: float, t1: float) -> list:
        out = []
        while self.news_i < len(self.rec.news) and self.rec.news[self.news_i]["seen"] <= t1:
            out.append(dict(self.rec.news[self.news_i]))
            self.news_i += 1
        return out

    def _wait_gate(self, n0):
        t = time.time()
        while not self.stop_flag.is_set() and time.time() - t < MAX_WAIT_S:
            try:
                if self.gate() > n0:
                    return
            except Exception:
                return
            self.waits += 1
            self.stop_flag.wait(0.05)

    def _run(self):
        self.connected = True
        self.on_status(f"Replay feed running: {self.rec.day} at {self.clock.speed:g}x")
        n0 = self.gate() if self.gate else 0
        last_min = int(self.clock.ts() // 60)
        while not self.stop_flag.is_set():
            t0 = self.clock.ts()
            if t0 >= self.end_ts:
                self.done = True
                self.stop_flag.wait(0.5)
                continue
            t1 = t0 + STEP_S
            if int(t1 // 60) != last_min and self.gate is not None:
                self._wait_gate(n0)
                n0 = self.gate()
            last_min = int(t1 // 60)
            self.clock.set(t1)
            try:
                ticks = self.ticks(t0, t1)
                if ticks:
                    self.msgs += 1
                    self.last_msg = time.time()
                    self.on_ticks(ticks)
                ns = self.news(t0, t1)
                if ns and self.on_news:
                    self.on_news(ns)
            except Exception as e:
                self.on_status(f"Replay step at {self.clock.now():%H:%M:%S} skipped: {e}")
            self.stop_flag.wait(STEP_S / max(0.1, self.clock.speed))
        self.connected = False
