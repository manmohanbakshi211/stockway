"""GIFT Nifty: the near-month Nifty future on NSE International Exchange (GIFT City), from NSE IX's public site.

Trades almost round the clock (two sessions, about 06:30-02:45 IST), so before 9:15 it is the best read of where Nifty
will open. Upstox does not stream it, so the app reads the same public endpoint the nseix.com page uses, at most
every 30 seconds. Read-only market data: nothing is sent to NSE IX except the page request.
"""
from __future__ import annotations

import datetime as dt
import threading
import time

URL = "https://www.nseix.com/api/derivatives-watch?inst_type1=IDX&inst_type2=STK&type=live"
HEAD = {"User-Agent": "Mozilla/5.0 (Macintosh) Stockway", "Referer": "https://www.nseix.com/", "Accept": "application/json"}


def parse(d: dict) -> dict | None:
    """Nearest-expiry NIFTY index future from the derivatives-watch JSON."""
    rows = [r for r in (d.get("data") or []) if str(r.get("SYMBOL")).upper() == "NIFTY"
            and "FUT" in str(r.get("INSTRUMENT", "")).upper() and r.get("LASTPRICE")]
    if not rows:
        return None

    def exp(r):
        try:
            return dt.datetime.strptime(r["EXPIRYDATE"], "%d-%b-%Y").date()
        except Exception:
            return dt.date.max
    r = min(rows, key=exp)
    last = float(r["LASTPRICE"])
    chg = r.get("CHANGE")
    pct = r.get("PERCHANGE")
    try:
        chg = float(chg) if chg not in (None, "", "-") else None
    except ValueError:
        chg = None
    try:
        pct = float(pct) if pct not in (None, "", "-") else None
    except ValueError:
        pct = None
    if pct is None and chg is not None and last - chg:
        pct = chg / (last - chg) * 100
    return {"name": "GIFT Nifty", "spot": last, "chg": chg, "chg_pct": pct, "expiry": r.get("EXPIRYDATE"),
            "src": "NSE IX, near-month future", "ts": time.time()}


class GiftNifty:
    def __init__(self, log=print):
        self.log = log
        self.row = None
        self.err = None
        self._at = 0.0
        self._lock = threading.Lock()

    def get(self, max_age=30) -> dict | None:
        """Cached row; refreshed in the background when older than max_age seconds."""
        if time.time() - self._at > max_age and self._lock.acquire(blocking=False):
            self._at = time.time()
            threading.Thread(target=self._fetch, daemon=True).start()
        return self.row

    def _fetch(self):
        try:
            import requests
            r = requests.get(URL, headers=HEAD, timeout=10)
            r.raise_for_status()
            row = parse(r.json())
            if row:
                self.row, self.err = row, None
        except Exception as e:
            if self.err is None:
                self.log(f"GIFT Nifty not available right now: {e}")
            self.err = str(e)
        finally:
            self._lock.release()
