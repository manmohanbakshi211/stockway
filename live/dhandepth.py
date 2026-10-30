"""200-level order book (Dhan Data API, Pro plan) for Nifty and Bank Nifty futures: who is waiting to buy vs sell.

One websocket per instrument (Dhan allows one instrument per 200-depth connection, five sockets in all). Every second
the book gives: total bid vs ask quantity, the imbalance near the price (±0.25%), and the biggest walls on each side.
A row per minute is saved to data/depth/<day>.parquet, because past order books can't be bought anywhere: recording
them is the only way to test them later. Live, the imbalance becomes a fact for the conviction model.

Market data only: nothing is ordered, nothing is read from the trading account.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import struct
import threading
import time
from pathlib import Path

URL = "wss://full-depth-api.dhan.co/twohundreddepth?token={token}&clientId={cid}&authType=2"
NEAR = 0.25            # % around the mid price for the "near" imbalance


def parse(buf: bytes) -> list:
    """Dhan 200-depth binary messages -> [(security_id, 'bid'|'ask', [(price, qty, orders), ...])] (little-endian)."""
    out, i = [], 0
    while i + 12 <= len(buf):
        length, code, _seg, sid, rows = struct.unpack_from("<hBBiI", buf, i)
        if code not in (41, 51):
            if length <= 0:
                break
            i += length
            continue
        lv = []
        p = i + 12
        for _ in range(min(rows, 200)):
            if p + 16 > len(buf):
                break
            price, qty, orders = struct.unpack_from("<dII", buf, p)
            if price > 0 and qty > 0:
                lv.append((price, qty, orders))
            p += 16
        out.append((sid, "bid" if code == 41 else "ask", lv))
        i += length if length > 12 else 12 + 16 * rows
    return out


def stats(bids: list, asks: list) -> dict | None:
    if not bids or not asks:
        return None
    bb, ba = max(b[0] for b in bids), min(a[0] for a in asks)
    mid = (bb + ba) / 2
    bq, aq = sum(b[1] for b in bids), sum(a[1] for a in asks)
    lo, hi = mid * (1 - NEAR / 100), mid * (1 + NEAR / 100)
    nb, na = sum(b[1] for b in bids if b[0] >= lo), sum(a[1] for a in asks if a[0] <= hi)
    wb, wa = max(bids, key=lambda x: x[1]), max(asks, key=lambda x: x[1])
    return {"mid": round(mid, 2), "spread": round(ba - bb, 2), "bid_qty": bq, "ask_qty": aq,
            "imb": round((bq - aq) / (bq + aq), 4) if bq + aq else 0.0,
            "imb_near": round((nb - na) / (nb + na), 4) if nb + na else 0.0,
            "bid_orders": sum(b[2] for b in bids), "ask_orders": sum(a[2] for a in asks),
            "wall_bid": wb[0], "wall_bid_qty": wb[1], "wall_bid_pct": round((wb[0] / mid - 1) * 100, 3),
            "wall_ask": wa[0], "wall_ask_qty": wa[1], "wall_ask_pct": round((wa[0] / mid - 1) * 100, 3)}


def futures_ids(cache_dir, syms=("NIFTY", "BANKNIFTY"), today: dt.date | None = None) -> dict:
    """sym -> Dhan security id of its nearest index future, from Dhan's public scrip master."""
    from lab.dhan_hist import SCRIP_MASTER
    today = today or dt.date.today()
    f = Path(cache_dir) / f"dhan_scrips_{today:%Y%m%d}.csv"
    if not f.exists():
        import requests
        r = requests.get(SCRIP_MASTER, timeout=120)
        r.raise_for_status()
        f.write_bytes(r.content)
    best = {}
    with open(f, newline="", encoding="utf-8", errors="ignore") as fh:
        for row in csv.DictReader(fh):
            if row.get("SEM_EXM_EXCH_ID") != "NSE" or row.get("SEM_INSTRUMENT_NAME") != "FUTIDX":
                continue
            und = (row.get("SEM_TRADING_SYMBOL") or "").split("-")[0]
            if und not in syms:
                continue
            try:
                exp = dt.date.fromisoformat((row.get("SEM_EXPIRY_DATE") or "")[:10])
            except ValueError:
                continue
            if exp >= today and (und not in best or exp < best[und][0]):
                best[und] = (exp, int(float(row["SEM_SMST_SECURITY_ID"])))
    return {k: v[1] for k, v in best.items()}


class DepthFeed:
    def __init__(self, client_id: str, token: str, ids: dict, data_dir, log=print):
        self.cid, self.token, self.ids, self.log = str(client_id), token, ids, log
        self.dir = Path(data_dir) / "depth"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.books = {s: {"bid": [], "ask": [], "ts": 0.0} for s in ids}
        self.by_id = {v: k for k, v in ids.items()}
        self.rows, self.last_min = [], {}
        self.stop_ev = threading.Event()
        self.status = {s: "starting" for s in ids}
        self.threads = []

    def start(self):
        for sym, sid in self.ids.items():
            t = threading.Thread(target=self._run, args=(sym, sid), daemon=True)
            t.start()
            self.threads.append(t)

    def stop(self):
        self.stop_ev.set()
        self.flush()

    def _run(self, sym, sid):
        import websocket
        backoff = 2
        while not self.stop_ev.is_set():
            def on_open(ws):
                ws.send(json.dumps({"RequestCode": 23, "ExchangeSegment": "NSE_FNO", "SecurityId": str(sid)}))
                self.status[sym] = "live"

            def on_msg(ws, msg):
                if isinstance(msg, (bytes, bytearray)):
                    for _sid, side, lv in parse(bytes(msg)):
                        b = self.books.get(self.by_id.get(_sid, sym))
                        if b is not None:
                            b[side], b["ts"] = lv, time.time()

            def on_err(ws, e):
                self.status[sym] = f"error: {str(e)[:80]}"
            ws = websocket.WebSocketApp(URL.format(token=self.token, cid=self.cid), on_open=on_open, on_message=on_msg, on_error=on_err)
            ws.run_forever(ping_interval=20, ping_timeout=10)
            if self.stop_ev.is_set():
                break
            self.status[sym] = "reconnecting"
            time.sleep(backoff)
            backoff = min(60, backoff * 2)

    def view(self, sym) -> dict | None:
        b = self.books.get(sym)
        if not b or time.time() - b["ts"] > 30:
            return None
        return stats(b["bid"], b["ask"])

    def tick(self, now: dt.datetime):
        """Call every loop: records one row per minute per instrument; flushes every 15 minutes."""
        m = now.replace(second=0, microsecond=0)
        for sym in self.ids:
            if self.last_min.get(sym) == m:
                continue
            v = self.view(sym)
            if v:
                self.rows.append({"ts": m.timestamp(), "sym": sym, **v})
                self.last_min[sym] = m
        if self.rows and m.minute % 15 == 0 and len(self.rows) >= len(self.ids) * 10:
            self.flush()

    def flush(self):
        if not self.rows:
            return
        import pandas as pd
        rows, self.rows = self.rows, []
        day = dt.datetime.fromtimestamp(rows[0]["ts"]).date()
        f = self.dir / f"{day.isoformat()}.parquet"
        df = pd.DataFrame(rows)
        if f.exists():
            df = pd.concat([pd.read_parquet(f), df], ignore_index=True).drop_duplicates(["ts", "sym"])
        df.to_parquet(f, index=False)
