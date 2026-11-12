"""Tanu's live feed (layer 1): the broker websocket in its own process, every message to the tick tape, decoded ticks
to the engine.

Why a process: the legacy app's reader shared Python with the analysis and fell behind (Upstox reset the connection
four times on 12 Nov morning). Here the child process only reads, writes the tape and decodes; the parent receives
ready ticks through a pipe.

Market data only (ADR-0001): the websocket URL passes safety.check_url; nothing is ever sent but subscriptions.
Before cutover nothing starts this outside tests (ADR-0007).
"""
from __future__ import annotations

import multiprocessing as mp
import os
import threading
import time

from ..core import log, safety

UPSTOX_WS = "wss://api.upstox.com/v3/feed/market-data-feed"
MODES = ("ltpc", "full", "option_greeks")
_log = log.get("feed")


def _child(url: str, token: str, cmd_q, out_conn, ppid: int, tape_dir: str | None, source: str, min_free_gb: float):
    """Runs in the feed process."""
    import json
    import queue
    import uuid

    import websocket

    from ..brokers.upstox.decode import decode
    from .tape import TapeWriter

    tape = TapeWriter(tape_dir, source, min_free_gb=min_free_gb) if tape_dir else None
    subs = {m: set() for m in MODES}
    st = {"ws": None, "up": False, "stop": False, "last": 0.0}
    out_lock, sub_lock = threading.Lock(), threading.Lock()

    def post(kind, payload):
        if out_conn is None:
            return
        try:
            with out_lock:
                out_conn.send((kind, payload))
        except Exception:
            st["stop"] = True                              # the app is gone

    def ctrl(event, **f):
        if tape:
            tape.control(event, **f)

    def send(method, keys, mode=None):
        ws = st["ws"]
        if not (ws and st["up"]) or not keys:
            return
        for i in range(0, len(keys), 500):
            data = {"instrumentKeys": keys[i:i + 500]}
            if mode:
                data["mode"] = mode
            try:
                ws.send(json.dumps({"guid": uuid.uuid4().hex, "method": method, "data": data}).encode(),
                        opcode=websocket.ABNF.OPCODE_BINARY)
            except Exception as e:
                post("status", f"send failed: {e}")

    def commands():
        while not st["stop"]:
            try:
                c = cmd_q.get(timeout=1.0)
            except queue.Empty:
                if os.getppid() != ppid:
                    st["stop"] = True
                    _close()
                continue
            except Exception:
                st["stop"] = True
                break
            if c[0] == "stop":
                st["stop"] = True
                _close()
                return
            keys = [k for k in c[1] if k]
            with sub_lock:
                for m in subs:
                    subs[m].difference_update(keys)
                if c[0] == "sub":
                    subs[c[2]].update(keys)
            ctrl(c[0], keys=keys, mode=c[2] if c[0] == "sub" else None)
            send(c[0], keys, c[2] if c[0] == "sub" else None)

    def _close():
        try:
            st["ws"] and st["ws"].close()
        except Exception:
            pass

    def on_open(ws):
        st["up"] = True
        ctrl("connect")
        post("conn", True)
        with sub_lock:
            snap = {m: sorted(v) for m, v in subs.items()}
        for m, keys in snap.items():
            send("sub", keys, m)

    def on_message(ws, msg):
        if isinstance(msg, str):
            return
        t = time.time()
        st["last"] = t
        if tape:
            tape.write(msg, t=t)
        if out_conn is not None:
            try:
                d = decode(msg)
            except Exception as e:
                post("status", f"bad message: {e}")
                return
            post("ticks", (t, d["server_ms"], d["market"], d["ticks"]))

    def on_close(ws, code, reason):
        st["up"] = False
        ctrl("disconnect", code=code)
        post("conn", False)
        if code in (401, 403):
            post("status", "login rejected")
            st["stop"] = True

    threading.Thread(target=commands, daemon=True, name="feed-commands").start()
    backoff = 1
    while not st["stop"]:
        st["ws"] = websocket.WebSocketApp(url, header={"Authorization": f"Bearer {token}", "Accept": "*/*"},
                                          on_open=on_open, on_message=on_message, on_close=on_close,
                                          on_error=lambda ws, e: post("status", f"error: {e}"))
        st["ws"].run_forever(ping_interval=20, ping_timeout=10)
        st["up"] = False
        if st["stop"] or os.getppid() != ppid:
            break
        backoff = 1 if time.time() - st["last"] < 60 else min(backoff * 2, 30)
        post("status", f"reconnecting in {backoff}s")
        time.sleep(backoff)
    if tape:
        tape.close()
    post("stopped", True)


