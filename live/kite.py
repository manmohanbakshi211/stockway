"""Zerodha Kite Connect: login, the instrument list and the live price websocket (gun, 2.52). MARKET DATA ONLY.

Mandeep, 10 Nov 2026: Kite next to Upstox and Dhan, each at its best. Kite is a backup and a cross-check for the
Upstox feed (live/feeds.py). This module never calls anything that places, changes or cancels an order
(tests/test_safety.py fails if it ever does). Keys and the daily login stay on the Mac (data/kite_token.json).

Kite Connect (₹500 / month) includes the live websocket (up to 3,000 instruments per connection) and historical data.
The access token ends at 6:00 AM the next day (Zerodha's rule): log in once a day, like Upstox.
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import struct
import threading
import time
import urllib.parse
from pathlib import Path

from .clock import IST

API = "https://api.kite.trade"
LOGIN = "https://kite.zerodha.com/connect/login"
WS_URL = "wss://ws.kite.trade"
HEADERS = {"X-Kite-Version": "3"}


# ----------------------------------------------------------------- login
def login_url(api_key: str) -> str:
    return f"{LOGIN}?{urllib.parse.urlencode({'v': 3, 'api_key': api_key})}"


def checksum(api_key: str, request_token: str, api_secret: str) -> str:
    """Kite's session checksum: SHA-256 of api_key + request_token + api_secret."""
    return hashlib.sha256(f"{api_key}{request_token}{api_secret}".encode()).hexdigest()


def exchange_token(request_token: str, api_key: str, api_secret: str) -> dict:
    """The request token from the login redirect -> the day's access token (market data only is used)."""
    import requests
    r = requests.post(f"{API}/session/token", headers=HEADERS, timeout=30,
                      data={"api_key": api_key, "request_token": request_token,
                            "checksum": checksum(api_key, request_token, api_secret)})
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    data = body.get("data") or {}
    if r.status_code != 200 or not data.get("access_token"):
        raise RuntimeError(f"Kite login failed: {body.get('message') or r.text[:200]}")
    return data


def token_expiry(now: dt.datetime | None = None) -> dt.datetime:
    """Kite access tokens end at 6:00 AM IST the next morning."""
    now = now or dt.datetime.now(IST)
    exp = now.replace(hour=6, minute=0, second=0, microsecond=0)
    return exp if now < exp else exp + dt.timedelta(days=1)


class KiteTokenStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def save(self, data: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"access_token": data["access_token"], "user": data.get("user_name") or data.get("user_id"),
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
            if dt.datetime.fromisoformat(rec["expires_at"]) <= dt.datetime.now(IST):
                return None
            return rec
        except Exception:
            return None

    def clear(self):
        self.path.unlink(missing_ok=True)


# ----------------------------------------------------------------- instruments and the Upstox -> Kite map
def kite_index(api_key: str, access_token: str, cache_dir: Path) -> tuple[dict, dict]:
    """Kite's instrument list (CSV, downloaded once a day and kept on the Mac), read into two small look-ups:
    (exchange, exchange_token) -> instrument_token and (exchange, tradingsymbol) -> instrument_token."""
    d = Path(cache_dir) / "instruments"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"KITE_{dt.datetime.now(IST).date().isoformat()}.csv.gz"
    if not f.exists():
        import requests
        r = requests.get(f"{API}/instruments", timeout=120,
                         headers={**HEADERS, "Authorization": f"token {api_key}:{access_token}"})
        r.raise_for_status()
        f.write_bytes(gzip.compress(r.content))
        for old in d.glob("KITE_*.csv.gz"):
            if old != f:
                old.unlink(missing_ok=True)
    return index_csv(gzip.decompress(f.read_bytes()).decode())


def index_csv(text: str) -> tuple[dict, dict]:
    by_tok, by_sym = {}, {}
    rd = csv.reader(io.StringIO(text))
    head = next(rd, [])
    try:
        it_i, et_i, ts_i, ex_i = (head.index(c) for c in ("instrument_token", "exchange_token", "tradingsymbol", "exchange"))
    except ValueError:
        return by_tok, by_sym
    for row in rd:
        try:
            ex = row[ex_i]
            if ex not in ("NSE", "NFO", "BSE", "BFO"):
                continue
            it = int(row[it_i])
        except (IndexError, ValueError):
            continue
        by_tok[(ex, str(row[et_i]))] = it
        by_sym[(ex, row[ts_i])] = it
    return by_tok, by_sym


def build_map(keys, ex_tok: dict, kite_idx: tuple[dict, dict], index_map: dict | None = None) -> dict:
    """Upstox instrument key -> Kite instrument token, for the keys asked. Futures / options and stocks match on the
    NSE exchange token (the same number in both lists; ex_tok: Upstox key -> exchange token, from universe.load);
    indices by name (rules/feeds.json index_map, else the Upstox name in capitals)."""
    by_tok, by_sym = kite_idx
    out = {}
    for k in keys:
        if not k or "|" not in k:
            continue
        seg, rest = k.split("|", 1)
        it = None
        if seg in ("NSE_INDEX", "BSE_INDEX"):
            target = (index_map or {}).get(k) or f"{'NSE' if seg == 'NSE_INDEX' else 'BSE'}:{rest.upper()}"
            ex, sym = target.split(":", 1)
            it = by_sym.get((ex, sym))
        else:
            et = str(ex_tok.get(k) or (rest if rest.isdigit() else ""))
            ex = {"NSE_FO": "NFO", "NSE_EQ": "NSE", "BSE_FO": "BFO", "BSE_EQ": "BSE"}.get(seg)
            if ex and et:
                it = by_tok.get((ex, et))
        if it is not None:
            out[k] = it
    return out


# ----------------------------------------------------------------- the websocket's binary ticks
def _i(b, a, z):
    return struct.unpack(">i", b[a:z])[0]


def parse_binary(data: bytes) -> list:
    """Kite's binary market-data message -> [{token, ltp, ...}]. Prices come in paise (÷100; currency ÷10^7)."""
    out = []
    if len(data) < 2:
        return out
    n = struct.unpack(">H", data[0:2])[0]
    j = 2
    for _ in range(n):
        if j + 2 > len(data):
            break
        ln = struct.unpack(">H", data[j:j + 2])[0]
        p = data[j + 2:j + 2 + ln]
        j += 2 + ln
        if len(p) < 8:
            continue
        tok = _i(p, 0, 4)
        seg = tok & 0xFF
        div = 10000000.0 if seg == 3 else 10000.0 if seg == 6 else 100.0
        t = {"token": tok, "ltp": _i(p, 4, 8) / div}
        if seg == 9:                                         # indices: 28 bytes (quote) or 32 (full)
            if len(p) >= 28:
                t.update(day_h=_i(p, 8, 12) / div, day_l=_i(p, 12, 16) / div, day_o=_i(p, 16, 20) / div,
                         cp=_i(p, 20, 24) / div)
            if len(p) >= 32:
                t["ts"] = _i(p, 28, 32)
        elif len(p) >= 44:                                   # quote (44) / full (184)
            t.update(ltq=_i(p, 8, 12), atp=_i(p, 12, 16) / div, vol=_i(p, 16, 20), tbq=_i(p, 20, 24), tsq=_i(p, 24, 28),
                     day_o=_i(p, 28, 32) / div, day_h=_i(p, 32, 36) / div, day_l=_i(p, 36, 40) / div, cp=_i(p, 40, 44) / div)
            if len(p) >= 184:
                t.update(ltt=_i(p, 44, 48), oi=_i(p, 48, 52), ts=_i(p, 60, 64))
                buy = [(_i(p, k, k + 4), _i(p, k + 4, k + 8) / div) for k in range(64, 124, 12)]
                sell = [(_i(p, k, k + 4), _i(p, k + 4, k + 8) / div) for k in range(124, 184, 12)]
                if buy and buy[0][1]:
                    t.update(bid=buy[0][1], bidq=buy[0][0])
                if sell and sell[0][1]:
                    t.update(ask=sell[0][1], askq=sell[0][0])
        out.append(t)
    return out


def to_tick(raw: dict, key: str) -> dict:
    """A Kite tick in the app's own tick format (the same fields the Upstox feed gives), for the Upstox key."""
    t = {"key": key, "ltp": raw.get("ltp")}
    for k in ("cp", "ltq", "vol", "atp", "tbq", "tsq", "oi", "bid", "ask", "bidq", "askq", "day_o", "day_h", "day_l"):
        if raw.get(k) is not None:
            t[k] = raw[k]
    lt = raw.get("ltt") or raw.get("ts")                  # seconds -> ms like Upstox
    if lt:
        t["ltt"] = int(lt) * 1000
    return t


# ----------------------------------------------------------------- the websocket
class KiteFeed:
    """Kite's live price websocket with reconnects and re-subscribes. Market data only."""

    def __init__(self, api_key: str, access_token: str, on_ticks, on_status=print, mode: str = "full"):
        self.api_key, self.token = api_key, access_token
        self.on_ticks, self.on_status, self.mode = on_ticks, on_status, mode
        self.tokens: set = set()
        self.ws = None
        self.thread = None
        self.stop_flag = threading.Event()
        self.connected = False
        self.msgs = 0
        self.last_msg = 0.0
        self.error = None
        self.lock = threading.Lock()

    def start(self):
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="kite-feed")
        self.thread.start()

    def stop(self):
        self.stop_flag.set()
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass

    def subscribe(self, tokens):
        tokens = [int(t) for t in tokens if t]
        if not tokens:
            return
        with self.lock:
            self.tokens.update(tokens)
        self._send(tokens, "subscribe")

    def unsubscribe(self, tokens):
        tokens = [int(t) for t in tokens if t]
        if not tokens:
            return
        with self.lock:
            self.tokens.difference_update(tokens)
        self._send(tokens, "unsubscribe")

    def _send(self, tokens, action):
        if not (self.ws and self.connected):
            return
        for i in range(0, len(tokens), 500):
            part = tokens[i:i + 500]
            try:
                self.ws.send(json.dumps({"a": action, "v": part}))
                if action == "subscribe":
                    self.ws.send(json.dumps({"a": "mode", "v": [self.mode, part]}))
            except Exception as e:
                self.on_status(f"Kite feed send failed: {e}")

    def _run(self):
        import websocket
        backoff = 1
        while not self.stop_flag.is_set():
            url = f"{WS_URL}?{urllib.parse.urlencode({'api_key': self.api_key, 'access_token': self.token})}"
            self.ws = websocket.WebSocketApp(url, on_open=self._on_open, on_message=self._on_message,
                                             on_error=lambda ws, e: self._err(e), on_close=lambda ws, c, m: self._on_close(c, m))
            try:
                import certifi
                sslopt = {"ca_certs": certifi.where()}
            except ImportError:
                sslopt = None
            self.ws.run_forever(ping_interval=20, ping_timeout=10, sslopt=sslopt)
            self.connected = False
            if self.stop_flag.is_set():
                break
            if self.last_msg and time.time() - self.last_msg < 60:
                backoff = 1
            self.on_status(f"Kite feed disconnected; reconnecting in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)

    def _err(self, e):
        self.error = str(e)[:200]
        self.on_status(f"Kite feed error: {e}")

    def _on_open(self, ws):
        self.connected = True
        self.error = None
        self.on_status("Kite feed connected (backup + cross-check)")
        with self.lock:
            toks = sorted(self.tokens)
        self._send(toks, "subscribe")

    def _on_close(self, code, msg):
        self.connected = False
        if code in (401, 403) or "403" in str(msg or ""):
            self.error = "Kite rejected the login: log in with Kite again"
            self.on_status(self.error)
            self.stop_flag.set()

    def _on_message(self, ws, message):
        if isinstance(message, str):                      # text: postbacks / errors (no orders are ever used)
            try:
                m = json.loads(message)
                if m.get("type") == "error":
                    self.error = str(m.get("data"))[:200]
            except Exception:
                pass
            return
        if len(message) <= 1:                             # heartbeat
            self.last_msg = time.time()
            return
        try:
            ticks = parse_binary(message)
        except Exception as e:
            self.on_status(f"Bad Kite message: {e}")
            return
        self.msgs += 1
        self.last_msg = time.time()
        if ticks:
            self.on_ticks(ticks)
