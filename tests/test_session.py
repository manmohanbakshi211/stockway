"""2.48: the NSE calendar (holidays, special sessions), quotes judged by exchange time, paper money that is really there,
a new paper trade's option streamed at once, and the Options lens after the day's recording is closed.

Mandeep, 10 Nov 2026: "today is holiday still system took trades made loss", "the account money is not linked with the
trades ... sometimes it is taking trades more than the amount", "the system should auto start live market ... like on
Diwali", and the Options lens showed "Request failed (500)"."""
import datetime as dt

import pytest

from live import session as S
from live.clock import IST
from live.paper import PaperBook
from live.store import Store


def _t(day, h, m):
    return dt.datetime.fromisoformat(f"{day}T{h:02d}:{m:02d}:00").replace(tzinfo=IST)


def _ms(day, h, m):
    return int(_t(day, h, m).timestamp() * 1000)


class Inst:
    def __init__(self, ltp, ltt=None, bid=None, ask=None):
        self.ltp, self.bid, self.ask, self.ltt, self.t = ltp, bid or ltp, ask or ltp, ltt, None


OPT = {"key": "OPT", "label": "ABC 100 CE 23 Nov", "side": "CE", "lot": 100}


def _alert(**kw):
    a = {"id": 1, "sym": "ABC", "kind": "volume_surge", "severity": 3, "direction": "up", "title": "t", "option": OPT,
         "und_key": "UND", "data": {"risk": {"verdict": "PASS", "lots": 1}}}
    a.update(kw)
    return a


# ------------------------------------------------------------------ the calendar
def test_calendar_regular_holiday_weekend_and_muhurat():
    assert S.session(dt.date(2026, 11, 9))["kind"] == "regular"             # Monday
    assert S.session(dt.date(2026, 11, 10)) is None                          # NSE holiday (rules/health.json)
    assert S.is_holiday(dt.date(2026, 11, 10))
    assert S.session(dt.date(2026, 11, 14)) is None                          # Saturday
    mu = S.session(dt.date(2026, 11, 8))                                     # Sunday: Diwali Muhurat (NSE circular)
    assert mu["kind"] == "special" and mu["open"].strftime("%H:%M") == "14:20" and mu["close"].strftime("%H:%M") == "15:40"
    assert S.is_open(_t("2026-11-08", 14, 30)) and not S.is_open(_t("2026-11-08", 10, 0))
    assert S.scanner_window(_t("2026-11-08", 14, 5)) and not S.scanner_window(_t("2026-11-08", 13, 30))
    assert not S.paper_ok(_t("2026-11-08", 14, 30))                          # paper_in_special is off: a thin, symbolic hour
    assert S.is_open(_t("2026-11-09", 9, 15)) and not S.is_open(_t("2026-11-09", 9, 14)) and not S.is_open(_t("2026-11-09", 15, 31))
    assert not S.is_open(_t("2026-11-10", 10, 0)) and not S.paper_ok(_t("2026-11-10", 10, 0))
    nx = S.next_session(_t("2026-11-10", 10, 0))
    assert nx["date"] == "2026-11-11" and nx["start_scanner"].strftime("%H:%M") == "09:00"
    v = S.view(_t("2026-11-10", 10, 0))
    assert v["holiday"] and v["today"] is None and not v["open_now"] and v["next"]["open"].startswith("Wed 11 Nov")


def test_quote_judged_by_exchange_time():
    now = _t("2026-11-09", 10, 0)
    assert S.quote_fresh(_ms("2026-11-09", 9, 59), now)
    assert not S.quote_fresh(_ms("2026-11-06", 15, 29), now)                 # last Friday's price, sent again on a reconnect
    assert S.quote_fresh(None, now) and S.quote_fresh(0, now)                # unknown: only the session check applies
    assert not S.quote_fresh(_ms("2026-11-09", 9, 50), now, max_age_s=120)  # ten minutes old: too old for the underlying
    assert not S.quote_fresh(_ms("2026-11-10", 9, 59), _t("2026-11-10", 10, 0))   # no session on a holiday


