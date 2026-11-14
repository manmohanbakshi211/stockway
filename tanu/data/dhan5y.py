"""The Dhan 5-year download in Tanu (ADR-0014): Dhan's expired-options archive (1-minute, from 2021) for Nifty, Bank
Nifty and every F&O stock, continued from where the legacy app's download stopped.

- Picks up from the legacy progress, read-only: a symbol-day that the legacy manifest (data/lab5y/manifest.json) or
  its month file already holds, or that Dhan finally had no data for, is never fetched again. The legacy folder is
  never written. Tanu's own days go to data/tanu/dhan5y/history/<SYM>/raw_YYYY-MM.parquet (same columns: ts, off,
  side, open, high, low, close, iv, volume, oi, strike, spot) with Tanu's own manifest.
- Trading days from the legacy calendar cache, else Dhan's own Nifty daily candles (a holiday is never guessed).
- A day is saved only when every strike came back (data, or a real "no data"); network trouble leaves it missing and
  after 3 failures in a row the download pauses and retries the same day. Every finished day is written at once.
- Stops at the daily call cap, at the disk guard, when its time window closes (never in market hours) or when asked.
Market data only: no orders, no account data (every URL through safety.check_url).
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import shutil
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd

from ..core import log, safety
from ..data.facts import Facts

SPEC = Path(__file__).with_name("dhan5y.json")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_log = log.get("dhan5y")


def _json(f: Path, default):
    try:
        return json.loads(f.read_text()) if f.exists() else default
    except Exception:
        return default


def _save_json(f: Path, obj) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")))
    tmp.replace(f)


def months(start: dt.date, end: dt.date):
    m = dt.date(start.year, start.month, 1)
    while m <= end:
        nxt = dt.date(m.year + (m.month == 12), m.month % 12 + 1, 1)
        yield m, nxt
        m = nxt


def frame(arr: dict, off: int, side: str) -> pd.DataFrame | None:
    ts = arr.get("timestamp") or []
    if not ts:
        return None
    n = len(ts)
    cols = {"ts": np.asarray(ts, dtype="int64"), "off": np.full(n, off, dtype="int8"), "side": [side] * n}
    for f in ("open", "high", "low", "close", "iv", "volume", "oi", "strike", "spot"):
        v = list(arr.get(f) or [None] * n)
        cols[f] = pd.to_numeric(pd.Series(v[:n] + [None] * (n - len(v))), errors="coerce").to_numpy(dtype="float64")
    return pd.DataFrame(cols)


def file_days(f: Path) -> set[int]:
    if not f.exists():
        return set()
    try:
        ts = pd.read_parquet(f, columns=["ts"])["ts"]
        return set(pd.to_datetime(ts.astype("int64") + 19800, unit="s").dt.day.unique().tolist()) if len(ts) else set()
    except Exception:
        return set()


def write_day(f: Path, df: pd.DataFrame) -> None:
    """Merge one day into Tanu's month file, atomically (a crash leaves the old file intact)."""
    f.parent.mkdir(parents=True, exist_ok=True)
    if f.exists():
        try:
            old = pd.read_parquet(f)
            if len(old):
                df = pd.concat([old, df], ignore_index=True).drop_duplicates(["ts", "off", "side"], keep="last")
        except Exception:
            pass
    tmp = f.with_name(f.name + ".tmp")
    df.sort_values(["ts", "off", "side"]).to_parquet(tmp, compression="zstd", index=False)
    os.replace(tmp, f)


# ---------------------------------------------------------------- when it may run
def window_open(now: dt.datetime, spec: dict, facts: Facts | None = None) -> bool:
    """Never in market hours: on a trading day only after market_days_after or before market_days_before (IST);
    all day on weekends and NSE holidays."""
    now = now.astimezone(IST)
    w = spec["run_when"]
    if not (facts or Facts()).trading_day(now.date()):
        return bool(w.get("holidays_and_weekends", True))
    hm = now.strftime("%H:%M")
    return hm >= w["market_days_after"] or hm < w["market_days_before"]


