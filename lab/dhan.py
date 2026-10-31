"""Dhan Data API client (historical expired options, daily index candles).

Needs a Dhan account with the Data API plan (Rs 499 / 30 days) and an access token from
web.dhan.co -> Profile -> Access DhanHQ APIs.
"""
from __future__ import annotations

import collections
import threading
import time

import requests

API = "https://api.dhan.co/v2"

# Dhan security IDs (verified against Dhan's scrip master)
INDEX_IDS = {"NIFTY": 13, "BANKNIFTY": 25, "FINNIFTY": 27, "MIDCPNIFTY": 442, "INDIAVIX": 21}
FIELDS = ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"]


class DhanError(RuntimeError):
    pass


class Dhan:
    def __init__(self, client_id: str, token: str, per_sec: float = 4.0):
        self.s = requests.Session()
        self.s.headers.update({"access-token": token, "client-id": str(client_id), "Content-Type": "application/json",
                               "Accept": "application/json"})
        self.client_id = str(client_id)
        self.token = token
        self.gap = 1.0 / per_sec
        self.last = 0.0
        self.lock = threading.Lock()
        self.calls = collections.deque(maxlen=1000)

    def _wait(self):
        with self.lock:
            d = self.gap - (time.time() - self.last)
            if d > 0:
                time.sleep(d)
            self.last = time.time()

    def _fresh_token(self) -> bool:
        """After a renew (or a new paste) the app holds a newer token: switch to it. True if switched."""
        from .dhan_token import latest
        new = latest()
        if new and new != self.token:
            self.token = new
            self.s.headers["access-token"] = new
            return True
        return False

    def post(self, path: str, body: dict, tries: int = 5) -> dict:
        swapped = False
        for i in range(tries):
            self._wait()
            try:
                r = self.s.post(API + path, json=body, timeout=60)
            except requests.RequestException as e:
                if i == tries - 1:
                    raise DhanError(f"Network error: {e}")
                time.sleep(2 * (i + 1))
                continue
            self.calls.append(time.time())
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(3 * (i + 1))
                continue
            try:
                body_json = r.json()
            except ValueError:
                body_json = {}
            if r.status_code == 401 or str(body_json.get("errorCode", "")).startswith(("DH-901", "DH-902")):
                if not swapped and self._fresh_token():
                    swapped = True
                    continue
                raise PermissionError("Dhan rejected the access token. Create a new token on web.dhan.co and paste it in Strategy lab settings.")
            if r.status_code >= 400 or body_json.get("status") == "failure" or body_json.get("errorType"):
                msg = body_json.get("errorMessage") or body_json.get("remarks") or r.text[:200]
                raise DhanError(f"Dhan {path}: {msg}")
            return body_json
        raise DhanError(f"Dhan {path}: too many retries")

    def profile(self) -> dict:
        self._wait()
        r = self.s.get(API + "/profile", timeout=30, headers={"dhanClientId": self.client_id})
        if r.status_code == 401 and self._fresh_token():
            r = self.s.get(API + "/profile", timeout=30, headers={"dhanClientId": self.client_id})
        if r.status_code == 401:
            raise PermissionError("Dhan rejected the access token.")
        r.raise_for_status()
        return r.json()

    def rolling(self, sym: str, flag: str, offset: int, side: str, frm: str, to: str, code: int = 1,
                interval: int = 1, security_id: int | None = None, instrument: str = "OPTIDX") -> dict:
        """1-minute data for a rolling strike (ATM + offset) between frm and to (to exclusive; Dhan serves ONE day per call).
        Index options: ATM ± 10; stock options (instrument OPTSTK, security_id of the stock): ATM ± 3.
        Returns arrays: timestamp, open, high, low, close, iv, volume, strike, oi, spot."""
        strike = "ATM" if offset == 0 else f"ATM{offset:+d}"
        body = {"securityId": str(security_id or INDEX_IDS[sym]), "exchangeSegment": "NSE_FNO", "instrument": instrument,
                "expiryFlag": flag, "expiryCode": code, "strike": strike,
                "drvOptionType": "CALL" if side == "CE" else "PUT", "requiredData": FIELDS,
                "fromDate": frm, "toDate": to, "interval": interval}
        res = self.post("/charts/rollingoption", body)
        data = res.get("data", res) or {}
        key = "ce" if side == "CE" else "pe"
        out = data.get(key) or data.get(key.upper()) or {}
        return out if isinstance(out, dict) else {}

    def daily(self, sym: str, frm: str, to: str) -> dict:
        body = {"securityId": str(INDEX_IDS[sym]), "exchangeSegment": "IDX_I", "instrument": "INDEX",
                "expiryCode": 0, "oi": False, "fromDate": frm, "toDate": to}
        res = self.post("/charts/historical", body)
        return res.get("data", res) or {}