# ------------------------------------------------------------------ paper: no trades on holidays or old prices
def test_no_paper_trade_on_a_holiday_or_on_an_old_quote(tmp_path):
    st = Store(tmp_path / "t.db")
    pb = PaperBook(st, "upstox", {"capital": 200000})
    hol = _t("2026-11-10", 9, 59)                                            # the 10 Nov 2026 holiday trades
    state = {"OPT": Inst(10.0, _ms("2026-11-10", 9, 58)), "UND": Inst(500.0, _ms("2026-11-10", 9, 58))}
    assert pb.try_open(_alert(), state, hol) is None
    assert pb.skipped.get("market closed (NSE calendar)") == 1
    with pytest.raises(ValueError, match="Market closed"):
        pb.open_manual(state, hol, sym="ABC", opt=OPT, direction=1, und_key="UND")
    day, now = "2026-11-09", _t("2026-11-09", 10, 0)
    state = {"OPT": Inst(10.0, _ms("2026-11-06", 15, 29)), "UND": Inst(500.0, _ms(day, 9, 59))}
    assert pb.try_open(_alert(), state, now) is None                         # option's price is from last Friday
    state = {"OPT": Inst(10.0, _ms(day, 9, 59)), "UND": Inst(500.0, _ms(day, 9, 50))}
    assert pb.try_open(_alert(), state, now) is None                         # underlying frozen for 10 minutes
    with pytest.raises(ValueError, match="old"):
        pb.open_manual(state, now, sym="ABC", opt=OPT, direction=1, und_key="UND")
    state = {"OPT": Inst(10.0, _ms(day, 9, 59)), "UND": Inst(500.0, _ms(day, 9, 59, ))}
    assert pb.try_open(_alert(), state, now)                                 # today's live prices: taken
    assert not st.trades(status="open")[0].get("alert_id") is None
    sim = PaperBook(st, "sim", {"capital": 200000})                          # the simulator keeps its own clock
    assert sim.market_ok(state, hol) is None


# ------------------------------------------------------------------ paper: never more money than the account has
def test_paper_never_uses_more_than_the_free_money(tmp_path):
    st = Store(tmp_path / "t.db")
    pb = PaperBook(st, "upstox", {"capital": 25000, "risk_pct": 50, "max_lots": 5, "max_open": 6})
    now = _t("2026-11-09", 10, 0)
    ms = _ms("2026-11-09", 9, 59)
    state = {"OPT": Inst(100.0, ms), "OPT2": Inst(100.0, ms), "OPT3": Inst(100.0, ms), "UND": Inst(500.0, ms)}
    a = _alert(data={"risk": {"verdict": "PASS", "lots": 5}})               # 5 lots x ₹10,000 = ₹50,000 > ₹25,000
    tid = pb.try_open(a, state, now)
    t = st.trades()[0]
    assert tid and t["lots"] == 2 and t["capital"] == pytest.approx(20000)  # cut to the 2 lots that fit
    assert pb.free_cash(state) == pytest.approx(5000)
    b = _alert(id=2, sym="XYZ", option={**OPT, "key": "OPT2", "label": "XYZ 100 CE"})
    assert pb.try_open(b, state, now) is None                                # 1 lot needs ₹10,000, ₹5,000 free
    assert pb.skipped.get("not enough free money") == 1
    with pytest.raises(ValueError, match="free money"):
        pb.open_manual(state, now, sym="QQQ", opt={**OPT, "key": "OPT3", "label": "QQQ 100 CE"}, direction=1, und_key="UND")
    state["OPT"] = Inst(150.0, ms)                                           # the open trade gains ₹10,000
    assert pb.free_cash(state) == pytest.approx(15000)
    assert pb.try_open(b, state, now)                                        # now one lot fits
    s = pb.summary(state)
    assert s["account"]["free"] >= 0 and s["account"]["used"] <= s["account"]["capital"] + s["account"]["unrealised"]


