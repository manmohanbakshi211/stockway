"""The feed process against a fake Upstox: ticks reach the app, every message lands on the tape byte for byte, a
dropped connection reconnects and resubscribes, the decoder reads every feed shape. No network, no token."""
import datetime as dt
import time

import pytest

from tanu.brokers.upstox import decode as D
from tanu.brokers.upstox import feed_pb2 as pb
from tanu.core import safety
from tanu.data import feed as F
from tanu.data import tape as T
from tanu.tests.fake_upstox import FakeUpstox, message

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _wait(cond, t=15.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_decoder_reads_index_stock_option_and_market_info():
    raw = message({"NSE_INDEX|Nifty 50": 25300.0, "NSE_FO|45138": 120.5}, ts_ms=1_794_454_620_000)
    d = D.decode(raw)
    assert d["type"] == "live_feed" and d["server_ms"] == 1_794_454_620_000
    by = {t["key"]: t for t in d["ticks"]}
    assert by["NSE_INDEX|Nifty 50"]["ltp"] == 25300.0 and "bid" not in by["NSE_INDEX|Nifty 50"]
    o = by["NSE_FO|45138"]
    assert (o["ltp"], o["bid"], o["ask"], o["vol"], o["oi"], o["ltq"]) == (120.5, 120.45, 120.55, 1120, 5000, 75)
    fr = pb.FeedResponse()
    fr.type = pb.Type.Value("market_info")
    fr.marketInfo.segmentStatus["NSE_FO"] = pb.MarketStatus.Value("NORMAL_OPEN")
    assert D.decode(fr.SerializeToString())["market"] == {"NSE_FO": "NORMAL_OPEN"}
    g = pb.FeedResponse()
    x = g.feeds["NSE_FO|1"].firstLevelWithGreeks
    x.ltpc.ltp, x.iv, x.optionGreeks.delta, x.firstDepth.bidP = 50.0, 0.15, 0.52, 49.9
    t = D.decode(g.SerializeToString())["ticks"][0]
    assert (t["ltp"], t["iv"], t["delta"], t["bid"]) == (50.0, pytest.approx(0.15), pytest.approx(0.52), pytest.approx(49.9))


def test_feed_process_records_every_message_and_survives_a_drop(tmp_path):
    srv = FakeUpstox()
    got = []
    f = F.FeedProcess("fake-token", on_ticks=got.extend, url=srv.url, tape_dir=tmp_path, source="fake", min_free_gb=0)
    f.start()
    try:
        f.subscribe(["NSE_INDEX|Nifty 50", "NSE_FO|45138"], "full")
        assert _wait(lambda: len(got) >= 2)
        assert {t["key"] for t in got} == {"NSE_INDEX|Nifty 50", "NSE_FO|45138"} and f.connected
        srv.push({"NSE_FO|45138": 121.0})
        assert _wait(lambda: any(t.get("ltp") == 121.0 for t in got))
        srv.drop()                                              # the broker cuts the connection
        assert _wait(lambda: srv.conns >= 2 and len(srv.subs) >= 2)
        assert srv.subs[-1][1:] == ("sub", ["NSE_FO|45138", "NSE_INDEX|Nifty 50"], "full")   # resubscribed
        assert _wait(lambda: f.msgs >= 3)
    finally:
        f.stop()
    day = dt.datetime.now(IST).date().isoformat()
    recs = list(T.read_day(tmp_path, "fake", day))
    msgs = [p for _, k, p in recs if k == T.KIND_MSG]
    assert msgs == srv.sent                                      # byte for byte, in order
    events = [p.decode() for _, k, p in recs if k == T.KIND_CTRL]
    assert any('"event":"sub"' in e and '"mode":"full"' in e for e in events)
    assert any('"event":"disconnect"' in e for e in events)
    assert sum('"event":"connect"' in e for e in events) >= 2         # the reconnect is on the tape too


def test_feed_refuses_a_trading_url():
    with pytest.raises(safety.BlockedCall):
        F.FeedProcess("t", url="wss://api.upstox.com/v2/order/place")
    with pytest.raises(ValueError):
        F.FeedProcess("t", url="ws://127.0.0.1:1/x").subscribe(["A"], "everything")
