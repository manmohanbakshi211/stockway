"""2.58 Multiverse: every bench signal down every timeline (filters x strikes x entry times x exits), judged honestly.
Mandeep, 11 Nov 2026: "saari possibilities simultaneously test karke ... best path as fastest as we can ... 1000 magzine".
The judge must never peek: each day's pick comes only from earlier days; days (not signals) are the proof."""
import datetime as dt
import json
import sqlite3

import numpy as np
import pandas as pd

from lab import multiverse as MV
from live.clock import IST

DAYS = ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"]           # NSE session days


def _ts(day, h, m):
    return dt.datetime.combine(dt.date.fromisoformat(day), dt.time(h, m), IST).timestamp()


def _universe(tmp_path, spec):
    """spec(day, i) -> (kind, facts, path list of % moves per minute) for 40 signals a day."""
    data = tmp_path / "data"
    (data / "recordings").mkdir(parents=True)
    con = sqlite3.connect(data / "bench.db")
    con.execute("CREATE TABLE bench_signals (sig TEXT, source TEXT, ts REAL, day TEXT, sym TEXT, kind TEXT, variant TEXT, "
                "direction TEXT, alert_id INTEGER, ctx TEXT, tag TEXT)")
    con.execute("CREATE TABLE bench_trades (sig TEXT, source TEXT, day TEXT, style TEXT, key1 TEXT, entry REAL, lot INTEGER)")
    for day in DAYS:
        rows = []
        for i in range(40):
            kind, facts, moves = spec(day, i)
            t0 = _ts(day, 10, 0) + i * 120
            sig = f"{int(t0)}|S{i}|{kind}||1"
            con.execute("INSERT INTO bench_signals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (sig, "upstox", t0, day, f"S{i}", kind, None, "up", i, json.dumps(facts), json.dumps({"sev": 2, "score": 50})))
            for style, k in (("buy", f"K{day}{i}a"), ("buy_otm", f"K{day}{i}o"), ("buy_itm", f"K{day}{i}i")):
                con.execute("INSERT INTO bench_trades VALUES (?,?,?,?,?,?,?)", (sig, "upstox", day, style, k, 100.0, 100))
                px = 100.0
                for j in range(0, 340):
                    px = 100.0 * (1 + (moves[j] if j < len(moves) else moves[-1]) / 100)
                    rows.append({"ts": int(t0 // 60 * 60 + 60 * j), "key": k, "ltp": px, "bid": px, "ask": px * 1.01})
        pd.DataFrame(rows).to_parquet(data / "recordings" / f"upstox_{day}_minutes.parquet", index=False)
    con.commit()
    con.close()
    return data


def _ramp(final, at=30):
    return [final * min(1, j / at) for j in range(340)]


def test_exits_are_the_exits_a_trader_would_get():
    R = np.array([[0, 5, 12, 25, 18, -5] + [-5] * 10], np.float32)
    j_end = np.array([15])
    ex = [("tsh", 20, None, None, None), ("tsh", None, None, 4, None), ("tsh", None, None, None, 2),
          ("trail", 20, 0.5, None, None), ("tsh", None, None, None, None)]
    out = MV.outcomes(R, j_end, ex)[0]
    assert out[0] == 25           # target 20: first minute at or past it, at that minute's bid
    assert out[1] == -5           # stop 4: the minute it is broken (no fill at the stop price)
    assert out[2] == 12           # 2 minutes
    assert out[3] == -5           # after +25, keep half: falls to -5 at minute 5
    assert out[4] == -5           # held to 15:15


def test_net_pct_has_every_charge():
    from lab.engine import charge_amount
    from lab.rules import costs
    r = costs("2026-11-11")
    got = MV.net_pct(np.array([[20.0]]), np.array([10000.0]), r)[0, 0]
    want = 20.0 - charge_amount(10000.0, 12000.0, 2, "2026-11-11") / 10000.0 * 100
    assert abs(got - want) < 1e-3


def test_one_good_day_proves_nothing():
    acc = MV.Acc(1, 1)
    acc.add(np.array([[500.0]], np.float32), np.array([[25000.0]], np.float32), np.array([[10.0]], np.float32), np.array([10.0]))
    lcb, _ = MV.lcb_table(acc, 2.0, 5, 1)
    assert lcb[0, 0] == -np.inf                                               # one day: no verdict
    assert MV.t_crit(np.array([1]), 1.96)[0] > 12 and abs(MV.t_crit(np.array([500]), 1.96)[0] - 1.96) < 1e-9


def test_finds_the_real_edge_and_beats_the_luck_line(tmp_path):
    rng = np.random.default_rng(3)

    def spec(day, i):
        if i % 4 == 0:                                                       # kind A with volume: a real +40% in 30 min
            return "volume_surge", {"rel_vol": 5.0, "mins": 45}, _ramp(40)
        return "sharp_move", {"rel_vol": 1.0, "mins": 45}, _ramp(float(rng.normal(0, 15)))
    data = _universe(tmp_path, spec)
    r = MV.run(data, log=lambda m: None, days=DAYS)
    top = r["best"][0]
    assert top["filter"].get("kind") == "volume_surge" or (top["filter"].get("single") or {}).get("fact") == "rel_vol"
    assert top["lcb"] > 0 and top["lcb"] > r["luck"]["lcb"]
    assert r["timelines"] == r["filters"] * r["exits"] * 3 * 3
    assert [w["day"] for w in r["walk"]] == DAYS[2:]                         # needs 2 earlier days before a pick
    assert r["walk_total"]["best_avg"] > 10                                  # the edge holds on days it never saw
    assert r["live_picks"] and all(p["delay"] == 0 and p["strike"] in ("atm", "otm1") for p in r["live_picks"])
    saved = MV.view(data)
    assert saved["timelines"] == r["timelines"]


def test_walk_forward_never_peeks(tmp_path):
    """Kind C is great on the first three days and terrible on the last: the last day's pick is C and loses."""
    def spec(day, i):
        if i % 2 == 0:
            return "orb", {"mins": 45}, _ramp(-40 if day == DAYS[-1] else 40)
        return "day_high", {"mins": 45}, _ramp(0)
    data = _universe(tmp_path, spec)
    r = MV.run(data, log=lambda m: None, days=DAYS)
    last = r["walk"][-1]
    assert last["day"] == DAYS[-1] and last["picks"][0]["filter"].get("kind") == "orb"
    assert last["best_avg"] < -30                                            # chosen on the past, judged on the unseen day


def test_same_move_seen_twice_is_one_timeline(tmp_path):
    def spec(day, i):
        return "orb", {"mins": 45}, _ramp(10)
    data = _universe(tmp_path, spec)
    con = sqlite3.connect(data / "bench.db")
    s = con.execute("SELECT * FROM bench_signals WHERE day=? LIMIT 1", (DAYS[0],)).fetchone()
    con.execute("INSERT INTO bench_signals VALUES (?,?,?,?,?,?,?,?,?,?,?)", (s[0] + "x", s[1], s[2] + 5, s[3], s[4], "day_high", "15m",
                                                                         s[7], s[8], s[9], s[10]))
    con.commit()
    con.close()
    D = MV.load_day(data, DAYS[0], MV.cfg())
    assert D["n"] == 40 and sorted(D["kinds"][0]) == ["day_high", "orb"]


def test_a_broken_filter_is_skipped_and_listed(tmp_path):
    def spec(day, i):
        return "orb", {"mins": 45}, _ramp(5)
    data = _universe(tmp_path, spec)
    c = MV.cfg()
    c["singles"] = c["singles"] + [{"name": "bad one", "fact": "x", "op": "~", "v": 1}]
    r = MV.run(data, log=lambda m: None, days=DAYS, c=c)
    assert "bad one" in r["errors"] and r["timelines"] > 0


def test_live_match_uses_the_same_filters():
    assert MV.matches({"kind": "orb", "single": {"fact": "rel_vol", "op": ">", "v": 2}}, ["orb"], {"rel_vol": 3})
    assert not MV.matches({"kind": "orb", "single": {"fact": "rel_vol", "op": ">", "v": 2}}, ["orb"], {"rel_vol": 1})
    assert not MV.matches({"kind": "orb"}, ["day_high"], {})
    assert MV.matches({"kind": None, "single": {"cat": "regime", "is": "range"}}, ["x"], {"regime": "range"})
    assert not MV.matches({"champion": True}, ["orb"], {})
    assert not MV.matches({"single": {"fact": "rel_vol", "op": ">", "v": 2}}, ["x"], {})        # missing fact: no


def test_multiverse_paper_account_takes_only_its_picks(tmp_path):
    from live import books as B
    from live.store import Store
    from tests.test_books import MAIN, _state, _t, DAY
    data = tmp_path / "data"
    data.mkdir()
    (data / "multiverse.json").write_text(json.dumps({"live_picks": [
        {"filter": {"kind": "orb", "single": {"fact": "rel_vol", "op": ">", "v": 2}}, "strike": "atm", "delay": 0,
         "exit": ["tsh", 30, None, 15, 60], "words": "orb with volume", "lcb": 3.0}]}))
    st = Store(tmp_path / "t.db")
    bs = B.Books("upstox", st, dict(MAIN), None, log=lambda m: None, data_dir=data)
    assert bs.has_multiverse()
    now = _t(DAY, 10, 0)
    a = {"id": 1, "sym": "S1", "kind": "orb", "direction": "up", "severity": 2, "title": "t", "und_key": "UND",
         "option": {"key": "O1", "label": "S1 100 CE", "side": "CE", "lot": 100}, "data": {}}
    opt = {"key": "O1", "label": "S1 100 CE", "side": "CE", "lot": 100}
    no = bs.offer_multiverse(a, _state(O1=10.0), now, {"rel_vol": 1.0}, lambda s: opt)
    assert no["mvpick"] == "no live pick matches"
    yes = bs.offer_multiverse(a, _state(O1=10.0), now, {"rel_vol": 3.0}, lambda s: opt)
    assert yes["mvpick"] == "taken"
    t = st.trades(source="upstox#mvpick")[0]
    assert abs(t["target"] - 13.0) < 0.01 and abs(t["stop"] - 8.5) < 0.01 and t["hold_max"] == 60 and t["lots"] == 1
    assert bs.consider(a, _state(O1=10.0), now).get("mvpick") is None      # it never takes alerts the normal way
    assert st.trades(source="upstox") == []


def test_job_and_self_check():
    from lab import proc
    from live import selfcheck
    assert "multiverse" in proc.JOBS
    chk = {c["id"]: c for c in selfcheck.static_checks()} if hasattr(selfcheck, "static_checks") else None
    if chk is not None:
        assert chk["multiverse"]["ok"]
