"""3.05 (Mandeep, 24 Nov 2026: "okay sab karo"): every paper account against the main account on the same days; after
20 market days the ones behind AND losing are switched off (record kept); you can turn them back on."""
import datetime as dt
import json

from live import books as B
from live.store import Store
from tests.test_books import MAIN


def _trade(st, src, day, net):
    st.add_trade({"source": src, "day": day, "sym": "NIFTY", "status": "closed", "net": float(net), "entry": 1.0, "exit": 1.0,
                  "entry_ts": 1.0, "exit_ts": 2.0})


def test_scorecard_retire_and_keep(tmp_path):
    st = Store(tmp_path / "t.db")
    days = [(dt.date(2026, 10, 1) + dt.timedelta(days=i)).isoformat() for i in range(22)]
    for i, d in enumerate(days):
        _trade(st, "upstox", d, 1000)                                  # main: +1,000 a day
        _trade(st, "upstox#best", d, -200)                             # behind and losing: retire
        _trade(st, "upstox#trail", d, 3000 if i % 2 else -500)         # ahead
        _trade(st, "upstox#pro_index", d, -300)                        # behind and losing, but Pro: your call
        if i >= 10:
            _trade(st, "upstox#rider", d, -900)                        # behind, only 12 days: still learning
    rows = {r["id"]: r for r in B.scorecard(st, "upstox", days[-1])}
    assert rows["best"]["days"] == 22 and rows["best"]["net"] == -4400 and rows["best"]["main_net"] == 22000
    assert rows["best"]["verdict"] == "behind" and rows["best"]["retire"] is True and rows["best"]["behind_days"] == 22
    assert rows["trail"]["verdict"] == "ahead" and rows["trail"]["retire"] is False and rows["trail"]["ahead_days"] == 11
    assert rows["pro_index"]["verdict"] == "behind" and rows["pro_index"]["retire"] is False and rows["pro_index"]["mode"] == "show"
    assert rows["rider"]["verdict"] == "learning" and rows["rider"]["days"] == 12 and rows["rider"]["main_net"] == 12000
    assert rows["target3"]["verdict"] == "no trades yet"
    logs = []
    assert B.apply_review(tmp_path, list(rows.values()), days[-1], log=logs.append) == ["best"]
    assert "switched off by the scorecard" in logs[0]
    bs = B.Books("upstox", st, dict(MAIN), None, log=lambda m: None, data_dir=tmp_path)
    assert "best" not in bs.books and bs.retired["best"]["since"] == days[-1] and "trail" in bs.books
    assert len(st.trades(source="upstox#best")) == 22                   # the record is kept
    assert B.apply_review(tmp_path, list(rows.values()), days[-1], log=logs.append) == []    # once
    B.keep_on(tmp_path, "best")
    assert "best" in B.Books("upstox", st, dict(MAIN), None, log=lambda m: None, data_dir=tmp_path).books
    assert B.apply_review(tmp_path, list(rows.values()), days[-1], log=logs.append) == []    # you kept it: never again
    assert json.loads((tmp_path / "accounts_review.json").read_text())["kept"] == ["best"]


def test_review_runs_once_after_the_close(tmp_path):
    from live.clock import IST
    from live.engine import LiveEngine
    e = object.__new__(LiveEngine)
    e.brief_done = set()
    d = dt.datetime(2026, 11, 25, 16, 11, tzinfo=IST)
    assert e._review_due(dt.datetime(2026, 11, 25, 15, 0, tzinfo=IST), "upstox") is False
    assert e._review_due(d, "upstox") is True and e._review_due(d, "upstox") is False
    assert e._review_due(dt.datetime(2026, 11, 26, 16, 30, tzinfo=IST), "sim") is False
