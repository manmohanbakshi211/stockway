"""Dhan Data API client (historical 1-minute candles and expired options), rate limited.

Needs a Dhan account with the Data API plan, a client ID and an access token (generated on web.dhan.co →
My Profile → Access DhanHQ APIs). Only historical data is read; no orders are placed.
"""
from __future__ import annotations

import datetime as dt
import io
import threading
import time
from pathlib import Path

import requests

BASE = "https://api.dhan.co/v2"
MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

INDEX_IDS = {"NIFTY": 13, "BANKNIFTY": 25, "FINNIFTY": 27, "MIDCPNIFTY": 442, "INDIAVIX": 21}


class DhanError(RuntimeError):
    pass


class Dhan:
    def __init__(self, client_id: str, token: str, per_sec: float = 4.0):
        self.s = requests.Session()
        self.s.headers.update({"access-token": token, "client-id": str(client_id),
                               "Content-Type": "application/json", "Accept": "application/json"})
        self.client_id = str(client_id)
        self.gap = 1.0 / per_sec
        self.last = 0.0
        self.lock = threading.Lock()
        self.calls = 0

    def _post(self, path, payload, tries=4):
        payload = {**payload, "dhanClientId": self.client_id}
        for i in range(tries):
            with self.lock:
                wait = self.last + self.gap - time.time()
                if wait > 0:
                    time.sleep(wait)
                self.last = time.time()
            try:
                r = self.s.post(BASE + path, json=payload, timeout=60)
            except requests.RequestException as e:
                if i == tries - 1:
                    raise DhanError(f"Network error: {e}")
                time.sleep(2 * (i + 1))
                continue
            self.calls += 1
            if r.status_code == 429:
                time.sleep(3 * (i + 1))
                continue
            if r.status_code in (401, 403):
                raise DhanError("Dhan rejected the access token. Generate a new one on web.dhan.co and save it in Live setup.")
            try:
                body = r.json()
            except ValueError:
                body = {}
            if r.status_code >= 400:
                msg = body.get("errorMessage") or body.get("remarks") or r.text[:200]
                code = body.get("errorCode") or r.status_code
                if str(code) in ("DH-905", "DH-907") or "no data" in str(msg).lower():
                    return {}          # no data for this range (holidays, before listing)
                if i == tries - 1:
                    raise DhanError(f"Dhan error {code}: {msg}")
                time.sleep(2 * (i + 1))
                continue
            return body
        return {}

    def check(self):
        """Cheap call to verify the token works (5 days of Nifty 60-minute candles)."""
        to = dt.date.today()
        self.intraday(INDEX_IDS["NIFTY"], "IDX_I", "INDEX", to - dt.timedelta(days=5), to, 60)
        return True

    def intraday(self, security_id, segment, instrument, frm: dt.date, to: dt.date, interval=1, oi=False) -> dict:
        return self._post("/charts/intraday", {
            "securityId": str(security_id), "exchangeSegment": segment, "instrument": instrument,
            "interval": str(interval), "oi": oi,
            "fromDate": f"{frm:%Y-%m-%d} 09:00:00", "toDate": f"{to:%Y-%m-%d} 15:35:00"})

    def rolling_option(self, underlying_id, instrument, expiry_flag, strike_rel: str, side: str,
                       frm: dt.date, to: dt.date, expiry_code=1, interval=1, segment="NSE_FNO") -> dict:
        return self._post("/charts/rollingoption", {
            "exchangeSegment": segment, "interval": str(interval), "securityId": int(underlying_id),
            "instrument": instrument, "expiryFlag": expiry_flag, "expiryCode": expiry_code, "strike": strike_rel,
            "drvOptionType": "CALL" if side == "CE" else "PUT",
            "requiredData": ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"],
            "fromDate": f"{frm:%Y-%m-%d}", "toDate": f"{to:%Y-%m-%d}"})


def series(body: dict) -> dict | None:
    """Find the dict of parallel arrays (with 'timestamp') anywhere in a Dhan response."""
    if not isinstance(body, dict):
        return None
    if "timestamp" in body and isinstance(body["timestamp"], list):
        return body
    for v in body.values():
        if isinstance(v, dict):
            s = series(v)
            if s:
                return s
    return None


def load_master(cache_dir: Path) -> "object":
    import pandas as pd
    f = Path(cache_dir) / "dhan_master.csv"
    if not f.exists() or time.time() - f.stat().st_mtime > 7 * 86400:
        r = requests.get(MASTER_URL, timeout=120)
        r.raise_for_status()
        f.write_bytes(r.content)
    return pd.read_csv(f, low_memory=False)


def lot_sizes(master) -> dict:
    out = {}
    m = master[(master.SEM_EXM_EXCH_ID == "NSE") & (master.SEM_INSTRUMENT_NAME == "OPTIDX")]
    for sym in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"):
        x = m[m.SEM_TRADING_SYMBOL.str.startswith(sym + "-")]
        if len(x):
            out[sym] = int(x.SEM_LOT_UNITS.mode().iloc[0])
    return out
