"""The supervisor: `python -m tanu run` (what "Start Tanu.command" runs).

Before cutover (settings `live` false): only the app on this machine (view only). After cutover (ADR-0009), every
trading day: wait for 08:50 and the day's Upstox login, download the instrument list, start the session (feed process,
live loop, engine, paper accounts, alerts), close the day at 15:30, then the end-of-day jobs (bars from the tape, gap
fill from broker candles, measured costs, evening review, a summary on Telegram). The web app runs the whole time.

Gates (checked before any broker connection): the switch is on, the legacy app is not answering on its port, no
other Tanu is live (lock file), and the token is valid. A failed gate keeps Tanu in view-only mode and says why."""
from __future__ import annotations

import datetime as dt
import json
import os
import socket
import threading
import time
from pathlib import Path

from ..core import config, log, paths
from ..core.secrets import Secrets
from ..data import instruments
from ..data.facts import Facts

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_log = log.get("supervisor")


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:                      # exists, owned by someone else
        return True
    except OSError:
        return False


class LiveLock:
    """One live Tanu per machine (one feed, one token keeper: ADR-0007)."""

    def __init__(self, base: Path):
        self.path = Path(base) / "live" / "live.lock"

    def holder(self) -> int | None:
        try:
            pid = int(self.path.read_text().strip())
        except Exception:
            return None
        return pid if pid != os.getpid() and _pid_alive(pid) else None

    def take(self) -> bool:
        if self.holder():
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(str(os.getpid()))
        return True

    def release(self) -> None:
        try:
            if self.path.read_text().strip() == str(os.getpid()):
                self.path.unlink()
        except Exception:
            pass


def gates(s: config.Settings, base: Path, *, web_running_here: bool = False) -> list[str]:
    """Reasons Tanu may not open a broker connection now (empty = clear)."""
    why = []
    if not s.live:
        why.append("Cutover switch is off: Tanu is view-only until Mandeep's go (python -m tanu cutover).")
    if not web_running_here and port_in_use(s.legacy_port):
        why.append(f"Something already answers on port {s.legacy_port} (the legacy app?). Close it first: one live feed at a time.")
    h = LiveLock(base).holder()
    if h:
        why.append(f"Another Tanu is already live (process {h}).")
    return why


def cutover(base: Path, undo: bool = False, s: config.Settings | None = None) -> str:
    """Turns the live switch on (or off with undo) in this machine's local.json."""
    s = s or config.load()
    p = Path(base) / "local.json"
    cur = json.loads(p.read_text()) if p.exists() else {}
    if undo:
        cur.pop("live", None)
        cur.pop("web_port", None)
        msg = "Live switch OFF: Tanu is view-only again (port 8790). The legacy app can be started with Start Radar."
    else:
        if port_in_use(s.legacy_port):
            return f"Not switched: something answers on port {s.legacy_port} (the legacy app?). Close it, then run this again."
        cur.update(live=True, web_port=s.legacy_port)
        msg = (f"Live switch ON: start Tanu with 'Start Tanu.command'. App: http://127.0.0.1:{s.legacy_port} . "
               "Undo any time: python -m tanu cutover --undo")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cur, indent=1))
    _log.info("cutover_switch", on=not undo)
    return msg


def instruments_for(day: str, base: Path) -> list[dict]:
    d = base / "instruments"
    rows: list[dict] = []
    for name, url in (("NSE", instruments.UPSTOX_NSE), ("BSE", instruments.UPSTOX_BSE)):
        f = d / f"{day}_{name}.json.gz"
        if not f.exists():
            try:
                instruments.download(f, url)
            except Exception as e:
                old = sorted(d.glob(f"*_{name}.json.gz"))
                _log.warning("instruments_download_failed", exchange=name, error=str(e), using=old[-1].name if old else None)
                if not old:
                    continue
                f = old[-1]
        rows += instruments.load(f)
    return rows


def evidence(base: Path) -> dict:
    """{setup_id: one line of evidence} from the latest research reports."""
    out = {}
    r = base / "research"
    for p in sorted([p for p in r.glob("*") if (p / "report.json").exists()]) if r.exists() else []:
        try:
            rep = json.loads((p / "report.json").read_text())
        except Exception:
            continue
        for x in rep.get("setups", []):
            sid = x["setup"].split("__")[0]
            le, ho = x.get("learn", {}), x.get("hold", {})
            out[sid] = (f"Evidence: learn {le.get('avg_pct')}% x {le.get('n')}, unseen {ho.get('avg_pct')}% x {ho.get('n')} "
                        f"a trade after costs")
    return out


def rank(base: Path) -> list[str]:
    f = sorted((base / "costs").glob("tradeable_*.json"))
    if not f:
        return []
    try:
        items = json.loads(f[-1].read_text()).get("items", [])
    except Exception:
        return []
    return [i["sym"] for i in sorted(items, key=lambda i: (not i.get("tradeable"), i["cost"]["total_pct"]))]


