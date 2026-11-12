"""The Upstox price feed in its own process (gun, 2.71).

Mandeep, 12 Nov 2026: "paper trading is not fetching the options price as the real movement ... data coming from all the
sources should be as fast as possible." The app's log showed the Upstox feed reset every 10-15 minutes in the morning
(09:23, 09:33, 09:48, 10:02: "Connection reset by peer"; once "449 of 449 ... no price update for minutes"). The feed's
reader shared one Python process (one GIL) with the whole analysis; when the analysis was busy, the reader fell behind,
Upstox's buffer filled and the server cut the connection, and prices froze until it reconnected.

Here the websocket (connect, pings, reading, protobuf decoding, reconnects) runs in a separate process that does
nothing else, so it always keeps up; the app only receives ready ticks through a pipe and applies them. Same interface
as live/upstox.py UpstoxFeed (start / stop / subscribe / unsubscribe / counts / connected / msgs / last_msg / lag_ms /
market_status). If the process cannot start, the old in-process reader is used, as before. The child process exits by
itself when the app goes away. Market data only: no order, no account call; the token stays in memory.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import threading
import time


def _child(token: str, cmd_q, out_conn, ppid: int, ws_url: str | None):
    """Runs in its own process: the websocket, (re)subscriptions, decoding; ticks go to the app through out_conn."""
    import json
    import queue
    import uuid

    import websocket

    from live import upstox_feed_pb2 as pb
    from live.upstox import WS_URL, decode

    url = ws_url or WS_URL
    subs: dict[str, set] = {"full": set(), "option_greeks": set(), "ltpc": set()}
    st = {"ws": None, "connected": False, "stop": False, "last": 0.0}
    out_lock, sub_lock = threading.Lock(), threading.Lock()

    def post(kind, payload):
        try:
            with out_lock:
                out_conn.send((kind, payload))
        except Exception:
            os._exit(0)                                   # the app is gone

    def send(method, keys, mode=None):
        ws = st["ws"]
        if not (ws and st["connected"]):
            return
        for i in range(0, len(keys), 500):
            req = {"guid": uuid.uuid4().hex, "method": method, "data": {"instrumentKeys": keys[i:i + 500]}}
            if mode:
                req["data"]["mode"] = mode
            try:
                ws.send(json.dumps(req).encode(), opcode=websocket.ABNF.OPCODE_BINARY)
            except Exception as e:
                post("status", f"Feed send failed: {e}")

    def commands():
        while True:
            try:
                c = cmd_q.get(timeout=1.0)
            except queue.Empty:
                if os.getppid() != ppid:
                    os._exit(0)
                continue
            except Exception:
                os._exit(0)
            if c[0] == "stop":
                st["stop"] = True
                try:
                    st["ws"] and st["ws"].close()
                except Exception:
                    pass
                return
            keys = [k for k in c[1] if k]
            with sub_lock:
                for m in subs:
                    subs[m].difference_update(keys)
                if c[0] == "sub":
                    subs[c[2]].update(keys)
            send(c[0], keys, c[2] if c[0] == "sub" else None)
    threading.Thread(target=commands, daemon=True, name="feed-commands").start()

    def on_open(ws):
        st["connected"] = True
        post("conn", True)
        post("status", "Live feed connected")
        with sub_lock:
            snap = {m: list(v) for m, v in subs.items()}
        for mode, keys in snap.items():
            if keys:
                send("sub", keys, mode)

    def on_message(ws, message):
        if isinstance(message, str):
            return
        meta = {}
        try:
            typ, ticks = decode(message, pb, meta)
        except Exception as e:
            post("status", f"Bad feed message: {e}")
            return
        st["last"] = time.time()
        post("ticks", (st["last"], meta.get("ts") or 0, meta.get("market"), ticks))

    def on_close(ws, code, msg):
        st["connected"] = False
        post("conn", False)
        if code in (401, 403):
            post("status", "Upstox rejected the token. Log in again.")
            st["stop"] = True

    backoff = 1
    sslopt = None
    try:
        import certifi
        sslopt = {"ca_certs": certifi.where()}
    except ImportError:
        pass
    while not st["stop"]:
        st["ws"] = websocket.WebSocketApp(url, header={"Authorization": f"Bearer {token}", "Accept": "*/*"},
                                          on_open=on_open, on_message=on_message,
                                          on_error=lambda ws, e: post("status", f"Feed error: {e}"), on_close=on_close)
        st["ws"].run_forever(ping_interval=20, ping_timeout=10, sslopt=sslopt if url.startswith("wss") else None)
        st["connected"] = False
        post("conn", False)
        if st["stop"]:
            break
        if st["last"] and time.time() - st["last"] < 60:
            backoff = 1
        post("status", f"Feed disconnected; reconnecting in {backoff}s")
        time.sleep(backoff)
        backoff = min(backoff * 2, 30)
        if os.getppid() != ppid:
            break
    post("stopped", True)


class ProcFeed:
    """The app's side: starts the feed process, passes subscriptions to it, receives ticks and hands them to on_ticks.
    Restarts the process if it dies (and re-subscribes); falls back to the in-process reader if it cannot start."""

    RESTARTS = 5                                          # in 10 minutes, then the in-process reader takes over

    def __init__(self, token: str, on_ticks, on_status=print, ws_url: str | None = None):
        self.token, self.on_ticks, self.on_status, self.ws_url = token, on_ticks, on_status, ws_url
        self.subs: dict[str, set] = {"full": set(), "option_greeks": set(), "ltpc": set()}
        self.lock = threading.Lock()
        self.stop_flag = threading.Event()
        self._connected, self._msgs, self._last, self._lag, self._market = False, 0, 0.0, None, {}
        self.pipe_ms = None                               # time a message waited between the feed process and the app
        self.proc = self.cmd_q = self.rx = None
        self._fb = None                                   # the in-process reader, only if the process cannot run
        self.restarts: list = []
        self.mode = "process"

    # the attributes the feed router and the System page read
    connected = property(lambda s: s._fb.connected if s._fb else s._connected)
    msgs = property(lambda s: s._fb.msgs if s._fb else s._msgs)
    last_msg = property(lambda s: s._fb.last_msg if s._fb else s._last)
    lag_ms = property(lambda s: s._fb.lag_ms if s._fb else s._lag)
    market_status = property(lambda s: s._fb.market_status if s._fb else s._market)

    def counts(self) -> dict:
        with self.lock:
            return {m: len(v) for m, v in self.subs.items()}

    def start(self):
        self.stop_flag.clear()
        try:
            self._spawn()
        except Exception as e:
            self._fallback(f"could not start the feed process ({e})")

    def _spawn(self):
        ctx = mp.get_context("spawn")
        self.cmd_q = ctx.Queue()
        r, w = ctx.Pipe(duplex=False)
        self.proc = ctx.Process(target=_child, args=(self.token, self.cmd_q, w, os.getpid(), self.ws_url), daemon=True,
                                name="upstox-feed")
        self.proc.start()
        w.close()
        self.rx = r
        with self.lock:
            snap = {m: list(v) for m, v in self.subs.items()}
        for mode, keys in snap.items():
            if keys:
                self.cmd_q.put(("sub", keys, mode))
        threading.Thread(target=self._rx_loop, args=(r,), daemon=True, name="upstox-feed-rx").start()

    def _fallback(self, why: str):
        from .upstox import UpstoxFeed
        self.on_status(f"Feed: {why}; using the in-process reader")
        self.mode = "thread"
        fb = UpstoxFeed(self.token, self.on_ticks, self.on_status)
        with self.lock:
            for m, v in self.subs.items():
                fb.subs[m] = set(v)
        fb.start()
        self._fb = fb

    def stop(self):
        self.stop_flag.set()
        if self._fb:
            self._fb.stop()
            return
        try:
            self.cmd_q and self.cmd_q.put(("stop",))
        except Exception:
            pass
        p = self.proc
        if p is not None:
            p.join(timeout=3)
            if p.is_alive():
                p.terminate()

    def subscribe(self, keys, mode):
        keys = [k for k in keys if k]
        if not keys:
            return
        with self.lock:
            for m in self.subs:
                self.subs[m].difference_update(keys)
            self.subs[mode].update(keys)
        if self._fb:
            return self._fb.subscribe(keys, mode)
        try:
            self.cmd_q and self.cmd_q.put(("sub", keys, mode))
        except Exception as e:
            self.on_status(f"Feed send failed: {e}")

    def unsubscribe(self, keys):
        keys = [k for k in keys if k]
        if not keys:
            return
        with self.lock:
            for m in self.subs:
                self.subs[m].difference_update(keys)
        if self._fb:
            return self._fb.unsubscribe(keys)
        try:
            self.cmd_q and self.cmd_q.put(("unsub", keys))
        except Exception as e:
            self.on_status(f"Feed send failed: {e}")

    def _rx_loop(self, r):
        while not self.stop_flag.is_set():
            try:
                kind, p = r.recv()
            except (EOFError, OSError):
                break
            except Exception as e:
                self.on_status(f"Feed message skipped: {e}")
                continue
            if kind == "ticks":
                sent, srv_ts, market, ticks = p
                now = time.time()
                self._msgs += 1
                self._last = now
                self.pipe_ms = (now - sent) * 1000
                if market:
                    self._market.update(market)
                if srv_ts:
                    lag = now * 1000 - srv_ts                 # Upstox server time -> applied in the app
                    if -5000 < lag < 120000:
                        self._lag = lag if self._lag is None else 0.8 * self._lag + 0.2 * lag
                if ticks:
                    try:
                        self.on_ticks(ticks)
                    except Exception as e:
                        self.on_status(f"Feed ticks skipped: {e}")
            elif kind == "conn":
                self._connected = bool(p)
            elif kind == "status":
                self.on_status(p)
                if "rejected the token" in str(p):
                    self.stop_flag.set()
            elif kind == "stopped":
                break
        self._connected = False
        if self.stop_flag.is_set() or self._fb:
            return
        now = time.time()                                # the process died: start a new one (re-subscribes)
        self.restarts = [t for t in self.restarts if now - t < 600] + [now]
        if len(self.restarts) > self.RESTARTS:
            self._fallback("the feed process stopped too often")
            return
        self.on_status("Feed process stopped; starting it again")
        time.sleep(1)
        try:
            self._spawn()
        except Exception as e:
            self._fallback(f"could not restart the feed process ({e})")

    def view(self) -> dict:
        p = self.proc
        return {"mode": self.mode, "pid": p.pid if p is not None and self.mode == "process" else None,
                "alive": bool(p is not None and p.is_alive()) if self.mode == "process" else None,
                "pipe_ms": None if self.pipe_ms is None else round(self.pipe_ms, 1), "restarts_10min": len(self.restarts)}