# ---------------------------------------------------------------- what is done (Tanu's own + the legacy's, read-only)
class Manifest:
    """'SYM|YYYY-MM' -> {"d": [days on disk], "e": {day: date asked} (Dhan has no data), "f": {day: errors}}."""

    def __init__(self, root: Path, legacy: Path | None, empty_final_days: int = 3, today=None):
        self.root, self.f = root, root / "manifest.json"
        self.m = _json(self.f, {})
        self.legacy = legacy
        self.lm = _json(legacy / "manifest.json", {}) if legacy else {}
        self.final = empty_final_days
        self.today = today or (lambda: dt.datetime.now(IST).date())
        self.dirty = 0
        self._legacy_files: dict[str, set] = {}

    def rec(self, sym: str, m0: dt.date) -> dict:
        k = f"{sym}|{m0:%Y-%m}"
        if k not in self.m:
            self.m[k] = {"d": sorted(file_days(self.root / "history" / sym / f"raw_{m0:%Y-%m}.parquet")), "e": {}, "f": {}}
            self.dirty += 1
        return self.m[k]

    def _legacy_has(self, sym: str, day: dt.date) -> bool:
        k = f"{sym}|{day:%Y-%m}"
        r = self.lm.get(k)
        if r is not None:
            if day.day in r.get("d", []):
                return True
            asked = (r.get("e") or {}).get(str(day.day))
            return bool(asked) and (dt.date.fromisoformat(asked) - day).days >= self.final
        if self.legacy is None:
            return False
        if k not in self._legacy_files:
            self._legacy_files[k] = file_days(self.legacy / "history" / sym / f"raw_{day:%Y-%m}.parquet")
        return day.day in self._legacy_files[k]

    def done(self, sym: str, day: dt.date) -> bool:
        if self._legacy_has(sym, day):
            return True
        rec = self.rec(sym, day.replace(day=1))
        if day.day in rec["d"]:
            return True
        asked = rec["e"].get(str(day.day))
        return bool(asked) and (dt.date.fromisoformat(asked) - day).days >= self.final

    def add(self, sym: str, day: dt.date, empty: bool = False) -> None:
        rec = self.rec(sym, day.replace(day=1))
        if empty:
            rec["e"][str(day.day)] = self.today().isoformat()
        elif day.day not in rec["d"]:
            rec["d"] = sorted(rec["d"] + [day.day])
        rec["f"].pop(str(day.day), None)
        self.dirty += 1

    def fail(self, sym: str, day: dt.date) -> int:
        rec = self.rec(sym, day.replace(day=1))
        n = rec["f"][str(day.day)] = rec["f"].get(str(day.day), 0) + 1
        self.dirty += 1
        return n

    def save(self, force: bool = False) -> None:
        if self.dirty and (force or self.dirty >= 20):
            _save_json(self.f, self.m)
            self.dirty = 0


class Calendar:
    """NSE trading days: Tanu's cache, then the legacy cache (read-only), then Dhan's Nifty daily candles."""

    def __init__(self, root: Path, legacy: Path | None, dhan=None, today=None):
        self.f, self.dhan = root / "trading_days.json", dhan
        self.c = _json(self.f, {})
        self.lc = _json(legacy / "trading_days.json", {}) if legacy else {}
        self.today = today or (lambda: dt.datetime.now(IST).date())

    def year(self, y: int) -> set | None:
        last = dt.date(y, 12, 31) if y < (self.today() - dt.timedelta(days=1)).year else self.today() - dt.timedelta(days=1)
        for c in (self.c, self.lc):
            rec = c.get(str(y))
            if rec and rec.get("to", "") >= last.isoformat():
                return {dt.date.fromisoformat(x) for x in rec["days"]}
        if self.dhan is None:
            rec = self.c.get(str(y)) or self.lc.get(str(y))
            return {dt.date.fromisoformat(x) for x in rec["days"]} if rec else None
        try:
            res = self.dhan.daily("NIFTY", dt.date(y, 1, 1).isoformat(), (last + dt.timedelta(days=1)).isoformat())
        except Exception:
            return None
        days = sorted({dt.datetime.fromtimestamp(int(t), IST).date() for t in (res.get("timestamp") or [])})
        days = [d for d in days if d <= last]
        if not days:
            return None
        self.c[str(y)] = {"to": last.isoformat(), "days": [d.isoformat() for d in days]}
        _save_json(self.f, self.c)
        return set(days)

    def days(self, m0: dt.date, m1: dt.date) -> list[dt.date]:
        """Trading days in [m0, m1) up to yesterday (Dhan publishes a day's archive after that day)."""
        stop = min(m1, self.today())
        ys = self.year(m0.year)
        out, d = [], m0
        while d < stop:
            if (d in ys) if ys is not None else d.weekday() < 5:
                out.append(d)
            d += dt.timedelta(days=1)
        return out


