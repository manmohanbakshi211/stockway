"""2.38: market open / closed on the Live page (dated market hours), ghost candles on the Fortune chart, lot size /
quantity / money used on paper, bench and shadow trades, and every Strategy board trade with its date and time."""
import datetime as dt
import types
from zoneinfo import ZoneInfo

import numpy as np

from live import fortune as F, marketclock as MC

IST = ZoneInfo("Asia/Kolkata")


def _at(s):
    return dt.datetime.fromisoformat(s).replace(tzinfo=IST)


def test_markets_open_closed_breaks_and_holidays():
    st = lambda k, s, **kw: (MC.state(k, _at(s), **kw) or {}).get("label")
    assert st("NSE", "2026-11-04 10:00") == "Open" and st("NSE", "2026-11-04 15:31") == "Closed"
    assert st("NSE", "2026-11-07 10:00") == "Closed"                       # Saturday
    assert st("NSE", "2026-11-10 11:00") == "Holiday"                      # NSE holiday (rules/health.json)
    assert st("^N225", "2026-11-04 08:05") == "Break"                      # Tokyo lunch
    assert st("^GSPC", "2026-11-04 20:00") == "Open" and st("^GSPC", "2026-11-04 10:00") == "Closed"
    assert st("ES=F", "2026-11-04 10:00") == "Open" and st("ES=F", "2026-11-07 10:00") == "Closed"   # futures: Sunday-Friday
    assert st("BTC-USD", "2026-11-08 03:00") == "Open"                      # crypto never closes
    assert st("GIFT", "2026-11-05 01:00") == "Open" and st("GIFT", "2026-11-04 16:00") == "Break"   # overnight session, break
    t = _at("2026-11-04 08:00").timestamp()
    assert st("^HSI", "2026-11-04 09:00", last_ts=t) == "No update"           # should trade, no new price for an hour
    assert st("^HSI", "2026-11-04 09:00", last_ts=t + 3000) == "Open"
    assert MC.state("UNKNOWN", _at("2026-11-04 10:00")) is None


def test_ghost_candles_follow_the_forming_one(monkeypatch):
    from tests.test_fortune import DAY, _walk
    rows = _walk(30, seed=5, start_day=DAY - dt.timedelta(days=50))
    today0 = F._open_ts(DAY)
    rng = np.random.default_rng(9)
    px, today = rows[-1][4], []
    for i in range(70):
        o = px
        px *= 1 + rng.normal(0, 0.0008)
        today.append([today0 + 60 * i, o, max(o, px), min(o, px), px, 100])
    now = today0 + 70 * 60 + 30 + 10.5 * 60 - 60 * 10
    monkeypatch.setattr(F.time, "time", lambda: now)

    class Store(dict):
        def get(self, k, d=None):
            return dict.get(self, k, d)

        def put(self, k, v):
            self[k] = v
    e = types.SimpleNamespace(store=Store(), metrics={"ABC": {"spot": 1.0}}, events=None, cdl=None,
                              charts=types.SimpleNamespace(today_bars=lambda s: [r for r in today if r[0] < now]))
    mtf = types.SimpleNamespace(long_rows=lambda sym, base, days: ([r for r in rows if base == 1], "test"))
    d = F.Fortune(e).compute(mtf, "ABC", "15m")
    g, real = d["ghost"], d["candles"][-1]
    assert d["forming"] and g[0]["forming"] and g[0]["time"] == real["time"] and d["refresh_s"] == 10
    assert g[0]["open"] == real["open"] and g[0]["high"] >= real["high"] and g[0]["low"] <= real["low"]
    assert g[0]["close"] == d["now"]["close_mid"] and len(g) == 7                      # the forming one + 6 ahead
    assert all(g[i]["time"] > g[i - 1]["time"] and g[i]["range"] for i in range(1, len(g)))
    assert all(x["low"] <= min(x["open"], x["close"]) and x["high"] >= max(x["open"], x["close"]) for x in g)
    assert [x["time"] for x in g[1:]] == [p["time"] for p in d["cone"][1:]]
    assert all(x["open"] < x["mid"] < x["close"] and x["mid"] == p["mid"] for x, p in zip(g[1:], d["cone"][1:]))   # box around the middle
    assert g[1]["from"] == g[0]["close"] and all(g[i]["from"] == g[i - 1]["mid"] for i in range(2, len(g)))


def test_paper_bench_and_shadow_record_lot_qty_and_money(tmp_path):
    from live.paper import PaperBook
    from live.store import Store
    st = Store(tmp_path / "live.db")
    book = PaperBook(st, "upstox", {"capital": 40000})

    class I:
        ltp, ask, bid = 100.0, 100.5, 99.5
    state = {"K": I()}
    state = types.SimpleNamespace(get=lambda k: {"K": I(), "U": types.SimpleNamespace(ltp=24000.0)}.get(k))
    now = dt.datetime(2026, 11, 4, 10, 0, tzinfo=IST)
    tid = book._open(state, now, sym="NIFTY", kind="orb", opt={"key": "K", "label": "NIFTY 24000 CE", "side": "CE", "lot": 65},
                     entry=100.5, ltp=100.0, lots=2, direction=1, manual=1, und_key="U")
    t = next(x for x in st.trades(source="upstox") if x["id"] == tid)
    assert t["qty"] == 130 and t["capital"] == round(100.5 * 130, 2) and t["acct_capital"] == 40000
    sid = book._open(state, now, sym="NIFTY", kind="manual", opt={"key": "K", "label": "NIFTY 24000 PE", "side": "PE", "lot": 65},
                     entry=99.5, ltp=100.0, lots=1, direction=-1, manual=4, und_key="U")
    s = next(x for x in st.trades(source="upstox") if x["id"] == sid)
    assert s["qty"] == 65 and s["capital"] == round(24000 * 65 * 0.15, 2)     # a short: the margin estimate
    from live import bench as B
    have = {r[1] for r in B.db_for(st).db.execute("PRAGMA table_info(bench_trades)").fetchall()}
    assert {"lots", "qty", "capital"} <= have
    from live.shadow import ShadowExits
    ShadowExits(st, "upstox")
    assert {"lot", "qty", "capital"} <= {r[1] for r in st.db.execute("PRAGMA table_info(plan_outcomes)").fetchall()}


def test_board_trades_have_date_time_and_money(tmp_path):
    from tests.test_bench_styles import CFG, NOW, OPT, _full
    from live import bench as B
    from live.store import Store
    e, st = _full()
    store = Store(tmp_path / "live.db")
    b = B.Bench(store, "upstox")
    b.add({"sym": "NIFTY", "kind": "orb", "direction": "up", "option": OPT, "severity": 3, "id": 7, "data": {}}, e, NOW, CFG)
    rows = {r["style"]: r for r in b.open}
    assert rows["buy"]["qty"] == 65 and rows["buy"]["lots"] == 1 and rows["buy"]["capital"] == 100 * 65
    assert rows["sell"]["capital"] > 99 * 65                               # sold: the margin estimate, not the premium
    b.update(st, NOW.timestamp() + 3600, force=True)
    tr = B.trades(store, "upstox", "orb", None, "buy")
    assert tr and tr[0]["ts"] and tr[0]["exit_ts"] and tr[0]["qty"] == 65 and tr[0]["capital"] == 6500
    brd = B.board(store, "upstox", 500000, 2.0, today=NOW.date().isoformat(), min_n=1)
    row = next(r for r in brd["rows"] if r["kind"] == "orb" and r["style"] == "buy")
    assert row["first_ts"] and row["last_ts"] and row["first_ts"] <= row["last_ts"]
