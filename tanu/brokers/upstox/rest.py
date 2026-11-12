"""Upstox REST for market data only (ADR-0001): candles and quotes. Every URL passes safety.check_url; calls stay
under Upstox's limits (25 a second, 250 a minute, 1000 per 30 minutes) with margin; timeouts and retries with backoff.

The token is passed in by the caller (Tanu's token keeper, at cutover); it is never logged or stored here.
`transport` can be replaced in tests: transport(url, headers, timeout) -> (status, body bytes).
"""
from __future__ import annotations

import collections
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from ...core import log, safety

BASE = "https://api.upstox.com"
_log = log.get("upstox_rest")


class RateLimiter:
    WINDOWS = ((1.0, 20), (60.0, 220), (1800.0, 900))          # Upstox: 25/s, 250/min, 1000/30 min, with margin

    def __init__(self, sleep=time.sleep, clock=time.time):
        self.calls: collections.deque = collections.deque()
        self.lock = threading.Lock()
        self.sleep, self.clock = sleep, clock

    def wait(self) -> None:
        while True:
            with self.lock:
                now = self.clock()
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
            self.sleep(min(delay, 5))


def _urllib(url: str, headers: dict, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class UpstoxRest:
    def __init__(self, token: str, transport=_urllib, limiter: RateLimiter | None = None, retries: int = 3,
                 timeout: float = 15.0, sleep=time.sleep):
        self._token, self.transport, self.limiter = token, transport, limiter or RateLimiter()
        self.retries, self.timeout, self.sleep = retries, timeout, sleep

    def get(self, path: str, params: dict | None = None) -> dict:
        url = BASE + path + (("?" + urllib.parse.urlencode(params)) if params else "")
        safety.check_url(url)
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        for attempt in range(self.retries + 1):
            self.limiter.wait()
            try:
                status, body = self.transport(url, headers, self.timeout)
            except Exception as e:                        # network error: retry
                status, body = None, str(e).encode()
            if status == 200:
                return json.loads(body or b"{}").get("data") or {}
            if status in (401, 403):
                raise PermissionError("Upstox login expired or not allowed. Log in again.")
            if attempt < self.retries and (status is None or status == 429 or status >= 500):
                self.sleep(min(2 ** attempt, 10))
                continue
            _log.warning("rest_failed", path=path.split("?")[0], status=status)
            raise RuntimeError(f"Upstox {path.split('?')[0]} failed: HTTP {status}")
        raise RuntimeError("unreachable")

    def intraday_1m(self, key: str) -> list:
        """Today's 1-minute candles: [[iso_time, o, h, l, c, volume, oi], ...] oldest first."""
        q = urllib.parse.quote(key, safe="")
        rows = self.get(f"/v3/historical-candle/intraday/{q}/minutes/1").get("candles") or []
        return sorted(rows, key=lambda r: r[0])

    def history_1m(self, key: str, frm: str, to: str) -> list:
        q = urllib.parse.quote(key, safe="")
        rows = self.get(f"/v3/historical-candle/{q}/minutes/1/{to}/{frm}").get("candles") or []
        return sorted(rows, key=lambda r: r[0])