class FeedProcess:
    """The app's handle on the feed process. on_ticks(ticks) gets each message's ticks; restarts a dead process (and
    resubscribes) up to RESTARTS times in 10 minutes."""

    RESTARTS = 5

    def __init__(self, token: str, on_ticks=None, url: str = UPSTOX_WS, tape_dir=None, source: str = "upstox",
                 min_free_gb: float = 10.0):
        self.url = safety.check_url(url)
        self._token, self.on_ticks = token, on_ticks
        self.tape_dir, self.source, self.min_free_gb = (str(tape_dir) if tape_dir else None), source, min_free_gb
        self.subs = {m: set() for m in MODES}
        self.lock = threading.Lock()
        self.stop_flag = threading.Event()
        self.connected, self.msgs, self.last_msg, self.lag_ms = False, 0, 0.0, None
        self.proc = self.cmd_q = None
        self.restarts: list = []

    def start(self) -> None:
        self.stop_flag.clear()
        self._spawn()

    def _spawn(self) -> None:
        ctx = mp.get_context("spawn")
        self.cmd_q = ctx.Queue()
        r, w = ctx.Pipe(duplex=False)
        self.proc = ctx.Process(target=_child, args=(self.url, self._token, self.cmd_q, w, os.getpid(), self.tape_dir,
                                                     self.source, self.min_free_gb), daemon=True, name="tanu-feed")
        self.proc.start()
        w.close()
        with self.lock:
            snap = {m: sorted(v) for m, v in self.subs.items()}
        for m, keys in snap.items():
            if keys:
                self.cmd_q.put(("sub", keys, m))
        threading.Thread(target=self._rx, args=(r,), daemon=True, name="tanu-feed-rx").start()
        _log.info("feed_started", pid=self.proc.pid, source=self.source)

    def subscribe(self, keys, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        keys = [k for k in keys if k]
        with self.lock:
            for m in self.subs:
                self.subs[m].difference_update(keys)
            self.subs[mode].update(keys)
        if self.cmd_q is not None and keys:
            self.cmd_q.put(("sub", keys, mode))

    def unsubscribe(self, keys) -> None:
        keys = [k for k in keys if k]
        with self.lock:
            for m in self.subs:
                self.subs[m].difference_update(keys)
        if self.cmd_q is not None and keys:
            self.cmd_q.put(("unsub", keys))

    def counts(self) -> dict:
        with self.lock:
            return {m: len(v) for m, v in self.subs.items()}

    def stop(self, timeout: float = 10.0) -> None:
        self.stop_flag.set()
        try:
            self.cmd_q and self.cmd_q.put(("stop",))
        except Exception:
            pass
        if self.proc is not None:
            self.proc.join(timeout)
            if self.proc.is_alive():
                self.proc.terminate()
        _log.info("feed_stopped", msgs=self.msgs)

    def _rx(self, r) -> None:
        while not self.stop_flag.is_set():
            try:
                kind, p = r.recv()
            except (EOFError, OSError):
                break
            if kind == "ticks":
                t, server_ms, market, ticks = p
                self.msgs += 1
                self.last_msg = time.time()
                if server_ms:
                    lag = self.last_msg * 1000 - server_ms
                    if -5000 < lag < 120000:
                        self.lag_ms = lag if self.lag_ms is None else 0.8 * self.lag_ms + 0.2 * lag
                if ticks and self.on_ticks:
                    try:
                        self.on_ticks(ticks)
                    except Exception as e:
                        _log.error("on_ticks_failed", error=str(e))
            elif kind == "conn":
                self.connected = bool(p)
                _log.info("feed_connected" if p else "feed_disconnected")
            elif kind == "status":
                _log.warning("feed_status", status=str(p))
                if p == "login rejected":
                    self.stop_flag.set()
            elif kind == "stopped":
                break
        self.connected = False
        if self.stop_flag.is_set():
            return
        now = time.time()
        self.restarts = [x for x in self.restarts if now - x < 600] + [now]
        if len(self.restarts) > self.RESTARTS:
            _log.error("feed_gave_up", restarts=len(self.restarts))
            return
        _log.warning("feed_restarting")
        time.sleep(1)
        self._spawn()
