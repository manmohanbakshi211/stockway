"""3.05 (Mandeep, 24 Nov 2026: "okay sab karo"): the bench's trailing exit and the honest grade of every alert type."""
import datetime as dt
import time

import pytest

from live import bench as B, grade as G
from live.clock import IST
from live.store import Store
from tests.test_bench import CFG, OPT, I, St, _engine


def test_bench_trailing_style_locks_in_a_run(tmp_path):
    st = St(CE800=I(99, 100), CE850=I(74, 75), CE900=I(54, 55))
    b = B.Bench(Store(tmp_path / "live.db"), "upstox")
    now = dt.datetime(2026, 11, 25, 10, 0, tzinfo=IST)
    b.add({"sym": "NIFTY", "kind": "orb", "direction": "up", "option": {**OPT, "expiry": "2026-11-25"}, "id": 7}, _engine(st), now, CFG)
    tr = next(r for r in b.open if r["style"] == "buy_trail")
    assert tr["entry"] == 100 and tr["stop"] == 75 and tr["target"] == 600       # -25% stop, no real target
    st["CE800"] = I(150, 151)                                                    # +50%: the main exit's target hit, trail on
    b.update(st, now.timestamp() + 300)
    tr = next(r for r in b.open if r["style"] == "buy_trail")
    assert tr["stop"] == 125 and tr.get("_trailing")                            # 25% of the premium behind the best (150)
    st["CE800"] = I(124, 125)
    b.update(st, now.timestamp() + 600)
    with b.bdb.lock:
        rows = {r["style"]: dict(r) for r in b.bdb.db.execute("SELECT * FROM bench_trades").fetchall()}
    assert rows["buy_trail"]["reason"] == "trailing stop" and rows["buy_trail"]["exit"] == 124 and rows["buy_trail"]["net"] > 0
    assert rows["buy"]["reason"] == "target"                                    # the main exit, unchanged


def test_bench_style_needs_both_trailing_numbers():
    with pytest.raises(ValueError):
        B._validate("x", {"legs": [{"side": "with", "offset": 0, "q": 1}], "exit": {"trail_pct": 20}})


def test_grade_median_win_and_capped_not_the_plain_average(tmp_path):
    st = Store(tmp_path / "live.db")
    B.Bench(st, "upstox")
    bdb = B.db_for(st)
    rows = []
    days = ["2026-11-17", "2026-11-18", "2026-11-19"]
    for d in days:
        for i in range(20):
            rows.append((d, "lucky", 0, "buy", 90000 if i == 0 else -500, 5000))     # one giant winner, the rest lose
            rows.append((d, "steady", 1, "buy", 800 if i % 3 else -400, 6000))
            rows.append((d, "steady", 1, "buy_trail", 1200 if i % 2 else -500, 6000))
    with bdb.lock:
        bdb.db.executemany("INSERT INTO bench_trades(ts, day, source, sym, kind, is_index, style, net, cost_lot) "
                           "VALUES (0, ?, 'upstox', 'X', ?, ?, ?, ?, ?)", rows)
        bdb.db.commit()
    g = G.build(st, "upstox", "2026-11-20")
    by = {r["kind"]: r for r in g["rows"]}
    lucky, steady = by["lucky"], by["steady"]
    assert lucky["buy"]["avg"] > 0 and lucky["buy"]["median"] == -500 and lucky["verdict"] == "no edge"
    assert lucky["buy"]["capped_pct"] < 0                                          # the giant winner counts as +100% only
    assert steady["buy"]["median"] == 800 and steady["buy"]["win_pct"] == 65 and steady["verdict"] == "edge"
    assert steady["buy_trail"]["n"] == 60 and steady["verdict_trail"] == "edge"
    assert g["summary"] == {"types": 2, "edge": 1, "edge_index": 1, "edge_stock": 0, "trail_edge": 1}
    assert g["days"] == days and list(bdb.path.parent.joinpath("bench_cache").glob("*__grade.json.gz"))   # finished days kept
    assert G.build(st, "upstox", "2026-11-20")["rows"] == g["rows"]               # read back from the cache: the same
    v = G.view(st, "upstox", "2026-11-20")
    for _ in range(50):
        if v.get("rows"):
            break
        time.sleep(0.1)
        v = G.view(st, "upstox", "2026-11-20")
    assert v["rows"] and v["summary"]["edge"] == 1
