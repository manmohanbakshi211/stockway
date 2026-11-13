"""A local fake of the Upstox V3 feed that plays one past market day, for the rehearsal (no network, no token, no
broker). It serves the day's index minutes (Tanu's history) and the day's option minutes (the legacy option files,
read in place) as protobuf ticks: four per minute per instrument (open, high, low, close, in that order of time), only
for the keys the client subscribed, like Upstox. `rows()` is a matching instrument list for that day."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import threading
from pathlib import Path

import pandas as pd

from ..brokers.upstox import feed_pb2 as pb
from ..data.facts import Facts
from ..engine.options import OptionHistory

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
INDEX_KEYS = {"NIFTY": "NSE_INDEX|Nifty 50", "BANKNIFTY": "NSE_INDEX|Nifty Bank", "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
              "MIDCPNIFTY": "NSE_INDEX|NIFTY MID SELECT", "SENSEX": "BSE_INDEX|SENSEX"}
OFFSETS = (1, 20, 40, 58)                       # seconds into the minute of the o, h, l, c ticks
HALF_SPREAD = {"NIFTY": 0.27 / 200, "BANKNIFTY": 0.33 / 200}


def _key(sym: str, strike: float, side: str) -> str:
    return f"NSE_FO|{sym}|{strike:g}|{side}"


class FakeBroker:
    def __init__(self, day: str, base: Path | str, legacy_history: Path | str, syms=("NIFTY", "BANKNIFTY"),
                 minute_s: float = 0.02, facts: Facts | None = None):
        from .context import minutes
        self.day, self.minute_s, self.facts = day, minute_s, facts or Facts()
        self.oh = OptionHistory(legacy_history)
        self.ticks: dict[int, list[tuple[str, float, float, float]]] = {}   # second -> [(key, price, cum_vol, oi)]
        self._rows: list[dict] = []
        a = dt.datetime.fromisoformat(day + "T00:00:00+05:30").timestamp()
        self.syms = []
        for s in syms:
            mins = minutes(base, s, INDEX_KEYS.get(s), before=None)
            d = mins[(mins["t"] >= a) & (mins["t"] < a + 86400)]
            f = self.oh.file_for(s, day)
            if d.empty or f is None:
                continue
            self.syms.append(s)
            k = INDEX_KEYS[s]
            for r in d.itertuples(index=False):
                for off, px in zip(OFFSETS, (r.o, r.h, r.l, r.c)):
                    self.ticks.setdefault(int(r.t) + off, []).append((k, float(px), None, None))
            o = pd.read_parquet(f)
            o = o[(o["ts"] >= a) & (o["ts"] < a + 86400)].sort_values("ts")
            exp = f.stem[4:]
            exp_ms = int(dt.datetime.fromisoformat(exp + "T15:30:00+05:30").timestamp() * 1000)
            for (strike, side), g in o.groupby(["strike", "side"]):
                ok = _key(s, float(strike), side)
                self._rows.append({"segment": "NSE_FO", "instrument_type": side, "instrument_key": ok, "underlying_symbol": s,
                                   "name": s, "underlying_key": k, "underlying_type": "INDEX", "expiry": exp_ms,
                                   "strike_price": float(strike), "lot_size": self.facts.lot(s, day) or 1, "tick_size": 5.0})
                cum = g["volume"].fillna(0).cumsum() if "volume" in g else pd.Series(0, index=g.index)
                for r, cv in zip(g.itertuples(index=False), cum):
                    oi = float(r.oi) if "oi" in g and pd.notna(r.oi) else None
                    for off, px in zip(OFFSETS, (r.open, r.high, r.low, r.close)):
                        self.ticks.setdefault(int(r.ts) + off, []).append((ok, float(px), float(cv), oi))
        self.subs: set = set()
        self.sent = 0
        self.port = None
        self.started = threading.Event()
        self.finished = threading.Event()
        self.ready = threading.Event()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run, daemon=True, name="fake-broker").start()
        if not self.ready.wait(10):
            raise RuntimeError("fake broker did not start")

    def rows(self) -> list[dict]:
        return list(self._rows)

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/v3/feed/market-data-feed"

    def _message(self, sec: int, items) -> bytes:
        fr = pb.FeedResponse()
        fr.type = 1
        fr.currentTs = sec * 1000
        for key, px, cv, oi in items:
            if key not in self.subs:
                continue
            if "INDEX" in key:
                f = fr.feeds[key].fullFeed.indexFF
                f.ltpc.ltp, f.ltpc.ltt = px, sec * 1000
            else:
                m = fr.feeds[key].fullFeed.marketFF
                m.ltpc.ltp, m.ltpc.ltt, m.ltpc.ltq = px, sec * 1000, 1
                if cv is not None:
                    m.vtt = int(cv)
                if oi is not None:
                    m.oi = oi
                hs = HALF_SPREAD.get(key.split("|")[1], 0.002)
                q = m.marketLevel.bidAskQuote.add()
                q.bidP, q.askP = round(px * (1 - hs), 2), round(px * (1 + hs), 2)
        return fr.SerializeToString() if fr.feeds else b""

    def _run(self):
        import websockets
        asyncio.set_event_loop(self.loop)

        async def stream(ws):
            await asyncio.sleep(0.3)                     # let the first subscriptions land
            secs = sorted(self.ticks)
            last_min = None
            for sec in secs:
                minute = sec // 60
                if last_min is not None and minute != last_min and self.minute_s:
                    await asyncio.sleep(self.minute_s)   # market minute -> a moment of wall time
                last_min = minute
                b = self._message(sec, self.ticks[sec])
                if b:
                    await ws.send(b)
                    self.sent += 1
                    if self.sent == 1:
                        await asyncio.sleep(0.3)         # the client subscribes options once it sees a price
            if secs:                                     # a closing heartbeat: the exchange clock passes 15:30
                end = (secs[-1] // 60 + 2) * 60
                last = {}
                for sec in secs:
                    for key, px, _cv, _oi in self.ticks[sec]:
                        if "INDEX" in key:
                            last[key] = px
                hb = pb.FeedResponse()
                hb.type, hb.currentTs = 1, end * 1000
                for k, px in last.items():
                    if k in self.subs:
                        hb.feeds[k].fullFeed.indexFF.ltpc.ltp = px
                        hb.feeds[k].fullFeed.indexFF.ltpc.ltt = end * 1000
                if hb.feeds:
                    await ws.send(hb.SerializeToString())
                    self.sent += 1
            self.finished.set()

        async def handler(ws):
            task = None
            try:
                async for m in ws:
                    d = json.loads(m if isinstance(m, str) else m.decode())
                    keys = d["data"]["instrumentKeys"]
                    if d["method"] == "sub":
                        self.subs.update(keys)
                    elif d["method"] == "unsub":
                        self.subs.difference_update(keys)
                    if task is None and not self.started.is_set():
                        self.started.set()
                        task = asyncio.ensure_future(stream(ws))
                if task:
                    await task
            except Exception:
                pass

        async def main():
            async with websockets.serve(handler, "127.0.0.1", 0, max_size=None) as srv:
                self.port = srv.sockets[0].getsockname()[1]
                self.ready.set()
                await asyncio.Future()
        self.loop.run_until_complete(main())
