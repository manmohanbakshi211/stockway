"""2.74: fill the day's gaps from real data after a late start or a restart (copied from Tanu's late-start backfill).

Before this, a restart in the middle of the day started the 1-minute candles again from that minute (13 Nov started at
09:19), so VWAP, the opening range, EMAs and every candle-based alert saw only part of the day, and the next save of
the day's candles replaced the morning in the recording. Now, when the live scanner starts after the open:

  1. Today's own recording first (exact, no calls): the 1-minute candles of every index and stock this app saved
     before the restart, and the last 45 minutes of every instrument's minute history (OI / price change alerts).
  2. Then Upstox's 1-minute intraday candles for the minutes still missing (the app was not running then), one
     underlying at a time at an easy pace so option chains and quotes are never kept waiting.

Live candles always win over filled ones (the same minute is never replaced). Market data only.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import threading
import time
import urllib.parse
from pathlib import Path

from .clock import IST
from .practice import PRACTICE

HIST_MIN = 45
PACE_S = 0.5                     # Upstox calls of the fill: at most 2 a second (the limiter allows 15)


def _minute(ts: float) -> int:
    return int(float(ts) // 60) * 60


def recording_bars(path: Path, t0: float, t1: float) -> dict[str, list[list]]:
    """{sym: [[t, o, h, l, c, v], ...]} saved in today's recording between t0 and t1."""
    if not Path(path).exists():
        return {}
    out: dict[str, list[list]] = {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    try:
        for s, t, o, h, l, c, v in con.execute("SELECT sym, t, o, h, l, c, v FROM bars WHERE t >= ? AND t < ? ORDER BY t", (t0, t1)):
            out.setdefault(s, []).append([_minute(t), o, h, l, c, v or 0.0])
    except sqlite3.Error:
        return {}
    finally:
        con.close()
    return out


def recording_hist(path: Path, since: float, until: float) -> dict[str, list[tuple]]:
    """{key: [(minute, ltp, vol, oi, iv), ...]}: the minute snapshots of every instrument in today's recording."""
    if not Path(path).exists():
        return {}
    out: dict[str, list[tuple]] = {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    try:
        for ts, k, ltp, vol, oi, iv in con.execute(
                "SELECT ts, key, ltp, vol, oi, iv FROM minutes WHERE ts >= ? AND ts < ? ORDER BY ts", (since, until)):
            if ltp:
                out.setdefault(k, []).append((int(ts), ltp, vol, oi, iv))
    except sqlite3.Error:
        return {}
    finally:
        con.close()
    return out


def upstox_rows(candles: list) -> list[list]:
    """Upstox intraday candles ([iso time, o, h, l, c, volume, oi], newest first) -> [[t, o, h, l, c, v]] oldest first."""
    out = []
    for c in candles or []:
        try:
            t = dt.datetime.fromisoformat(str(c[0])).timestamp()
            out.append([_minute(t), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5] or 0)])
        except (TypeError, ValueError, IndexError):
            continue
    return sorted(out)


def missing_minutes(rows: list[list], t0: float, t1: float) -> int:
    have = {int(r[0]) for r in rows}
    return sum(1 for m in range(_minute(t0), _minute(t1), 60) if m not in have)


def fill(engine, now: dt.datetime | None = None, pace_s: float = PACE_S, should_stop=lambda: False) -> dict:
    """Runs once at the scanner's start (background thread). Returns what it filled (also logged)."""
    from . import session as SESSION
    now = (now or engine.clock.now()).astimezone(IST)
    ses = SESSION.session(now.date())
    out = {"recording_bars": 0, "recording_syms": 0, "hist_keys": 0, "upstox_bars": 0, "upstox_syms": 0, "upstox_failed": 0}
    if ses is None or now <= ses["open"] or engine.state is None:
        return {**out, "skipped": "not after today's open"}
    t0, t1 = ses["open"].timestamp(), min(now, ses["close"]).timestamp()
    cur = _minute(now.timestamp())
    st = engine.state
    rec = getattr(engine.recorder, "path", None)
    # 1. today's own recording (what this app saved before the restart)
    if rec is not None:
        bars = recording_bars(rec, t0, cur)
        for sym, rows in bars.items():
            n = st.fill_bars(sym, rows, before=cur)
            if n:
                out["recording_bars"] += n
                out["recording_syms"] += 1
        hist = recording_hist(rec, max(t0, cur - HIST_MIN * 60), cur)
        out["hist_keys"] = sum(1 for k, rows in hist.items() if st.fill_hist(k, rows))
    # 2. Upstox candles for what is still missing (indices first)
    rest = getattr(engine, "rest", None)
    if rest is not None and hasattr(rest, "intraday_1m"):
        unis = sorted(engine.uni.underlyings.items(), key=lambda kv: (not getattr(kv[1], "is_index", False), kv[0]))
        for sym, u in unis:
            if should_stop():
                break
            with st.lock:
                have = [list(b) for b in tuple(st.bars[sym].bars)] if sym in st.bars else []
            if missing_minutes(have, t0, cur) == 0:
                continue
            try:
                rows = upstox_rows(rest.intraday_1m(u.key))
            except PermissionError:
                raise
            except Exception:
                out["upstox_failed"] += 1
                continue
            n = st.fill_bars(sym, [r for r in rows if t0 <= r[0] < cur], before=cur)
            if n:
                out["upstox_bars"] += n
                out["upstox_syms"] += 1
            time.sleep(pace_s)
    return out


def start(engine) -> threading.Thread | None:
    """Starts the fill in its own thread (never holds up the start, the analysis loop or the REST worker)."""
    if getattr(engine, "source", None) in PRACTICE:     # 2.76: practice sources have nothing to fill
        return None

    def run():
        try:
            r = fill(engine, should_stop=lambda: engine.stop_evt.is_set())
            if r.get("skipped"):
                return
            got = r["recording_bars"] + r["upstox_bars"]
            engine.status["gapfill"] = {**r, "at": time.time()}
            if got or r["hist_keys"]:
                engine.log(f"Filled today's missing 1-minute candles: {r['recording_bars']:,} from today's recording "
                           f"({r['recording_syms']} symbols), {r['upstox_bars']:,} from Upstox ({r['upstox_syms']} symbols); "
                           f"minute history restored for {r['hist_keys']:,} instruments"
                           + (f"; {r['upstox_failed']} symbols could not be fetched" if r["upstox_failed"] else ""))
                if hasattr(engine, "_save_bars"):
                    engine._save_bars()            # the recording holds the whole day again
        except PermissionError as e:
            engine.log(f"Filling today's candles stopped: {e}")
        except Exception as e:
            engine.log(f"Filling today's candles failed (the day continues with live candles only): {e}")

    th = threading.Thread(target=run, daemon=True, name="gap-fill")
    th.start()
    return th


def intraday_path(key: str) -> str:
    return f"/v3/historical-candle/intraday/{urllib.parse.quote(key, safe='')}/minutes/1"
