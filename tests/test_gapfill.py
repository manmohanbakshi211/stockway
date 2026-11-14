"""2.74: a late start or a restart fills the day's earlier 1-minute candles (today's recording first, then Upstox),
never replacing a live candle, and the next save of the day's candles keeps the morning."""
import datetime as dt
import sqlite3
import types

from live import gapfill
from live.clock import IST
from live.record import Recorder
from live.state import Bars, MarketState

DAY = dt.date(2026, 11, 13)
T0 = dt.datetime(2026, 11, 13, 9, 15, tzinfo=IST).timestamp()


def m(i):
    return int(T0 + 60 * i)


def test_bars_fill_adds_earlier_minutes_and_never_replaces_live_ones():
    b = Bars()
    b.update(m(30), 101.0, 1000)
    b.update(m(30), 102.0, 1100)
    n = b.fill([[m(i), 100, 101, 99, 100.5, 10] for i in range(0, 31)], before=m(30))
    assert n == 30 and [x[0] for x in b.bars] == [m(i) for i in range(31)]
    assert b.bars[-1][4] == 102.0 and b.bars[-1][5] == 100          # the live minute kept its own values
    assert b.fill([[m(5), 1, 1, 1, 1, 1]]) == 0                     # already there
    small = Bars(maxlen=10)
    small.fill([[m(i), 1, 1, 1, 1, 1] for i in range(20)])
    assert len(small.bars) == 10 and small.bars[0][0] == m(10)


class Rest:
    def __init__(self):
        self.asked = []

    def intraday_1m(self, key):
        self.asked.append(key)
        rows = [[dt.datetime.fromtimestamp(m(i), IST).isoformat(), 200 + i, 201 + i, 199 + i, 200.5 + i, 5, 0] for i in range(0, 106)]
        return rows[::-1]                                             # Upstox: newest first


def engine(tmp_path):
    rec = Recorder(tmp_path, "upstox", DAY.isoformat())
    rec.bars({"NIFTY": [[m(i), 25000, 25010, 24990, 25005, 0] for i in range(0, 60)]})   # saved before a restart
    with rec.lock:
        rec.db.executemany("INSERT INTO minutes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           [(m(i), "NSE_FO|1", 100.0 + i, 1e4, 5e5 + i, 12.0, None, None, None, None, None) for i in range(60, 100)])
        rec.db.commit()
    st = MarketState()
    st.bars.setdefault("NIFTY", Bars()).update(m(105), 25100.0, None)                    # live since the restart
    uni = types.SimpleNamespace(underlyings={"NIFTY": types.SimpleNamespace(key="NSE_INDEX|Nifty 50", is_index=True),
                                             "RELIANCE": types.SimpleNamespace(key="NSE_EQ|INE002A01018", is_index=False)})
    return types.SimpleNamespace(state=st, recorder=rec, rest=Rest(), uni=uni, clock=None, source="upstox"), rec


def test_fill_restores_the_recording_then_upstox_for_the_rest(tmp_path):
    e, rec = engine(tmp_path)
    now = dt.datetime.fromtimestamp(m(105) + 20, IST)
    r = gapfill.fill(e, now=now, pace_s=0)
    nifty = [b[0] for b in e.state.bars["NIFTY"].bars]
    assert nifty == [m(i) for i in range(106)]                        # 09:15 .. now, no hole
    assert r["recording_bars"] == 60 and r["recording_syms"] == 1
    assert r["upstox_bars"] == 45 + 105 and r["upstox_syms"] == 2       # NIFTY 60..104, RELIANCE 0..104
    assert e.rest.asked == ["NSE_INDEX|Nifty 50", "NSE_EQ|INE002A01018"]   # indices first
    assert e.state.bars["NIFTY"].bars[-1][4] == 25100.0                # the live minute untouched
    assert e.state.bars["NIFTY"].bars[0][4] == 25005                   # the recording's own candle, not Upstox's
    assert r["hist_keys"] == 1 and len(e.state.get("NSE_FO|1").hist) == 40
    rec.bars({s: [list(x) for x in b.bars] for s, b in e.state.bars.items()})   # the next save keeps the morning
    con = sqlite3.connect(rec.path)
    assert con.execute("SELECT MIN(t), COUNT(*) FROM bars WHERE sym='NIFTY'").fetchone() == (float(m(0)), 106)


def test_nothing_before_the_open_or_on_a_holiday(tmp_path):
    e, _ = engine(tmp_path)
    assert gapfill.fill(e, now=dt.datetime(2026, 11, 13, 9, 10, tzinfo=IST))["skipped"]
    assert gapfill.fill(e, now=dt.datetime(2026, 11, 14, 11, 0, tzinfo=IST))["skipped"]     # Saturday
    assert e.rest.asked == []


def test_a_failing_symbol_is_skipped_and_counted(tmp_path):
    e, _ = engine(tmp_path)

    def boom(key):
        raise RuntimeError("busy")
    e.rest.intraday_1m = boom
    r = gapfill.fill(e, now=dt.datetime.fromtimestamp(m(105) + 20, IST), pace_s=0)
    assert r["upstox_failed"] == 2 and r["recording_bars"] == 60


def test_upstox_rows_parse_and_sort():
    rows = gapfill.upstox_rows([["2026-11-13T09:16:00+05:30", 1, 2, 0.5, 1.5, 10, 0], ["bad"],
                                ["2026-11-13T09:15:00+05:30", 1, 2, 0.5, 1.2, 7, 0]])
    assert rows == [[m(0), 1.0, 2.0, 0.5, 1.2, 7.0], [m(1), 1.0, 2.0, 0.5, 1.5, 10.0]]
