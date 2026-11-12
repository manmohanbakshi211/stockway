"""Upstox V3 feed message (protobuf bytes) -> normalized ticks.

A tick is a dict with `key` plus whatever the message carried: ltp, cp (previous close), ltt (last trade time, epoch
ms), ltq, vol (cumulative day volume), oi, iv, atp, bid, ask, bidq, askq, tbq, tsq (total buy / sell quantity),
delta, gamma, theta, vega, day_o / day_h / day_l. Missing fields are simply absent, never zero-filled.
"""
from __future__ import annotations

from . import feed_pb2 as pb


def decode(raw: bytes) -> dict:
    """{"type": "live_feed" | "initial_feed" | "market_info", "server_ms": int, "market": {segment: status} | None,
    "ticks": [tick, ...]}"""
    fr = pb.FeedResponse.FromString(raw)
    out = {"type": pb.Type.Name(fr.type), "server_ms": int(fr.currentTs or 0), "market": None, "ticks": []}
    if out["type"] == "market_info":
        out["market"] = {k: pb.MarketStatus.Name(v) for k, v in fr.marketInfo.segmentStatus.items()}
    for key, feed in fr.feeds.items():
        which = feed.WhichOneof("FeedUnion")
        t: dict = {"key": key}
        if which == "ltpc":
            _ltpc(t, feed.ltpc)
        elif which == "firstLevelWithGreeks":
            g = feed.firstLevelWithGreeks
            _ltpc(t, g.ltpc)
            t.update(vol=g.vtt, oi=g.oi, iv=g.iv, bid=g.firstDepth.bidP, ask=g.firstDepth.askP,
                     bidq=g.firstDepth.bidQ, askq=g.firstDepth.askQ)
            _greeks(t, g.optionGreeks)
        elif which == "fullFeed":
            ff = feed.fullFeed
            if ff.WhichOneof("FullFeedUnion") == "marketFF":
                m = ff.marketFF
                _ltpc(t, m.ltpc)
                t.update(vol=m.vtt, oi=m.oi, iv=m.iv, atp=m.atp, tbq=m.tbq, tsq=m.tsq)
                if m.marketLevel.bidAskQuote:
                    q = m.marketLevel.bidAskQuote[0]
                    t.update(bid=q.bidP, ask=q.askP, bidq=q.bidQ, askq=q.askQ)
                if m.HasField("optionGreeks"):
                    _greeks(t, m.optionGreeks)
            else:
                m = ff.indexFF
                _ltpc(t, m.ltpc)
            for o in m.marketOHLC.ohlc:
                if o.interval == "1d":
                    t.update(day_o=o.open, day_h=o.high, day_l=o.low)
        else:
            continue
        out["ticks"].append(t)
    return out


def _ltpc(t: dict, x) -> None:
    t.update(ltp=x.ltp, cp=x.cp, ltt=int(x.ltt or 0), ltq=int(x.ltq or 0))


def _greeks(t: dict, g) -> None:
    t.update(delta=g.delta, gamma=g.gamma, theta=g.theta, vega=g.vega)