def test_aggressive_mode_is_capped_by_money_too(tmp_path):
    st = Store(tmp_path / "t.db")
    pb = PaperBook(st, "upstox", {"capital": 25000, "aggressive": True})
    now = _t("2026-11-09", 10, 0)
    ms = _ms("2026-11-09", 9, 59)
    state = {f"O{i}": Inst(100.0, ms) for i in range(6)}
    state["UND"] = Inst(500.0, ms)
    taken = [pb.try_open(_alert(id=i, sym=f"S{i}", option={**OPT, "key": f"O{i}", "label": f"S{i}"},
                                data={"risk": {"verdict": "BLOCK", "can_override": True}}), state, now) for i in range(6)]
    assert sum(1 for x in taken if x) == 2                                   # ₹25,000 holds two ₹10,000 lots, not six
    used = sum(t["capital"] for t in st.trades(status="open"))
    assert used <= 25000


def test_a_new_paper_trade_streams_its_option_at_once(tmp_path):
    seen = []
    pb = PaperBook(Store(tmp_path / "t.db"), "upstox", {"capital": 200000}, on_open=lambda k, u: seen.append((k, u)))
    ms = _ms("2026-11-09", 9, 59)
    pb.try_open(_alert(), {"OPT": Inst(10.0, ms), "UND": Inst(500.0, ms)}, _t("2026-11-09", 10, 0))
    assert seen == [("OPT", "UND")]
    s = pb.summary({"OPT": Inst(11.0, ms), "UND": Inst(500.0, ms)})
    assert s["open"][0]["px_ts"] == pytest.approx(ms / 1000)                # the price's exchange time is shown


# ------------------------------------------------------------------ engine: alerts only in an open session
def test_engine_trading_open_follows_calendar_and_exchange_status():
    from live.engine import LiveEngine

    class Feed:
        market_status = {}
    e = LiveEngine.__new__(LiveEngine)
    e.source, e.feed = "upstox", Feed()
    assert e._trading_open(_t("2026-11-09", 10, 0))
    assert not e._trading_open(_t("2026-11-10", 10, 0))                      # holiday
    assert not e._trading_open(_t("2026-11-09", 9, 5))                       # pre-open
    assert e._trading_open(_t("2026-11-08", 14, 30))                         # Muhurat: alerts yes (paper stays off)
    e.feed.market_status = {"NSE_FO": "NORMAL_CLOSE"}
    assert not e._trading_open(_t("2026-11-09", 10, 0))                      # the exchange says closed
    e.source = "sim"
    assert e._trading_open(_t("2026-11-10", 10, 0))


def test_feed_reads_the_exchange_market_status():
    from live import upstox_feed_pb2 as pb
    from live.upstox import decode
    fr = pb.FeedResponse(type=pb.Type.Value("market_info"), currentTs=1)
    fr.marketInfo.segmentStatus["NSE_FO"] = pb.MarketStatus.Value("NORMAL_CLOSE")
    meta = {}
    decode(fr.SerializeToString(), pb, meta)
    assert meta["market"] == {"NSE_FO": "NORMAL_CLOSE"}


# ------------------------------------------------------------------ Options lens after the recording is closed
def test_lens_reads_the_saved_day_after_the_recording_closes(tmp_path):
    import json
    import types

    from live.lens import Lens
    from live.record import Recorder
    rec = Recorder(tmp_path, "upstox", "2026-11-09")
    ts = int(_t("2026-11-09", 10, 0).timestamp())
    with rec.lock:
        rec.db.execute("INSERT INTO metrics VALUES (?,?,?)", (ts, "NIFTY", json.dumps({"spot": 24000, "pcr": 0.9})))
        rec.db.commit()
    e = types.SimpleNamespace(recorder=rec, uni=None, data_dir=tmp_path, metrics={}, market={}, context=None)
    assert Lens(e).symbol("NIFTY")["session"] == "today"
    rec.finalize(log=lambda m: None)                                         # the session is over: compressed and closed
    out = Lens(e).symbol("NIFTY")                                            # was: Cannot operate on a closed database (500)
    assert out["session"] == "2026-11-09" and out["series"]["spot"]
