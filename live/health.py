"""Health monitor (gun): makes sure the app is really collecting data during market hours, and says so when it isn't.

Runs in the engine's watcher thread (every ~20 s, whether or not the scanner is running). Thresholds, the keep-awake
window and NSE holidays are bullets in rules/health.json. It only reads: the app's own state, the disk, and on a Mac
the power source (`pmset -g batt`, read-only). The one thing it starts is macOS's built-in `caffeinate -i -w <app>`
during market hours (a temporary "don't sleep while idle" request that ends at the close or when the app quits; no
setting is changed). Notices go to Telegram (if set) and the System page. Nothing here touches trading.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .clock import CLOSE, IST, OPEN

FILE = Path(__file__).resolve().parents[1] / "rules" / "health.json"
DEFAULTS = {"gap_alert_s": 150, "scanner_down_min": 3, "recording_stall_min": 4, "errors_10min": 12, "disk_warn_gb": 10,
            "battery_warn": True, "keep_awake": True, "keep_awake_from": "08:50", "keep_awake_to": "15:45",
            "daily_summary_at": "15:40", "repeat_min": 30, "holidays": {}}
ERR = re.compile(r"fail|error|rejected|refused|stopped|disconnected|skipped", re.I)
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


def is_trading_day(day: dt.date, c: dict | None = None) -> bool:
    c = c or cfg()
    hol = set((c.get("holidays") or {}).get(str(day.year)) or [])
    return day.weekday() < 5 and day.isoformat() not in hol


def market_window(day: dt.date) -> tuple[float, float]:
    return (dt.datetime.combine(day, OPEN, IST).timestamp(), dt.datetime.combine(day, CLOSE, IST).timestamp())


def market_overlap(t0: float, t1: float, c: dict | None = None) -> float:
    """Seconds of market time (trading days, 9:15-15:30) between t0 and t1."""
    if t1 <= t0:
        return 0.0
    out, d = 0.0, dt.datetime.fromtimestamp(t0, IST).date()
    last = dt.datetime.fromtimestamp(t1, IST).date()
    while d <= last:
        if is_trading_day(d, c):
            a, b = market_window(d)
            out += max(0.0, min(b, t1) - max(a, t0))
        d += dt.timedelta(days=1)
    return out


def _hm(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, IST).strftime("%H:%M")


class Health:
    def __init__(self, engine):
        self.e = engine
        self.last = None
        self.last_check = None
        self.notices = collections.deque(maxlen=40)
        self.sent: dict = {}                       # notice key -> last time sent
        self.gaps = collections.deque(maxlen=60)   # {"from", "to", "mins"}
        self.awake = None                          # caffeinate process
        self.down_since = None
        self.rec_seen = (None, None)               # (recorder rows, since)
        self.errs = collections.deque(maxlen=500)  # (time, text)
        self.last_line = None
        self.summary_day = None
        self.battery_at = 0.0
        self.state: dict = {}
        self.saved_at = 0.0
        try:                                       # when the app was last seen alive: a restart after a closed app
            ls = engine.store.get("health:last_seen")  # during market hours counts as a gap too
            self.last = float(ls) if ls else None
        except Exception:
            self.last = None

    # ------------------------------------------------------------ notices
    def notify(self, key: str, text: str, every_min: float | None = None, force: bool = False) -> bool:
        c = cfg()
        rep = float(every_min if every_min is not None else c["repeat_min"]) * 60
        t = time.time()
        if not force and t - self.sent.get(key, -1e18) < rep:
            return False
        self.sent[key] = t
        self.notices.appendleft({"ts": t, "at": _hm(t), "key": key, "text": text})
        try:
            self.e.telegram.send(text)
        except Exception:
            pass
        try:
            self.e.log(f"Health: {text}")
        except Exception:
            pass
        return True

    # ------------------------------------------------------------ the check
    def tick(self, now: dt.datetime | None = None):
        c = cfg()
        t = time.time()
        now = now or dt.datetime.now(IST)
        trading = is_trading_day(now.date(), c)
        o, cl = market_window(now.date())
        in_mkt = trading and o <= t <= cl
        self.state.update(trading_day=trading, market_open=in_mkt)
        self._gap(t, c)
        self.last = t
        self.last_check = t
        if t - self.saved_at >= 60:
            self.saved_at = t
            try:
                self.e.store.put("health:last_seen", t)
            except Exception:
                pass
        for fn in (self._scanner, self._recording, self._errors, self._disk, self._battery, self._keep_awake, self._summary):
            try:
                fn(now, t, c, trading, in_mkt)
            except Exception as ex:
                self.state[f"err_{fn.__name__}"] = str(ex)[:120]

    def _gap(self, t, c):
        if self.last is None or t - self.last <= float(c["gap_alert_s"]):
            return
        lost = market_overlap(self.last, t, c)
        g = {"from": self.last, "to": t, "mins": round((t - self.last) / 60, 1), "market_mins": round(lost / 60, 1)}
        self.gaps.appendleft(g)
        try:
            day = dt.datetime.fromtimestamp(t, IST).date().isoformat()
            k = f"health:gaps:{day}"
            self.e.store.put(k, (self.e.store.get(k, []) or []) + [g])
        except Exception:
            pass
        if lost >= 60:
            d0, d1 = dt.datetime.fromtimestamp(self.last, IST), dt.datetime.fromtimestamp(t, IST)
            span = f"{_hm(self.last)} to {_hm(t)}" if d0.date() == d1.date() else f"{d0:%d %b %H:%M} to {d1:%d %b %H:%M}"
            self.notify(f"gap:{int(self.last)}", f"⚠️ Stockway was not running from {span} ({g['market_mins']:.0f} market "
                        "minutes): the Mac slept, the app was closed or it froze. Nothing was recorded in that time.", force=True)

    def _scanner(self, now, t, c, trading, in_mkt):
        e = self.e
        lc = e.cfg().get("live") or {}
        from . import session as SESSION            # 2.48: the NSE calendar (special sessions such as the Muhurat too)
        ses = SESSION.session(now.date())
        expected = (ses is not None and ses["open"] + dt.timedelta(minutes=1) <= now < ses["close"] - dt.timedelta(minutes=5)
                    and lc.get("source", "upstox") == "upstox" and lc.get("auto_start", True)
                    and getattr(e, "manual_stop_day", None) != now.date())
        if not expected or e.running:
            self.down_since = None
            self.state["scanner"] = "running" if e.running else ("not expected" if not expected else "?")
            return
        self.down_since = self.down_since or t
        self.state["scanner"] = f"down since {_hm(self.down_since)}"
        if t - self.down_since >= float(c["scanner_down_min"]) * 60:
            st = (e.status or {}).get("state")
            why = (e.status or {}).get("message") or st or "not started"
            hint = "Log in with Upstox in the app (Live page)." if st == "login_needed" else "Open the app: it starts by itself (press Resume on the Live page if you paused it)."
            self.notify("scanner_down", f"⚠️ Stockway: the live scanner is not running ({why}). {hint} Data is not being recorded.")

    def _recording(self, now, t, c, trading, in_mkt):
        e = self.e
        rec = getattr(e, "recorder", None)
        if not (in_mkt and e.running and rec is not None and now.strftime("%H:%M") >= "09:20"):
            self.rec_seen = (None, None)
            return
        rows = getattr(rec, "rows", None)
        if rows != self.rec_seen[0]:
            self.rec_seen = (rows, t)
            self.state["recording"] = "growing"
            return
        if t - self.rec_seen[1] >= float(c["recording_stall_min"]) * 60:
            self.state["recording"] = f"stuck since {_hm(self.rec_seen[1])}"
            self.notify("recording_stall", f"⚠️ Stockway: today's recording has not grown since {_hm(self.rec_seen[1])}. "
                        "Check the System page (Problems in the last hour).")

    def _errors(self, now, t, c, trading, in_mkt):
        lines = list(getattr(self.e, "logs", []) or [])
        new = []
        for ln in reversed(lines):
            if ln == self.last_line:
                break
            new.append(ln)
        if lines:
            self.last_line = lines[-1]
        for ln in reversed(new):
            if ERR.search(ln) and "Health:" not in ln:
                self.errs.append((t, ln[10:].strip()))
        recent = [x for x in self.errs if t - x[0] <= 600]
        self.state["errors_10min"] = len(recent)
        if in_mkt and len(recent) >= int(c["errors_10min"]):
            top = collections.Counter(re.sub(r"\d+", "#", x[1])[:70] for x in recent).most_common(3)
            self.notify("errors", "⚠️ Stockway: many problems in the last 10 minutes: " +
                        "; ".join(f"{k} ({n}x)" for k, n in top) + ". Details on the System page.")

    def _disk(self, now, t, c, trading, in_mkt):
        free = shutil.disk_usage(Path(self.e.data_dir)).free / 1e9
        self.state["disk_free_gb"] = round(free, 1)
        if free < float(c["disk_warn_gb"]):
            self.notify("disk", f"⚠️ Stockway: only {free:.1f} GB free on the Mac. Recording needs room: free some space "
                        "(old downloads, Trash).", every_min=24 * 60)

    def _battery(self, now, t, c, trading, in_mkt):
        if sys.platform != "darwin" or not c.get("battery_warn") or not trading:
            return
        if not (str(c["keep_awake_from"]) <= now.strftime("%H:%M") <= str(c["keep_awake_to"])) or t - self.battery_at < 600:
            return
        self.battery_at = t
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
        on_batt = "Battery Power" in out
        self.state["power"] = "battery" if on_batt else "charger"
        if on_batt:
            pct = re.search(r"(\d+)%", out)
            self.notify("battery", f"🔌 Stockway: the Mac is on battery{f' ({pct.group(1)}%)' if pct else ''}. "
                        "Plug in the charger so it doesn't sleep during the market.", every_min=120)

    def _keep_awake(self, now, t, c, trading, in_mkt):
        want = (sys.platform == "darwin" and c.get("keep_awake") and trading
                and str(c["keep_awake_from"]) <= now.strftime("%H:%M") <= str(c["keep_awake_to"]))
        alive = self.awake is not None and self.awake.poll() is None
        if want and not alive:
            self.awake = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.state["keep_awake"] = "on"
        elif not want and alive:
            self.awake.terminate()
            self.awake = None
            self.state["keep_awake"] = "off (outside market hours)"
        elif not want:
            self.state["keep_awake"] = "off" if sys.platform == "darwin" else "not a Mac"

    def _summary(self, now, t, c, trading, in_mkt):
        if not trading or now.strftime("%H:%M") < str(c["daily_summary_at"]) or self.summary_day == now.date():
            return
        if now.strftime("%H:%M") > "18:00":                 # app opened late: no stale summary
            self.summary_day = now.date()
            return
        self.summary_day = now.date()
        e = self.e
        day = now.date().isoformat()
        rec = getattr(e, "recorder", None)
        cnt = dict(getattr(rec, "counts", {}) or {})
        try:
            from . import vault
            today = next((d for d in vault.days(e.data_dir, e.source or "upstox", 5) if d["day"] == day), None)
        except Exception:
            today = None
        bench = 0
        try:
            bench = sum(v.get("opened", 0) for v in (e.bench.status().get("styles") or {}).values()) if e.bench else 0
        except Exception:
            pass
        gaps = [g for g in (e.store.get(f"health:gaps:{day}", []) or []) if g.get("market_mins", 0) >= 1]
        lost = sum(g["market_mins"] for g in gaps)
        parts = [f"📊 Stockway today: {today['mb'] if today else 0} MB recorded",
                 f"{cnt.get('chains', 0):,} option chains", f"{cnt.get('news', 0):,} headlines",
                 f"{cnt.get('candles', 0):,} candle patterns", f"{bench:,} test-bench trades"]
        txt = ", ".join(parts) + "."
        txt += f" Gaps: {len(gaps)} ({lost:.0f} market minutes lost)." if gaps else " No gaps: the whole session was recorded."
        self.notify(f"summary:{day}", txt, force=True)

    # ------------------------------------------------------------ for the System page
    def view(self) -> dict:
        c = cfg()
        day = dt.datetime.now(IST).date().isoformat()
        try:
            gaps = self.e.store.get(f"health:gaps:{day}", []) or []
        except Exception:
            gaps = []
        return {"last_check_s": round(time.time() - self.last_check) if self.last_check else None, "state": dict(self.state),
                "gaps_today": [{**g, "from_hm": _hm(g["from"]), "to_hm": _hm(g["to"])} for g in gaps][-10:],
                "notices": list(self.notices)[:12], "keep_awake": c.get("keep_awake"), "error": _C.get("error"),
                "window": f"{c['keep_awake_from']}-{c['keep_awake_to']}"}
