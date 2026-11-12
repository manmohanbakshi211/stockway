"""A local websocket server that speaks the Upstox V3 feed (binary JSON subscriptions in, protobuf out), for tests.
No network, no token, no broker."""
import asyncio
import json
import threading
import time

from tanu.brokers.upstox import feed_pb2 as pb


def message(prices: dict, ts_ms: int | None = None) -> bytes:
    fr = pb.FeedResponse()
    fr.type = 1
    fr.currentTs = ts_ms or int(time.time() * 1000)
    for k, p in prices.items():
        if k.startswith("NSE_INDEX"):
            f = fr.feeds[k].fullFeed.indexFF
            f.ltpc.ltp, f.ltpc.ltt, f.ltpc.cp = p, fr.currentTs, p - 10
        else:
            m = fr.feeds[k].fullFeed.marketFF
            m.ltpc.ltp, m.ltpc.ltt, m.ltpc.ltq, m.vtt, m.oi = p, fr.currentTs, 75, 1000 + int(p), 5000
            q = m.marketLevel.bidAskQuote.add()
            q.bidP, q.askP, q.bidQ, q.askQ = p - 0.05, p + 0.05, 150, 300
    return fr.SerializeToString()


class FakeUpstox:
    def __init__(self):
        self.subs, self.sent, self.conns, self.port = [], [], 0, None
        self._live = set()
        self.ready = threading.Event()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run, daemon=True).start()
        assert self.ready.wait(10)

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/v3/feed/market-data-feed"

    def _run(self):
        import websockets
        asyncio.set_event_loop(self.loop)

        async def handler(ws):
            self.conns += 1
            self._live.add(ws)
            try:
                async for m in ws:
                    d = json.loads(m if isinstance(m, str) else m.decode())
                    self.subs.append((self.conns, d["method"], sorted(d["data"]["instrumentKeys"]), d["data"].get("mode")))
                    if d["method"] == "sub":
                        b = message({k: 100.0 + i for i, k in enumerate(d["data"]["instrumentKeys"])})
                        self.sent.append(b)
                        await ws.send(b)
            except Exception:
                pass
            finally:
                self._live.discard(ws)

        async def main():
            async with websockets.serve(handler, "127.0.0.1", 0) as srv:
                self.port = srv.sockets[0].getsockname()[1]
                self.ready.set()
                await asyncio.Future()
        self.loop.run_until_complete(main())

    def push(self, prices: dict) -> bytes:
        b = message(prices)
        self.sent.append(b)
        for ws in list(self._live):
            asyncio.run_coroutine_threadsafe(ws.send(b), self.loop).result(5)
        return b

    def drop(self):
        """Cut every connection abruptly, like Upstox's "connection reset by peer"."""
        for ws in list(self._live):
            self.loop.call_soon_threadsafe(ws.transport.abort)
