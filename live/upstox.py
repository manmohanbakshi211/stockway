"""Upstox login (OAuth), REST calls with rate limiting, and the live market-data websocket."""
from __future__ import annotations

import collections
import datetime as dt
import json
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

import requests

from .clock import IST

API = "https://api.upstox.com"
WS_URL = "wss://api.upstox.com/v3/feed/market-data-feed"


# ----------------------------------------------------------------- login
def login_url(api_key: str, redirect_uri: str, state: str = "stockway") -> str:
    q = urllib.parse.urlencode({"response_type": "code", "client_id": api_key,
                                "redirect_uri": redirect_uri, "state": state})
    return f"{API}/v2/login/authorization/dialog?{q}"


def exchange_code(code: str, api_key: str, api_secret: str, redirect_uri: str) -> dict:
    r = requests.post(f"{API}/v2/login/authorization/token",
                      headers={"accept": "application/json",
                               "Content-Type": "application/x-www-form-urlencoded"},
                      data={"code": code, "client_id": api_key, "client_secret": api_secret,
                            "redirect_uri": redirect_uri, "grant_type": "authorization_code"},
                      timeout=30)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or "access_token" not in body:
        msg = (body.get("errors") or [{}])[0].get("message") if body.get("errors") else r.text[:200]
        raise RuntimeError(f"Upstox login failed: {msg}")
    return body


def token_expiry(now: dt.datetime | None = None) -> dt.datetime:
    """Upstox tokens expire at 3:30 AM IST the next morning."""
    now = now or dt.datetime.now(IST)
    exp = now.replace(hour=3, minute=30, second=0, microsecond=0)
    return exp if now < exp else exp + dt.timedelta(days=1)


class TokenStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def save(self, body: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"access_token": body["access_token"], "user": body.get("user_name") or body.get("user_id"),
               "saved_at": dt.datetime.now(IST).isoformat(), "expires_at": token_expiry().isoformat()}
        self.path.write_text(json.dumps(rec))
        try:
            self.path.chmod(0o600)
        except Exception:
            pass

    def load(self) -> dict | None:
        if not self.path.exists():
            return None
        try:
            rec = json.loads(self.path.read_text())
        except Exception:
            return None
        if dt.datetime.fromisoformat(rec["expires_at"]) <= dt.datetime.now(IST):
            return None
        return rec

    def clear(self):
        self.path.unlink(missing_ok=True)


# ----------------------------------------------------------------- REST with rate limits
class RateLimiter:
    """Stay under Upstox's limits (25/s, 250/min, 1000/30 min) with margin."""
    WINDOWS = ((1.0, 15), (60.0, 180), (1800.0, 850))

    def __init__(self):
        self.lock = threading.Lock()
        self.calls = collections.deque()

    def wait(self):
        while True:
            with self.lock:
                now = time.time()
                while self.calls and now - self.calls[0] > 1800:
                    self.calls.popleft()
                delay = 0.0
                for win, cap in self.WINDOWS:
                    recent = [t for t in self.calls if now - t < win]
                    if len(recent) >= cap:
                        delay = max(delay, win - (now - recent[0]) + 0.05)
                if delay == 0:
                    self.calls.append(now)
                    return
            time.sleep(min(delay, 5))

    def used(self, win=1800.0) -> int:
        now = time.time()
        return sum(1 for t in self.calls if now - t < win)


class UpstoxREST:
    def __init__(self, token: str, limiter: RateLimiter | None = None):
        self.token = token
        self.limiter = limiter or RateLimiter()
        self.s = requests.Session()
        self.s.headers.update({"Accept": "application/json", "Authorization": f"Bearer {token}"})

    def get(self, path: str, params: dict | None = None) -> dict:
        self.limiter.wait()
        r = self.s.get(API + path, params=params, timeout=30)
        if r.status_code == 401:
            raise PermissionError("Upstox login expired. Log in again.")
        if r.status_code == 429:
            time.sleep(2)
            self.limiter.wait()
            r = self.s.get(API + path, params=params, timeout=30)
        r.raise_for_status()
        body = r.json()
        if body.get("status") not in (None, "success"):
            raise RuntimeError(str(body.get("errors"))[:200])
        return body.get("data")

    def profile(self) -> dict:
        return self.get("/v2/user/profile")

    def quotes(self, keys: list[str]) -> dict:
        """Full quotes (v3) for up to 500 keys -> {instrument_key: quote}."""
        out = {}
        for i in range(0, len(keys), 450):
            data = self.get("/v3/market-quote/quotes", {"instrument_key": ",".join(keys[i:i + 450])}) or {}
            for v in data.values():
                k = v.get("instrument_token")
                if k:
                    out[k] = v
        return out

    def option_chain(self, underlying_key: str, expiry: dt.date) -> list:
        return self.get("/v2/option/chain", {"instrument_key": underlying_key,
                                              "expiry_date": expiry.isoformat()}) or []

    def daily_candles(self, key: str, days: int = 40) -> list:
        to = dt.datetime.now(IST).date()
        frm = to - dt.timedelta(days=days)
        path = f"/v3/historical-candle/{urllib.parse.quote(key, safe='')}/days/1/{to.isoformat()}/{frm.isoformat()}"
        data = self.get(path) or {}
        return data.get("candles", [])


# ----------------------------------------------------------------- live feed
def _f(x):
    return float(x) if x is not None else None


