"""2.87: paper and test-bench trades on a day NSE had no session (10 Nov 2026, before 2.48's calendar check) are void:
kept, never counted (Mandeep's status check, 22 Nov 2026)."""
import inspect

from live import books as B
from live import voidday as V
from live.bench import BenchDB
from live.paper import PaperBook
from live.store import Store


def _t(day, source="upstox", net=-1000.0, status="closed", reason="stop loss"):
    return {"source": source, "day": day, "sym": "NIFTY", "kind": "orb", "opt_key": "K", "label": "N 25000 CE",
            "side": "CE", "lot": 65, "lots": 1, "entry_ts": 0, "entry": 100.0, "exit": 90.0, "net": net,
            "status": status, "reason": reason, "dir": 1}


def test_holiday_paper_trades_become_void_and_leave_the_money_figures(tmp_path):
    st = Store(tmp_path / "live.db")
    for t in (_t("2026-11-10", net=-22753.0), _t("2026-11-10", source="upstox#trail"), _t("2026-11-11", net=500.0),
              _t("2026-11-10", source="sim"), _t("2026-11-15")):                     # 15 Nov is a Sunday
        st.add_trade(t)
    bdb = BenchDB(tmp_path / "bench.db")
    with bdb.lock:
        for day in ("2026-11-10", "2026-11-11"):
            bdb.db.execute("INSERT INTO bench_trades(source, day, sym, kind, net) VALUES ('upstox', ?, 'NIFTY', 'orb', -50)", (day,))
        bdb.db.commit()
    logs = []
    out = V.run(st, log=logs.append, bench_db=bdb)
    assert out["paper"] == 3 and out["bench"] == 1 and out["days"][:1] == ["2026-11-10"]
    rows = {(r["day"], r["source"]): r for r in st.trades(limit=50)}
    assert rows[("2026-11-10", "upstox")]["status"] == "void" and V.NOTE in rows[("2026-11-10", "upstox")]["reason"]
    assert rows[("2026-11-10", "sim")]["status"] == "closed"                          # practice is left alone
    assert rows[("2026-11-11", "upstox")]["status"] == "closed"
    pb = PaperBook(st, "upstox", {"capital": 100000})
    assert pb.free_cash() == 100000 + 500.0                                           # the -22,753 no longer counts
    assert B.stats(st.trades(source="upstox"), 100000)["net"] == 500.0
    with bdb.lock:
        srcs = dict(bdb.db.execute("SELECT day, source FROM bench_trades").fetchall())
    assert srcs == {"2026-11-10": "upstox|void", "2026-11-11": "upstox"}
    assert any("Void" in m for m in logs)
    again = V.run(st, log=logs.append, bench_db=bdb)                                  # idempotent
    assert again["paper"] == 0 and again["bench"] == 0
    assert len(st.trades(limit=50)) == 5                                              # nothing deleted


def test_a_special_session_is_not_void(monkeypatch):
    from live import session as S
    monkeypatch.setattr(S, "cfg", lambda: {**S.DEFAULT, "special_sessions": [{"date": "2026-11-08", "name": "Muhurat",
                                                                             "open": "18:00", "close": "19:00"}]})
    assert not V.no_session("2026-11-08") and V.no_session("2026-11-10")


def test_engine_runs_it_at_start():
    from live.engine import LiveEngine
    assert "voidday" in inspect.getsource(LiveEngine.__init__)
