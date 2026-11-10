"""2.50 (Mandeep #11, #6): is the score / conviction model / Focus credible, and which facts (candles too) make a trade
better? Built from results that already happened; read-only."""
import datetime as dt
import json

import numpy as np

from live import bench as BN
from live import credibility as C
from live.clock import IST
from live.record import OUTCOME_SQL
from live.store import Store


def test_auc_ranks_winners_above_losers():
    y = np.array([1, 1, 1, 1, 1, 0, 0, 0, 0, 0], float)
    assert C.auc(np.array([9, 8, 7, 6, 5, 4, 3, 2, 1, 0], float), y) == 1.0
    assert C.auc(np.array([0, 1, 2, 3, 4, 5, 6, 7, 8, 9], float), y) == 0.0
    assert C.auc(np.ones(10), y) == 0.5
    assert C.auc(np.array([1.0, 2.0]), np.array([1.0, 0.0])) is None          # too few to judge


def _alerts(st, n=400):
    rng = np.random.default_rng(1)
    today = dt.datetime.now(IST).date()
    with st.lock:
        st.db.executescript(OUTCOME_SQL)
        for i in range(n):
            score = int(rng.integers(20, 99))
            p_hit = 0.3 + 0.5 * (score - 20) / 80                                 # a credible score: higher, better
            hit = rng.random() < p_hit
            day = (today - dt.timedelta(days=i % 8)).isoformat()
            ts = dt.datetime.combine(today, dt.time(9, 30), IST).timestamp() + (i % 300) * 60
            focus = score >= 80
            data = {"score": score, "focus": focus, "model_p": None, "risk": {"verdict": "PASS" if i % 3 else "BLOCK"},
                    "regime": "Range"}
            st.db.execute("INSERT INTO alerts (id, ts, day, source, sym, kind, direction, severity, title, data) VALUES "
                          "(?,?,?,?,?,?,?,?,?,?)", (i + 1, ts, day, "upstox", "SBIN", "orb", "up", 1 + score // 34, "t", json.dumps(data)))
            st.db.execute("INSERT INTO outcomes (alert_id, source, day, ts, sym, kind, direction, severity, is_index, result, o60) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (i + 1, "upstox", day, ts, "SBIN", "orb", "up", 1 + score // 34, 0,
                                                            "hit" if hit else "miss", 5.0 if hit else -5.0))
        st.db.commit()


def test_alert_credibility_finds_a_credible_score_and_focus(tmp_path):
    st = Store(tmp_path / "live.db")
    _alerts(st)
    a = C.alerts_check(st, "upstox", 60)
    assert a["n"] == 400 and a["decided"] == 400
    v = {x["what"]: x["verdict"] for x in a["verdicts"]}
    assert v["Signal score"] == "higher is better (credible)"
    assert v["Focus"].endswith("Focus picks better alerts")
    assert "not enough to judge" in v["Conviction model"]                        # no model chances recorded
    bands = [g for g in a["groups"] if g["group"] == "Signal score"]
    assert [g["value"] for g in bands] == ["under 40", "40-55", "55-70", "70-85", "85+"]


def _bench(st, days=12, n_day=80):
    rng = np.random.default_rng(2)
    bdb = BN.db_for(st)
    today = dt.datetime.now(IST).date()
    sigs, trades = [], []
    for d in range(days):
        day = (today - dt.timedelta(days=d)).isoformat()
        for k in range(n_day):
            nifty = int(rng.choice([1, -1]))
            cdl = int(rng.choice([1, 0, -1]))
            net = -300 + 500 * nifty + rng.normal(0, 400)                     # Nifty agreeing really helps; candles don't
            sig = f"{day}-{k}"
            sigs.append((sig, "upstox", 0.0, day, "SBIN", "orb", "", "up", None,
                         json.dumps({"nifty_al": nifty, "cdl_15m_al": cdl, "cdl_al_sum": cdl, "vix": float(rng.normal(15, 1))}), None))
            trades.append((0.0, day, "upstox", "SBIN", "orb", "buy", "up", 100, 10.0, 1000.0, net, sig))
    with bdb.lock:
        bdb.db.executemany("INSERT INTO bench_signals (sig, source, ts, day, sym, kind, variant, direction, alert_id, ctx, tag) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?,?)", sigs)
        bdb.db.executemany("INSERT INTO bench_trades (ts, day, source, sym, kind, style, direction, lot, entry, cost_lot, net, sig) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", trades)
        bdb.db.commit()


def test_facts_check_finds_what_helps_and_what_does_not(tmp_path):
    st = Store(tmp_path / "live.db")
    _bench(st)
    p = C.params_check(st, "upstox", 60)
    assert p["n"] == 960 and p["days"] == 12
    rows = {r["fact"]: r for r in p["rows"]}
    assert rows["nifty_al"]["verdict"] == "helps" and rows["nifty_al"]["gap"] > 800 and rows["nifty_al"]["same_both_halves"]
    assert rows["cdl_15m_al"]["verdict"] in ("no real difference", "leans helpful", "leans harmful") and rows["cdl_15m_al"]["candle"]
    assert "do not make other signals better" in p["candles"] or "lean" in p["candles"]
    assert rows["vix"]["kind"] == "high / low"


def test_few_days_can_only_lean(tmp_path):
    st = Store(tmp_path / "live.db")
    _bench(st, days=4, n_day=200)
    r = {x["fact"]: x for x in C.params_check(st, "upstox", 60)["rows"]}["nifty_al"]
    assert r["verdict"] == "leans helpful (only 4 days)"                      # a clear gap, but only 4 days


def test_report_reads_only(tmp_path):
    import types
    st = Store(tmp_path / "live.db")
    _alerts(st, 120)
    e = types.SimpleNamespace(store=st, source="upstox", cfg=lambda: {"live": {"source": "upstox"}})
    r = C.report(e, days=30)
    assert r["alerts"]["n"] == 120 and r["params"]["n"] == 0 and "Read-only" in r["note"]