# ---------------------------------------------------------------- the download
class NetDown(Exception):
    def __init__(self, msg: str, net: bool = True):
        super().__init__(msg)
        self.net = net


class Dhan5y:
    def __init__(self, base: Path | str, legacy_data: Path | str | None, dhan=None, budget=None, spec: dict | None = None,
                 now=time.time, sleep=time.sleep, fetch_scrips=None):
        self.base = Path(base)
        self.root = self.base / "dhan5y"
        self.legacy = Path(legacy_data) / "lab5y" if legacy_data and (Path(legacy_data) / "lab5y").exists() else None
        self.legacy_history = Path(legacy_data) / "history" if legacy_data else None
        self.dhan, self.budget = dhan, budget
        self.spec = spec or json.loads(SPEC.read_text())
        self.now, self.sleep = now, sleep
        self.fetch_scrips = fetch_scrips or self._fetch_scrips
        self.lines: deque = deque(maxlen=40)
        self.lock = threading.Lock()

    def today(self) -> dt.date:
        return dt.datetime.fromtimestamp(self.now(), IST).date()

    def say(self, msg: str) -> None:
        t = dt.datetime.fromtimestamp(self.now(), IST).strftime("%d %b %H:%M")
        self.lines.append(f"{t} {msg}")
        _log.info("dhan5y", msg=msg[:200])

    def set_status(self, **kw) -> None:
        st = {**_json(self.root / "status.json", {}), **kw, "at": self.now(), "log": list(self.lines)[-15:]}
        _save_json(self.root / "status.json", st)

    # -------------------------------------------------------------- pause switch (System page)
    def paused(self) -> bool:
        return bool(_json(self.root / "paused.json", {}).get("paused"))

    def set_paused(self, on: bool) -> None:
        _save_json(self.root / "paused.json", {"paused": bool(on), "at": self.now()})

    # -------------------------------------------------------------- which stocks, in which order
    def _fetch_scrips(self, url: str, dest: Path) -> None:
        req = urllib.request.Request(safety.check_url(url), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read(200_000_000)
        dest.write_bytes(data)

    def scrips(self) -> Path | None:
        f = self.root / f"dhan_scrips_{self.today():%Y%m%d}.csv"
        if not f.exists():
            old = sorted(self.root.glob("dhan_scrips_*.csv"))
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                self.fetch_scrips(self.spec["scrip_master"], f)
            except Exception as e:
                self.say(f"Dhan's instrument list not reachable ({type(e).__name__}); using the last one")
                return old[-1] if old else None
            for o in old:
                try:
                    o.unlink()
                except OSError:
                    pass
        return f

    def stock_ids(self) -> dict:
        """Every NSE stock with options: {sym: {"id": Dhan security id, "lot": option lot}} (read once a day)."""
        memo = getattr(self, "_ids", None)
        if memo and memo[0] == self.today():
            return memo[1]
        f = self.scrips()
        if not f:
            return {}
        ids, lots = {}, {}
        with open(f, newline="", encoding="utf-8", errors="ignore") as fh:
            for row in csv.DictReader(fh):
                ex, inst, sym = row.get("SEM_EXM_EXCH_ID"), row.get("SEM_INSTRUMENT_NAME"), row.get("SEM_TRADING_SYMBOL", "")
                if ex == "NSE" and inst == "EQUITY" and row.get("SEM_SERIES", "EQ") in ("EQ", ""):
                    try:
                        ids[sym] = int(float(row["SEM_SMST_SECURITY_ID"]))
                    except (KeyError, ValueError):
                        pass
                elif ex == "NSE" and inst == "OPTSTK":
                    und = sym.split("-")[0]
                    if und not in lots:
                        try:
                            lots[und] = int(float(row.get("SEM_LOT_UNITS") or 0))
                        except ValueError:
                            lots[und] = 0
        out = {s: {"id": ids[s], "lot": lots[s] or 1} for s in sorted(lots) if s in ids}
        if out:
            self._ids = (self.today(), out)
        return out

    def turnover(self, ids: dict) -> dict:
        """Average daily turnover (20 days) per stock: Tanu's cache (a week), else the legacy cache, else Dhan."""
        own = _json(self.root / "universe.json", {})
        leg = _json(self.legacy / "universe.json", {}) if self.legacy else {}
        for c in (own, leg):                               # a week-old ranking is good enough (no Dhan calls)
            if c.get("turnover") and (self.today() - dt.date.fromisoformat(c.get("at", "2000-01-01"))).days < 7:
                return c["turnover"]
        if self.dhan is None:
            return own.get("turnover") or leg.get("turnover") or {}
        turn = {}
        end = self.today()
        for s, v in ids.items():
            try:
                d = self.dhan.daily(s, (end - dt.timedelta(days=45)).isoformat(), end.isoformat(), security_id=v["id"],
                                    segment="NSE_EQ", instrument="EQUITY")
                xs = [c * q for c, q in zip((d.get("close") or [])[-20:], (d.get("volume") or [])[-20:]) if c and q]
                turn[s] = sum(xs) / len(xs) if xs else 0.0
            except Exception:
                turn[s] = (own.get("turnover") or leg.get("turnover") or {}).get(s, 0.0)
        _save_json(self.root / "universe.json", {"turnover": turn, "at": self.today().isoformat()})
        self.say(f"Ranked {len(turn)} F&O stocks by turnover")
        return turn

    def universe(self, ids: dict, turn: dict) -> list[tuple[str, int]]:
        sp = self.spec
        first = [s for s in sp["first"] if s in ids]
        rest = sorted((s for s in ids if s not in first), key=lambda s: -turn.get(s, 0.0))
        return [(s, sp["wide_offsets"] if i < sp["wide_count"] else sp["narrow_offsets"]) for i, s in enumerate(first + rest)]

    # -------------------------------------------------------------- the plan
    def _months_for(self, sym: str, end: dt.date) -> list[tuple[dt.date, dt.date]]:
        stop = end
        if sym in self.spec["index_flag"] and self.legacy_history is not None:
            fs = sorted(p.stem[4:] for p in (self.legacy_history / sym).glob("raw_*.parquet") if len(p.stem) > 11)
            if fs:                                         # Upstox per-expiry files already cover the indices from here
                stop = min(end, dt.date.fromisoformat(fs[0]) - dt.timedelta(days=35))
        start = dt.date.fromisoformat(self.spec["start"])
        return [(m0, m1) for m0, m1 in months(start, stop) if m0 < stop]

    def plan(self, uni, cal: Calendar, man: Manifest) -> list:
        """[(sym, offsets, kind, month start, [missing days])]: indices first, newest month first."""
        end = self.today()
        jobs = []
        n = self.spec["index_offsets"]
        for sym, offs in [(s, n) for s in self.spec["index_flag"]] + list(uni):
            kind = "index" if sym in self.spec["index_flag"] else "stock"
            for m0, m1 in reversed(self._months_for(sym, end)):
                miss = [d for d in cal.days(m0, m1) if not man.done(sym, d)]
                if miss:
                    jobs.append((sym, offs, kind, m0, miss))
        return jobs

    def overview(self) -> dict:
        """Where the download stands, from the manifests only (no Dhan calls): the System page's progress."""
        man = Manifest(self.root, self.legacy, self.spec["empty_final_days"], self.today)
        cal = Calendar(self.root, self.legacy, None, self.today)
        own = _json(self.root / "universe.json", {}).get("turnover")
        turn = own or (_json(self.legacy / "universe.json", {}).get("turnover") if self.legacy else None) or {}
        uni = self.universe({s: 1 for s in set(turn) | set(self.spec["first"])}, turn) if turn else \
            [(s, self.spec["wide_offsets"]) for s in self.spec["first"]]
        end = self.today()
        n = self.spec["index_offsets"]
        tot = complete = days_miss = calls = 0
        sym_done = sym_part = sym_none = 0
        for sym, offs in [(s, n) for s in self.spec["index_flag"]] + uni:
            had = miss_sym = 0
            for m0, m1 in self._months_for(sym, end):
                tot += 1
                need = cal.days(m0, m1)
                miss = [d for d in need if not man.done(sym, d)]
                had += len(need) - len(miss)
                miss_sym += len(miss)
                complete += not miss
                days_miss += len(miss)
                calls += len(miss) * (2 * offs + 1) * 2
            if not miss_sym:
                sym_done += 1
            elif had:
                sym_part += 1
            else:
                sym_none += 1
        cap = self.spec["daily_cap"]
        used = self.budget.used() if self.budget else 0
        st = _json(self.root / "status.json", {})
        free = shutil.disk_usage(self.base if self.base.exists() else Path.cwd()).free / 1e9
        return {"symbols": len(uni) + len(self.spec["index_flag"]), "symbols_complete": sym_done, "symbols_partial": sym_part,
                "symbols_not_started": sym_none, "months_total": tot, "months_complete": complete,
                "pct": round(complete / tot * 100, 1) if tot else 0.0, "days_missing": days_miss, "calls_left": calls,
                "days_to_finish": round(calls / cap, 1) if cap else None, "calls_today": used, "cap": cap,
                "free_gb": round(free, 1), "paused": self.paused(), "state": st.get("state"), "why": st.get("why"), "now": st.get("now"),
                "status_at": st.get("at"), "log": st.get("log", []), "from_legacy": self.legacy is not None,
                "run_when": self.spec["run_when"]}

    # -------------------------------------------------------------- one day of one symbol
    def fetch_day(self, sym: str, kind: str, sid: int | None, offs: int, day: dt.date, should_stop):
        """All strikes of one day: a frame, 'empty' (Dhan has no data that day) or None (stopped)."""
        from ..brokers.dhan.client import DhanError, NoToken
        nxt = (day + dt.timedelta(days=1)).isoformat()
        order = sorted(range(-offs, offs + 1), key=lambda o: (abs(o), o))   # ATM first: empty ATM = empty day
        parts = []
        for i, off in enumerate(order):
            for side in ("CE", "PE"):
                if should_stop():
                    return None
                try:
                    if kind == "index":
                        arr = self.dhan.rolling(sym, self.spec["index_flag"][sym], off, side, day.isoformat(), nxt)
                    else:
                        arr = self.dhan.rolling(sym, "MONTH", off, side, day.isoformat(), nxt, security_id=sid,
                                                instrument="OPTSTK")
                except NoToken:
                    raise
                except DhanError as e:
                    raise NetDown(str(e), net=e.net) from None
                except Exception as e:
                    raise NetDown(f"{type(e).__name__}: {e}") from None
                df = frame(arr, off, side)
                if df is not None:
                    parts.append(df)
            if i == 0 and not parts:
                return "empty"
        return pd.concat(parts, ignore_index=True) if parts else "empty"

    def download(self, should_stop=lambda: False) -> str:
        """Fetches every missing trading day within today's cap. Returns done / stopped / budget / disk / no_login."""
        from ..brokers.dhan.client import NoToken
        sp = self.spec
        self.set_status(state="starting", now=None)
        try:
            ids = self.stock_ids()
            uni = [(s, o) for s, o in self.universe(ids, self.turnover(ids)) if s in ids]
            cal = Calendar(self.root, self.legacy, self.dhan, self.today)
            man = Manifest(self.root, self.legacy, sp["empty_final_days"], self.today)
            jobs = self.plan(uni, cal, man)
        except NoToken as e:
            self.say(str(e))
            self.set_status(state="no_login")
            return "no_login"
        man.save(force=True)
        per = lambda offs: (2 * offs + 1) * 2
        self.say(f"{len(jobs)} months need data ({sum(len(j[4]) for j in jobs):,} trading days, about "
                 f"{sum(len(j[4]) * per(j[1]) for j in jobs):,} calls); up to {sp['daily_cap']:,} calls a day")
        result, fails = "done", 0
        for sym, offs, kind, m0, days in jobs:
            f = self.root / "history" / sym / f"raw_{m0:%Y-%m}.parquet"
            self.set_status(state="running", now=f"{sym} {m0:%b %Y}")
            got, di = 0, 0
            while di < len(days):
                day = days[di]
                if should_stop():
                    result = "stopped"
                    break
                if self.budget and self.budget.used() + per(offs) > sp["daily_cap"]:
                    self.say(f"Today's Dhan allowance is used ({self.budget.used():,} calls); it continues after midnight")
                    result = "budget"
                    break
                if shutil.disk_usage(self.base).free / 1e9 < sp["min_free_gb"]:
                    self.say(f"Less than {sp['min_free_gb']} GB free on the Mac: paused until space is freed")
                    result = "disk"
                    break
                try:
                    res = self.fetch_day(sym, kind, ids.get(sym, {}).get("id"), offs, day, should_stop)
                except NoToken as e:
                    self.say(str(e))
                    result = "no_login"
                    break
                except NetDown as e:
                    n = man.fail(sym, day)
                    if not e.net:                          # Dhan answered but refused this day: twice more, then next run
                        if n % 3 == 0:
                            self.say(f"{sym} {day}: Dhan refused ({str(e)[:90]}); tried again on the next run")
                            di += 1
                        continue
                    fails += 1
                    if fails >= 3:
                        wait = sp["pauses_s"][min(len(sp["pauses_s"]) - 1, fails - 3)]
                        self.say(f"Network or Dhan trouble ({str(e)[:80]}): pausing {wait // 60} min, then {sym} {day:%d %b %Y} again")
                        for _ in range(int(wait)):
                            if should_stop():
                                break
                            self.sleep(1)
                    continue                               # the same day again: nothing is skipped
                fails = 0
                if res is None:
                    result = "stopped"
                    break
                if isinstance(res, str):
                    man.add(sym, day, empty=True)
                else:
                    write_day(f, res)
                    man.add(sym, day)
                    got += 1
                man.save()
                di += 1
            if got:
                self.say(f"{sym} {m0:%b %Y}: +{got} day(s)")
            if result != "done":
                break
        man.save(force=True)
        if self.budget:
            self.budget.flush()
        if result == "done":
            self.say("Every month is complete up to yesterday; new days are added each evening")
        self.set_status(state=result, now=None)
        return result


def run_forever(d5: Dhan5y, stop: threading.Event, live_ok=lambda: True, facts: Facts | None = None,
                idle_s: float = 300.0) -> None:
    """The supervisor's thread after the switch-over: downloads only while the window is open, not paused and the
    live gates hold; checks again every idle_s otherwise."""
    facts = facts or Facts()

    def blocked() -> str | None:
        if stop.is_set():
            return "stopping"
        if d5.paused():
            return "paused"
        if not live_ok():
            return "not after the switch-over"
        if not window_open(dt.datetime.fromtimestamp(d5.now(), IST), d5.spec, facts):
            return "market hours"
        return None

    while not stop.is_set():
        why = blocked()
        if why:
            d5.set_status(state="waiting", why=why)
        else:
            d5.set_status(why=None)
            try:
                res = d5.download(should_stop=lambda: blocked() is not None)
            except Exception as e:                        # never let the thread die
                d5.say(f"Download error: {type(e).__name__}: {str(e)[:150]}")
                d5.set_status(state="error")
                res = "error"
            if res == "done":
                d5.set_status(state="done")
        stop.wait(idle_s)
