"""Dated facts, 1-minute bars from the tape (same tape -> same bars), gap check and fill from broker candles."""
import datetime as dt

import pandas as pd
import pytest

from tanu.brokers.upstox import feed_pb2 as pb
from tanu.data import bars as B
from tanu.data import gaps as G
from tanu.data import tape as T
from tanu.data.facts import Facts

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DAY = "2026-11-13"                                              # a Friday


def _ts(h, m, s=0):
    return dt.datetime(2026, 11, 13, h, m, s, tzinfo=IST).timestamp()


def _msg(key, ltp, t, vol, oi=1000.0, bid=None):
    fr = pb.FeedResponse()
    fr.type = 1
    fr.currentTs = int(t * 1000)
    m = fr.feeds[key].fullFeed.marketFF
    m.ltpc.ltp, m.ltpc.ltt, m.vtt, m.oi = ltp, int(t * 1000), vol, oi
    if bid:
        q = m.marketLevel.bidAskQuote.add()
        q.bidP, q.askP = bid, bid + 0.1
    return fr.SerializeToString()


def test_facts_are_dated_and_sessions_follow_the_calendar():
    f = Facts()
    assert f.lot("NIFTY", DAY) == 65 and f.lot("UNKNOWN", DAY) is None
    o, c = f.session(DAY)
    assert (o.strftime("%H:%M"), c.strftime("%H:%M")) == ("09:15", "15:30")
    assert f.session("2026-11-24") is None and f.session("2026-11-14") is None      # holiday, Saturday
    o, c = f.session("2026-11-08")                                                     # Muhurat (a Sunday)
    assert (o.strftime("%H:%M"), c.strftime("%H:%M")) == ("14:20", "15:40")
    assert f.option_charges(100, 120, 65, DAY) == pytest.approx(65.02, abs=0.02)


def test_bars_from_the_tape_and_a_rebuild_is_identical(tmp_path):
    k = "NSE_FO|45138"
    w = T.TapeWriter(tmp_path / "tape", "upstox", flush_s=0.05)
    w.write(_msg(k, 99.0, _ts(9, 14, 30), 500), t=_ts(9, 14, 30))          # pre-open: outside the session
    for t, p, v in ((_ts(9, 15, 1), 100.0, 1000), (_ts(9, 15, 20), 103.0, 1300), (_ts(9, 15, 50), 101.0, 1400),
                    (_ts(9, 16, 5), 101.5, 1600), (_ts(9, 16, 40), 99.5, 1650)):
        w.write(_msg(k, p, t, v, bid=p - 0.1), t=t + 0.2)
    w.write(b"not a protobuf message", t=_ts(9, 17))                          # junk is skipped, not fatal
    w.close()
    out = B.build_day(tmp_path / "tape", "upstox", DAY, tmp_path / "bars")
    df = pd.read_parquet(out)
    assert list(df.columns) == B.COLS and len(df) == 2
    a, b = df.iloc[0], df.iloc[1]
    assert (a.t, a.o, a.h, a.l, a.c, a.cum_vol, a.vol, a.ticks) == (_ts(9, 15), 100.0, 103.0, 100.0, 101.0, 1400, 1400, 3)
    assert (b.o, b.h, b.l, b.c, b.vol, b.bid, b.src) == (101.5, 101.5, 99.5, 99.5, 250, pytest.approx(99.4), "tape")
    again = pd.read_parquet(B.build_day(tmp_path / "tape", "upstox", DAY, tmp_path / "bars2"))
    pd.testing.assert_frame_equal(df, again)


def test_gap_check_and_fill_marks_broker_bars(tmp_path):
    mins = G.session_minutes(DAY)
    assert len(mins) == 375 and mins[0] == _ts(9, 15)
    bars = pd.DataFrame([{"key": "A", "t": m, "o": 1, "h": 1, "l": 1, "c": 1, "vol": 1, "cum_vol": 1, "oi": 0,
                          "bid": None, "ask": None, "ticks": 1, "src": "tape"} for m in mins if m != _ts(10, 0)],
                        columns=B.COLS)
    gaps = G.missing(bars, ["A", "B"], DAY)
    assert gaps["A"] == [_ts(10, 0)] and len(gaps["B"]) == 375

    def fetch(key):
        if key == "B":
            raise RuntimeError("HTTP 500")
        iso = dt.datetime.fromtimestamp(_ts(10, 0), IST).isoformat()
        return [[iso, 2.0, 3.0, 1.5, 2.5, 900, 77]]
    filled, rep = G.fill(bars, gaps, fetch)
    assert rep["filled"] == 1 and rep["still_missing"] == 375 and rep["failed"][0]["key"] == "B"
    row = filled[(filled.key == "A") & (filled.t == _ts(10, 0))].iloc[0]
    assert (row.o, row.c, row.vol, row.oi, row.src) == (2.0, 2.5, 900, 77, "broker")
    assert G.missing(filled, ["A"], DAY) == {}