def end_of_day(day: str, base: Path, token: str | None, sender=None) -> dict:
    """Bars from the tape, gap fill from broker candles, measured costs, evening review."""
    import pandas as pd

    from ..brokers.upstox.rest import UpstoxRest
    from ..costs import daily
    from ..data import bars, gaps
    from ..journal import review
    from ..journal.store import Journal
    rep: dict = {"day": day}
    try:
        f = bars.build_day(base / "tape", "upstox", day, base / "bars")
        df = pd.read_parquet(f)
        st = json.loads((base / "live" / "status.json").read_text()) if (base / "live" / "status.json").exists() else {}
        keys = sorted(set(df["key"]))
        idx = [k for k in keys if "INDEX" in k or "_EQ|" in k]          # underlyings: worth a broker fill
        g = gaps.missing(df, idx, day)
        if g and token:
            rest = UpstoxRest(token)
            df, rep["gaps"] = gaps.fill(df, g, rest.intraday_1m)
            df.to_parquet(f, compression="zstd", index=False)
        rep["bars"] = {"rows": len(df), "instruments": len(keys), "late_ticks": (st.get("counts") or {}).get("late")}
    except Exception as e:
        rep["bars_error"] = str(e)
        _log.error("eod_bars_failed", error=str(e))
    try:
        rep["costs"] = daily.run(day, base, paths.LEGACY_DATA)[1]
    except Exception as e:
        rep["costs_error"] = str(e)
    try:
        j = Journal(base / "store" / "journal.db")
        rv = review.daily(j, day)
        review.write(rv, base)
        rep["review"] = {a: x["summary"] for a, x in rv.get("accounts", {}).items()}
    except Exception as e:
        rep["review_error"] = str(e)
    (base / "live" / f"eod_{day}.json").write_text(json.dumps(rep, indent=1, default=str))
    if sender is not None:
        sn = (rep.get("review") or {}).get("sniper") or {}
        sh = (rep.get("review") or {}).get("shadow") or {}
        sender.send(f"Tanu day closed {day}\nSniper: {sn.get('trades', 0)} trades, ₹{sn.get('pnl_rs', 0):,.0f}\n"
                    f"Shadow graded {sh.get('trades', 0)} signals\nReview: Journal page")
    _log.info("eod_done", day=day)
    return rep


