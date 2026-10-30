"""Day-1 lessons: the risk check binds paper trades, cool-off after stops, Focus, decided win rates, day report."""
import datetime as dt
import json

from live import riskgate
from live.clock import IST
from live.paper import DEFAULTS, PaperBook
from live.store import Store


def _now(h, m, day=30):
    return dt.datetime(2026, 10, day, h, m, tzinfo=IST)


class Inst:
    def __init__(self, ltp):
        self.ltp, self.ask, self.bid = ltp, ltp, ltp


def _alert(sym="ABC", verdict="PASS", can_override=False, lots=1):
    return {"id": 1, "sym": sym, "kind": "sharp_move", "severity": 3, "title": "t",
            "option": {"key": "OPT", "label": f"{sym} 100 CE", "side": "CE", "lot": 100},
            "data": {"risk": {"verdict": verdict, "lots": lots, "can_override": can_override, "reasons": [] if verdict == "PASS" else ["x"]}}}


def test_hard_and_soft_reasons():
    opt = {"ltp": 50, "ask": 50, "bid": 49.9, "lot": 50}
    g = {"blocked": False}
    pre = riskgate.check({"sym": "A", "data": {}}, opt, now=_now(8, 54), paper_cfg=DEFAULTS, guard=g, open_syms=set())
    assert pre["verdict"] == "BLOCK" and pre["hard"] and not pre["can_override"] and "not open yet" in pre["hard"][0]
    soft = riskgate.check({"sym": "A", "data": {"setup_levels": {"rr": 0.5}}}, opt, now=_now(10, 30), paper_cfg=DEFAULTS, guard=g, open_syms=set())
    assert soft["verdict"] == "BLOCK" and not soft["hard"] and soft["can_override"]
    lim = riskgate.check({"sym": "A", "data": {}}, opt, now=_now(10, 30), paper_cfg=DEFAULTS,
                         guard={"blocked": True, "why": "Daily loss limit hit"}, open_syms=set())
    assert lim["hard"] and not lim["can_override"]
    sat = riskgate.check({"sym": "A", "data": {}}, opt, now=dt.datetime(2026, 10, 31, 11, 0, tzinfo=IST), paper_cfg=DEFAULTS, guard=g, open_syms=set())
    assert any("weekend" in r for r in sat["hard"])


def test_paper_book_respects_the_gate(tmp_path):
    st = Store(tmp_path / "t.db")
    pb = PaperBook(st, "upstox", {"capital": 500000})
    state = {"OPT": Inst(10.0)}
    now = _now(10, 30)
    assert pb.try_open(_alert(verdict="BLOCK", can_override=True), state, now, manual=True) is None
    tid = pb.try_open(_alert(verdict="BLOCK", can_override=True), state, now, manual=True, override=True)
    t = st.trades()[0]
    assert tid and t["manual"] == 2 and t["lots"] == 1
    assert pb.try_open(_alert(sym="XYZ", verdict="BLOCK", can_override=False), state, now, manual=True, override=True) is None
    assert pb.try_open(_alert(sym="PQR", verdict="PASS", lots=2), state, now, manual=True) and st.trades()[0]["lots"] == 2


def test_cooloff_after_two_stops(tmp_path):
    st = Store(tmp_path / "t.db")
    pb = PaperBook(st, "upstox", {"capital": 500000, "max_daily_loss_pct": 50})
    base = _now(10, 0).timestamp()
    for i in range(2):
        st.add_trade({"source": "upstox", "day": "2026-10-30", "sym": f"S{i}", "kind": "k", "opt_key": "x", "label": "x", "side": "CE",
                      "lot": 1, "lots": 1, "entry_ts": base + i * 60, "entry": 10, "exit_ts": base + 600 + i * 60, "exit": 8,
                      "reason": "stop loss", "status": "closed", "net": -200, "costs": 40, "manual": 0})
    g = pb.guard(_now(10, 20))
    assert g["blocked"] and "cool-off" in g["why"]
    assert not pb.guard(_now(11, 0))["blocked"]


def test_scorecard_uses_decided_cases(tmp_path):
    from live.record import OUTCOME_SQL, scorecard
    st = Store(tmp_path / "t.db")
    st.db.executescript(OUTCOME_SQL)
    rows = [("hit",)] * 6 + [("miss",)] * 4 + [("flat",)] * 30
    for i, (res,) in enumerate(rows):
        st.db.execute("INSERT INTO outcomes(alert_id, source, day, ts, sym, kind, direction, severity, is_index, p0, result) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (i + 1, "upstox", "2026-10-30", 1.0 + i, "A", "sharp_move", "up", 3, 0, 100, res))
    st.db.commit()
    s = scorecard(st, "upstox")[("sharp_move", 0)]
    assert s["n"] == 40 and s["decided"] == 10 and s["hit_rate"] == 60 and s["flat_pct"] == 75


def test_day_report(tmp_path):
    from live.dayreport import build, text
    st = Store(tmp_path / "t.db")
    day = "2026-10-30"
    ts = _now(10, 0).timestamp()
    aid = st.add_alert({"ts": ts, "day": day, "source": "upstox", "sym": "A", "kind": "sharp_move", "direction": "up", "severity": 3,
                        "title": "t", "detail": "d", "data": {"focus": True, "risk": {"verdict": "BLOCK"}}})
    st.add_alert({"ts": ts, "day": day, "source": "upstox", "sym": "B", "kind": "orb", "direction": "up", "severity": 1,
                  "title": "t", "detail": "d", "data": {"focus": False}})
    st.add_trade({"alert_id": aid, "source": "upstox", "day": day, "sym": "A", "kind": "sharp_move", "opt_key": "x", "label": "A CE",
                  "side": "CE", "lot": 1, "lots": 1, "entry_ts": _now(8, 54).timestamp(), "entry": 10, "exit_ts": ts, "exit": 8,
                  "reason": "stop loss", "status": "closed", "net": -6437, "costs": 87, "manual": 1})
    r = build(st, "upstox", day)
    p = r["paper"]
    assert p["n"] == 1 and p["by_gate"]["blocked"]["n"] == 1 and p["outside_window"]["n"] == 1
    assert r["alerts"]["total"] == 2 and r["alerts"]["focus"] == 1
    assert any("against the risk check" in l for l in r["lessons"]) and "−₹6,437" in " ".join(text(r))


def test_session_day_for_briefs():
    from live.engine import LiveEngine
    e = LiveEngine.__new__(LiveEngine)
    e.source, e.metrics = "upstox", {}
    e.thread = None
    assert e.session_day(_now(6, 11)).isoformat() == "2026-10-29"           # before the open: yesterday's session
    assert e.session_day(_now(15, 45)).isoformat() == "2026-10-30"
    assert e.session_day(dt.datetime(2026, 11, 1, 10, 0, tzinfo=IST)).isoformat() == "2026-10-30"   # Sunday -> Friday
    e.source = "sim"
    assert e.session_day(_now(6, 11)).isoformat() == "2026-10-30"