def decode(msg_bytes: bytes, pb) -> tuple[str, list]:
    """Protobuf FeedResponse -> (type, [normalized tick dicts])."""
    fr = pb.FeedResponse.FromString(msg_bytes)
    typ = pb.Type.Name(fr.type) if hasattr(pb, "Type") else str(fr.type)
    ticks = []
    for key, feed in fr.feeds.items():
        which = feed.WhichOneof("FeedUnion")
        t = {"key": key}
        if which == "ltpc":
            lt = feed.ltpc
            t.update(ltp=lt.ltp, cp=lt.cp, ltt=lt.ltt, ltq=lt.ltq)
        elif which == "firstLevelWithGreeks":
            g = feed.firstLevelWithGreeks
            t.update(ltp=g.ltpc.ltp, cp=g.ltpc.cp, ltt=g.ltpc.ltt, vol=g.vtt, oi=g.oi, iv=g.iv,
                     bid=g.firstDepth.bidP, ask=g.firstDepth.askP, bidq=g.firstDepth.bidQ, askq=g.firstDepth.askQ,
                     delta=g.optionGreeks.delta, gamma=g.optionGreeks.gamma, theta=g.optionGreeks.theta,
                     vega=g.optionGreeks.vega)
        elif which == "fullFeed":
            ff = feed.fullFeed
            sub = ff.WhichOneof("FullFeedUnion")
            if sub == "marketFF":
                m = ff.marketFF
                t.update(ltp=m.ltpc.ltp, cp=m.ltpc.cp, ltt=m.ltpc.ltt, ltq=m.ltpc.ltq, vol=m.vtt, oi=m.oi, iv=m.iv,
                         atp=m.atp, tbq=m.tbq, tsq=m.tsq)
                if m.marketLevel.bidAskQuote:
                    q = m.marketLevel.bidAskQuote[0]
                    t.update(bid=q.bidP, ask=q.askP, bidq=q.bidQ, askq=q.askQ)
                if m.HasField("optionGreeks"):
                    og = m.optionGreeks
                    t.update(delta=og.delta, gamma=og.gamma, theta=og.theta, vega=og.vega)
                ohlc = m.marketOHLC.ohlc
            else:
                m = ff.indexFF
                t.update(ltp=m.ltpc.ltp, cp=m.ltpc.cp, ltt=m.ltpc.ltt)
                ohlc = m.marketOHLC.ohlc
            for o in ohlc:
                if o.interval == "1d":
                    t.update(day_o=o.open, day_h=o.high, day_l=o.low)
        else:
            continue
        ticks.append(t)
    return typ, ticks


class UpstoxFeed:
    """Websocket client for Upstox Market Data Feed V3 with reconnects and re-subscribes."""

    def __init__(self, token: str, on_ticks, on_status=print):
        from . import upstox_feed_pb2 as pb  # official Upstox schema
        self.pb = pb
        self.token = token
        self.on_ticks = on_ticks
        self.on_status = on_status
        self.subs: dict[str, set] = {"full": set(), "option_greeks": set(), "ltpc": set()}
        self.ws = None
        self.thread = None
        self.stop_flag = threading.Event()
        self.connected = False
        self.msgs = 0
        self.last_msg = 0.0
        self.lock = threading.Lock()

    # public
    def start(self):
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="upstox-feed")
        self.thread.start()

    def stop(self):
        self.stop_flag.set()
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass

    def subscribe(self, keys, mode):
        keys = [k for k in keys if k]
        if not keys:
            return
        with self.lock:
            for m in self.subs:
                self.subs[m].difference_update(keys)
            self.subs[mode].update(keys)
        self._send("sub", keys, mode)

    def unsubscribe(self, keys):
        keys = [k for k in keys if k]
        if not keys:
            return
        with self.lock:
            for m in self.subs:
                self.subs[m].difference_update(keys)
        self._send("unsub", keys)

    def counts(self) -> dict:
        return {m: len(v) for m, v in self.subs.items()}

    # internals
    def _send(self, method, keys, mode=None):
        if not (self.ws and self.connected):
            return
        import websocket
        for i in range(0, len(keys), 500):
            req = {"guid": uuid.uuid4().hex, "method": method, "data": {"instrumentKeys": keys[i:i + 500]}}
            if mode:
                req["data"]["mode"] = mode
            try:
                self.ws.send(json.dumps(req).encode(), opcode=websocket.ABNF.OPCODE_BINARY)
            except Exception as e:
                self.on_status(f"Feed send failed: {e}")

    def _run(self):
        import websocket
        backoff = 1
        while not self.stop_flag.is_set():
            self.ws = websocket.WebSocketApp(
                WS_URL, header={"Authorization": f"Bearer {self.token}", "Accept": "*/*"},
                on_open=self._on_open, on_message=self._on_message,
                on_error=lambda ws, e: self.on_status(f"Feed error: {e}"),
                on_close=lambda ws, c, m: self._on_close(c, m))
            try:
                import certifi
                sslopt = {"ca_certs": certifi.where()}
            except ImportError:
                sslopt = None
            self.ws.run_forever(ping_interval=20, ping_timeout=10, sslopt=sslopt)
            if self.last_msg and time.time() - self.last_msg < 60:
                backoff = 1          # it was streaming fine: reconnect quickly
            self.connected = False
            if self.stop_flag.is_set():
                break
            self.on_status(f"Feed disconnected; reconnecting in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)

    def _on_open(self, ws):
        self.connected = True
        self.on_status("Live feed connected")
        with self.lock:
            snap = {m: list(v) for m, v in self.subs.items()}
        for mode, keys in snap.items():
            self._send("sub", keys, mode)

    def _on_close(self, code, msg):
        self.connected = False
        if code in (401, 403):
            self.on_status("Upstox rejected the token. Log in again.")
            self.stop_flag.set()

    def _on_message(self, ws, message):
        if isinstance(message, str):
            return
        try:
            typ, ticks = decode(message, self.pb)
        except Exception as e:
            self.on_status(f"Bad feed message: {e}")
            return
        self.msgs += 1
        self.last_msg = time.time()
        if ticks:
            self.on_ticks(ticks)
