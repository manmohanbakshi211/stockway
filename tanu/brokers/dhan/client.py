"""Dhan historical data API (market data only, ADR-0001 / ADR-0014): rolling expired options (1-minute, one day per
call) and daily candles. One speed limit and one daily count for every Dhan call Tanu makes (Dhan: 5 requests a
second, 100,000 a day on the Data APIs); the count lives in data/tanu/dhan/calls.json and resets at midnight IST.

`transport(url, headers, body, timeout) -> (status, body bytes)` can be replaced in tests (no network in tests)."""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from ...core import log, safety

API = "https://api.dhan.co/v2"
INDEX_IDS = {"NIFTY": 13, "BANKNIFTY": 25, "FINNIFTY": 27, "MIDCPNIFTY": 442, "INDIAVIX": 21}
FIELDS = ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"]
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_log = log.get("dhan")


class DhanError(RuntimeError):
    def __init__(self, msg: str = "", status: int | None = None, net: bool = False):
        super().__init__(msg)
        self.status, self.net = status, net


class NoToken(RuntimeError):
    pass


class Budget:
    """At most `rate` calls a second in total, and a per-day count (IST) saved every 25 calls."""

    def __init__(self, path: Path | str, rate: float = 4.6, now=time.time, sleep=time.sleep):
        self.path, self.rate, self.now, self.sleep = Path(path), rate, now, sleep
        self.lock = threading.Lock()
        self.last = 0.0
        self.day, self.n, self.unsaved = self._today(), 0, 0
        try:
            self.n = int(json.loads(self.path.read_text()).get(self.day, 0))
        except Exception:
            pass

    def _today(self) -> str:
        return dt.datetime.fromtimestamp(self.now(), IST).date().isoformat()

    def used(self) -> int:
        return self.n if self.day == self._today() else 0

    def take(self) -> None:
        with self.lock:
            slot = max(self.last + 1.0 / self.rate, self.now())
            self.last = slot
            today = self._today()
            if today != self.day:
                self.day, self.n = today, 0
            self.n += 1
            self.unsaved += 1
            save = self.unsaved >= 25
        wait = slot - self.now()
        if wait > 0:
            self.sleep(wait)
        if save:
            self.flush()

    def flush(self) -> None:
        with self.lock:
            self.unsaved = 0
            day, n = self.day, self.n
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({day: n}))
            tmp.replace(self.path)
        except Exception:
            pass


def _post(url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class DhanData:
    def __init__(self, keeper, budget: Budget, transport=_post, sleep=time.sleep, tries: int = 5):
        self.keeper, self.budget, self.transport, self.sleep, self.tries = keeper, budget, transport, sleep, tries

    def post(self, path: str, body: dict) -> dict:
        url = safety.check_url(API + path)
        swapped, last = False, None
        i = 0
        while i < self.tries:
            tok = self.keeper.token()
            if not tok:
                raise NoToken("No Dhan token: save the Dhan client ID, PIN and TOTP key on the System page.")
            self.budget.take()
            hdr = {"access-token": tok, "client-id": str(self.keeper.client_id()), "Content-Type": "application/json",
                   "Accept": "application/json"}
            try:
                code, raw = self.transport(url, hdr, json.dumps(body).encode(), 60)
            except Exception as e:
                i += 1
                if i >= self.tries:
                    raise DhanError(f"Network error: {type(e).__name__}", net=True) from None
                self.sleep(2 * i)
                continue
            try:
                j = json.loads(raw or b"{}")
            except ValueError:
                j = {}
            err = str(j.get("errorCode", "")) if isinstance(j, dict) else ""
            if code == 401 or err.startswith(("DH-901", "DH-902")):
                self.keeper.refused(tok)
                if swapped:
                    raise NoToken("Dhan refused a fresh token too: check the Dhan login on the System page.")
                swapped = True
                continue                                   # the next token() makes a new one
            if code in (429, 500, 502, 503, 504):
                last = code
                i += 1
                if i < self.tries:
                    self.sleep(3 * i)
                continue
            if code >= 400 or (isinstance(j, dict) and (j.get("status") == "failure" or j.get("errorType"))):
                msg = (j.get("errorMessage") or j.get("remarks") or "") if isinstance(j, dict) else ""
                raise DhanError(f"Dhan {path} answered {code}: {str(msg)[:150]}", status=code)
            return j if isinstance(j, dict) else {}
        raise DhanError(f"Dhan {path}: too many retries (last answer HTTP {last})", status=last, net=True)

    def rolling(self, sym: str, flag: str, offset: int, side: str, frm: str, to: str, security_id: int | None = None,
                instrument: str = "OPTIDX") -> dict:
        """1-minute data of the rolling strike ATM + offset for one day (to = the next day, exclusive)."""
        body = {"securityId": str(security_id or INDEX_IDS[sym]), "exchangeSegment": "NSE_FNO", "instrument": instrument,
                "expiryFlag": flag, "expiryCode": 1, "strike": "ATM" if offset == 0 else f"ATM{offset:+d}",
                "drvOptionType": "CALL" if side == "CE" else "PUT", "requiredData": FIELDS,
                "fromDate": frm, "toDate": to, "interval": 1}
        res = self.post("/charts/rollingoption", body)
        data = res.get("data", res) or {}
        out = data.get("ce" if side == "CE" else "pe") or data.get("CE" if side == "CE" else "PE") or {}
        return out if isinstance(out, dict) else {}

    def daily(self, sym: str, frm: str, to: str, security_id: int | None = None, segment: str = "IDX_I",
              instrument: str = "INDEX") -> dict:
        body = {"securityId": str(security_id or INDEX_IDS[sym]), "exchangeSegment": segment, "instrument": instrument,
                "expiryCode": 0, "oi": False, "fromDate": frm, "toDate": to}
        res = self.post("/charts/historical", body)
        return res.get("data", res) or {}
