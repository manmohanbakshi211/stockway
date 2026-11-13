"""One small HTTP client for public sites (not brokers): every URL through safety.check_url, a browser-like user
agent, timeouts, a polite minimum gap per site, cookies (NSE answers its API only after a visit to its home page).

`transport(url, headers, timeout, opener) -> (status, body bytes)` can be replaced in tests (no network in tests)."""
from __future__ import annotations

import http.cookiejar
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from ..core import log, safety

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 "
      "Safari/537.36")
NSE_SITE = "https://www.nseindia.com"
NSE_ARCHIVES = "https://nsearchives.nseindia.com"
MAX_BYTES = 30_000_000
_log = log.get("intel")


class FetchError(RuntimeError):
    pass


def _urllib(url: str, headers: dict, timeout: float, opener) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        return e.code, e.read(10_000)


class Http:
    GAP_S = {"www.nseindia.com": 0.6, "nsearchives.nseindia.com": 0.4}       # polite pauses between calls per site

    def __init__(self, transport=_urllib, timeout: float = 15.0, sleep=time.sleep, clock=time.time):
        self.transport, self.timeout, self.sleep, self.clock = transport, timeout, sleep, clock
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.last: dict[str, float] = {}
        self.lock = threading.Lock()
        self.nse_warm = 0.0
        self.calls = 0

    def _pause(self, host: str) -> None:
        gap = self.GAP_S.get(host, 0.0)
        if not gap:
            return
        with self.lock:
            wait = self.last.get(host, 0.0) + gap - self.clock()
            self.last[host] = max(self.clock(), self.last.get(host, 0.0) + gap)
        if wait > 0:
            self.sleep(wait)

    def get(self, url: str, params: dict | None = None, headers: dict | None = None, timeout: float | None = None) -> bytes:
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        safety.check_url(url)
        host = urllib.parse.urlparse(url).netloc
        self._pause(host)
        h = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9", **(headers or {})}
        self.calls += 1
        status, body = self.transport(url, h, timeout or self.timeout, self.opener)
        if status != 200:
            raise FetchError(f"{host} answered {status}")
        if len(body) > MAX_BYTES:
            raise FetchError(f"{host} sent more than {MAX_BYTES // 1_000_000} MB")
        return body

    def text(self, url: str, **kw) -> str:
        return self.get(url, **kw).decode("utf-8", "replace")

    def json(self, url: str, **kw):
        b = self.get(url, **kw)
        if b.lstrip()[:1] == b"<":
            raise FetchError(f"{urllib.parse.urlparse(url).netloc} sent a web page instead of data")
        return json.loads(b)

    # -------------------------------------------------------------- NSE
    def nse_warmup(self, force: bool = False) -> None:
        if force or self.clock() - self.nse_warm > 600:
            try:
                self.get(NSE_SITE + "/", headers={"Accept": "text/html"})
            except Exception as e:                        # the API call below says what really failed
                _log.info("nse_warmup_failed", error=str(e)[:120])
            self.nse_warm = self.clock()

    def nse(self, path: str):
        """NSE's website API (JSON). Visits the home page first for cookies; one retry with fresh cookies."""
        self.nse_warmup()
        hdr = {"Referer": NSE_SITE + "/", "Accept": "application/json"}
        try:
            return self.json(NSE_SITE + path, headers=hdr)
        except FetchError:
            self.nse_warmup(force=True)
            return self.json(NSE_SITE + path, headers=hdr)