class Supervisor:
    def __init__(self, s: config.Settings | None = None):
        self.s = s or config.load()
        self.d = paths.ensure(self.s)
        self.base = self.d["base"]
        log.setup(self.d["logs"], self.s.log_level, self.s.log_keep_days)
        self.facts = Facts()
        self.secrets = Secrets(self.base)
        from ..brokers.upstox.auth import TokenKeeper
        from ..notify.telegram import Telegram
        self.keeper = TokenKeeper(self.secrets)
        self.telegram = Telegram(self.secrets)
        self.session = None
        self.state = {"mode": "view", "why": [], "since": time.time()}
        self.stop = threading.Event()
        from ..detect.book import AlertBook
        from ..intel.collect import Collector
        self.book = AlertBook(self.base / "live" / "alerts")
        self.collector = Collector(self.base, on_alert=self.book.add)
        from ..ai.client import AI
        self.ai = AI(self.base, self.secrets)

    def web(self, port_check: bool = True):
        import uvicorn

        from ..ui.api import create_app
        if port_check and port_in_use(self.s.web_port):
            raise SystemExit(f"Port {self.s.web_port} is busy: is Tanu (or the legacy app) already open?")
        app = create_app(self.base, paths.LEGACY_DATA / "history", settings=self.s, keeper=self.keeper,
                         telegram=self.telegram, supervisor=self)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.s.web_port, log_level="warning"))
        threading.Thread(target=server.run, daemon=True, name="tanu-web").start()
        _log.info("web_started", port=self.s.web_port)
        return server

    def run(self) -> int:
        why = gates(self.s, self.base)
        server = self.web()
        print(f"Tanu app: http://127.0.0.1:{self.s.web_port}")
        if why:
            self.state.update(mode="view", why=why)
            for w in why:
                print(" -", w)
            _log.info("view_only", why=why)
            try:
                while not self.stop.is_set():
                    self.stop.wait(1)
            except KeyboardInterrupt:
                pass
            server.should_exit = True
            return 0
        lock = LiveLock(self.base)
        if not lock.take():
            print("Another Tanu is already live.")
            return 1
        threading.Thread(target=self.collector.run_forever, args=(self.stop,), daemon=True, name="tanu-intel").start()
        threading.Thread(target=self._notes, daemon=True, name="tanu-ai-notes").start()
        try:
            self._days()
        except KeyboardInterrupt:
            pass
        finally:
            if self.session:
                self.session.stop()
            lock.release()
            server.should_exit = True
        return 0

    def _notes(self) -> None:
        """After the switch-over: the morning and evening AI notes at their times on trading days (models.json),
        also sent to Telegram. A failure is logged and tried again a few minutes later."""
        from ..ai import notes
        retry: dict[str, float] = {}
        while not self.stop.is_set():
            now = dt.datetime.now(IST)
            for kind, at in self.ai.spec.get("notes_at", {}).items():
                if time.time() < retry.get(kind, 0) or not self.ai.available():
                    continue
                if notes.due(kind, self.base, now, at, self.facts.session(now.date().isoformat()) is not None):
                    try:
                        r = notes.write(kind, self.base, self.ai, self.collector, self.book, user=False)
                        self.telegram.send(f"Tanu {kind} note ({r['day']}):\n{r['text'][:3500]}")
                    except Exception as e:
                        retry[kind] = time.time() + 600
                        _log.warning("ai_note_failed", kind=kind, error=str(e)[:200])
            self.stop.wait(60)

    def _set(self, mode: str, *why: str) -> None:
        if self.state.get("mode") != mode or self.state.get("why") != list(why):
            self.state = {"mode": mode, "why": list(why), "since": time.time()}
            _log.info("supervisor_state", mode=mode, why=list(why))

    def _days(self) -> None:
        from .session import Session
        told_login = None
        while not self.stop.is_set():
            now = dt.datetime.now(IST)
            day = now.date().isoformat()
            sess = self.facts.session(day)
            if sess is None or now > sess[1] + dt.timedelta(minutes=20):
                self._set("idle", "Market closed. Next start 08:50 on the next trading day.")
                self.stop.wait(60)
                continue
            if now < sess[0] - dt.timedelta(minutes=25):
                self._set("idle", f"Waiting for 08:50 ({day}).")
                self.stop.wait(30)
                continue
            token = self.keeper.token()
            if not token:
                self._set("login", f"Log in with Upstox: http://127.0.0.1:{self.s.web_port} (System page).")
                if told_login != day:
                    told_login = day
                    self.telegram.send(f"Tanu: log in with Upstox for {day} (System page on the Mac).")
                self.stop.wait(10)
                continue
            self._set("live", f"Live for {day}.")
            rows = instruments_for(day, self.base)
            self.session = Session(self.base, day, rows, url=self._ws_url(), token=token, out_dir=self.base / "live",
                                   journal_path=self.base / "store" / "journal.db", tape_dir=self.base / "tape",
                                   rank=rank(self.base), sender=self.telegram, evidence=evidence(self.base),
                                   min_free_gb=self.s.min_free_disk_gb)
            late = now >= sess[0] + dt.timedelta(minutes=1)
            if late:                                      # started after the open: the morning comes from broker candles
                from ..brokers.upstox.rest import UpstoxRest
                rest = UpstoxRest(token)
                n = self.session.backfill(rest.intraday_1m, now.timestamp())
                _log.info("backfilled_morning", minutes=n)
            self.session.start()
            self._watch(sess[1])
            self.session.stop()
            self._set("eod", f"End-of-day jobs for {day}.")
            end_of_day(day, self.base, self.keeper.token(), self.telegram)
            self.session = None
            self._set("idle", "Day done. Next start 08:50 on the next trading day.")

    def _ws_url(self) -> str:
        from ..data.feed import UPSTOX_WS
        return UPSTOX_WS

    def _watch(self, close: dt.datetime) -> None:
        """Until the close (+5 min for late prints): restart the feed with a new token if Upstox rejected the old one."""
        told, dead_since = False, None
        while not self.stop.is_set() and dt.datetime.now(IST) < close + dt.timedelta(minutes=5):
            f = self.session.feed
            gave_up = f.gave_up and not f.stop_flag.is_set()
            if gave_up and not self.session.loop.done:            # restarted too often: pause 30 s, then try again
                dead_since = dead_since or time.time()
                if time.time() - dead_since > 30:
                    f.restarts.clear()
                    f.start()
                    dead_since = None
                    _log.warning("feed_restarted_after_pause")
            elif f.stop_flag.is_set() and not self.session.loop.done:
                tok = self.keeper.token()
                if tok and tok != f._token:
                    f._token = tok
                    f.restarts.clear()
                    f.start()
                    told = False
                    _log.info("feed_restarted_with_new_login")
                elif not told:
                    told = True
                    self._set("login", "Upstox stopped the feed (login). Log in again on the System page.")
                    self.telegram.send("Tanu: Upstox stopped the feed. Log in again on the System page.")
            self.stop.wait(2)
        self.session.loop.finish()
