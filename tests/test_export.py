"""2.49: everything the app tests in one Excel file (Mandeep, 10 Nov 2026, #10: "make its excel sheet also so I can
check what's going on; which alert is working or not, verified daily and historically")."""
import datetime as dt
import io
import json
import shutil
import types
from pathlib import Path

import pandas as pd

from live import export as X
from live.clock import IST
from live.record import OUTCOME_SQL
from live.store import Store

ROOT = Path(__file__).resolve().parents[1]


def _engine(tmp_path):
    st = Store(tmp_path / "live.db")
    with st.lock:
        st.db.executescript(OUTCOME_SQL)
        today = dt.datetime.now(IST).date()
        rows = []
        for i in range(30):                                          # volume_surge: 24 of 30 worked -> Working
            rows.append((i, "upstox", (today - dt.timedelta(days=i % 3)).isoformat(), 1e9 + i, "SBIN", "volume_surge", "up",
                         3, 0, "hit" if i < 24 else "miss", 5.0, 9.0))
        for i in range(30, 55):                                      # orb: 5 of 25 worked -> Not working
            rows.append((i, "upstox", today.isoformat(), 1e9 + i, "TCS", "orb", "down", 2, 0, "hit" if i < 35 else "miss", -3.0, -6.0))
        rows.append((99, "upstox", today.isoformat(), 1e9 + 99, "INFY", "day_high", "up", 2, 0, "hit", 2.0, 4.0))
        st.db.executemany("INSERT INTO outcomes (alert_id, source, day, ts, sym, kind, direction, severity, is_index, result, "
                          "o15, o60) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        st.db.commit()
    lab = tmp_path / "lab"
    lab.mkdir()
    rule = {"id": "S1", "rule": "Buy the ATM call ...", "dir": 1, "syms": ["NIFTY"], "exit": "runner", "stage": "promoted",
            "evidence": "weak", "found": "2026-11-03", "train": {"n": 100, "avg": 500, "total": 50000, "win_pct": 52, "pf": 1.5},
            "valid": {"n": 30, "avg": 300, "total": 9000, "win_pct": 50, "pf": 1.3},
            "hold": {"n": 30, "avg": -50, "total": -1500, "win_pct": 45, "pf": 0.9}}
    (lab / "factory.json").write_text(json.dumps({"rules": {"S1": rule}, "period": {"train": ["a", "b"]}, "updated": "x"}))
    cfg = {"live": {"source": "upstox", "paper": {"capital": 200000, "risk_pct": 1.0}}}
    return types.SimpleNamespace(store=st, source="upstox", paper=None, books=None, data_dir=tmp_path, cfg=lambda: cfg)


def test_verdicts_need_enough_data():
    assert X.verdict(10, 80, 5) == "Not enough data yet"
    assert X.verdict(30, 60, 2) == "Working"
    assert X.verdict(30, 60, -1) == "Not working"                    # wins often but loses money on average
    assert X.verdict(30, 40, 1) == "Not working"
    assert X.verdict(30, 50, 1) == "Mixed"


def test_workbook_has_every_sheet_and_the_right_verdicts(tmp_path):
    e = _engine(tmp_path)
    sh = X.build(e, days=60, trades_days=5)
    assert list(sh)[:3] == ["Read me", "Alert check", "Alert check by day"]
    for name in ("Test bench strategies", "Test bench by day", "Exit styles (shadow)", "Strategy Lab 1 year",
                 "Strategy Lab 5 years", "Paper accounts", "Alerts (last days)", "Test bench trades"):
        assert name in sh
    ac = sh["Alert check"].set_index("code")
    assert ac.loc["volume_surge", "Verdict"] == "Working" and ac.loc["volume_surge", "Worked %"] == 80.0
    assert ac.loc["orb", "Verdict"] == "Not working"
    assert ac.loc["day_high", "Verdict"] == "Not enough data yet"
    assert ac.loc["volume_surge", "Days"] == 3
    byday = sh["Alert check by day"]
    assert set(byday["Day"]) == {(dt.datetime.now(IST).date() - dt.timedelta(days=k)).isoformat() for k in range(3)}
    lab = sh["Strategy Lab 1 year"]
    assert lab.iloc[0]["Holdout avg ₹"] == -50 and lab.iloc[0]["Positive in all three"] == "no"
    assert {r["id"] for r in sh["Paper accounts"].to_dict("records")} >= {"main", "best", "target3"}


def test_excel_file_opens_with_all_sheets(tmp_path):
    e = _engine(tmp_path)
    data, ext, media = X.to_xlsx(X.build(e))
    assert ext == "xlsx" and media.endswith("sheet")
    book = pd.read_excel(io.BytesIO(data), sheet_name=None)
    assert "Alert check" in book and "Strategy Lab 1 year" in book and len(book["Alert check"]) == 3


def test_export_only_reads(tmp_path):
    e = _engine(tmp_path)
    before = (tmp_path / "live.db").stat().st_size
    X.to_xlsx(X.build(e))
    assert not any(n in X.__dict__ for n in ("place_order", "modify_order", "cancel_order"))
    assert (tmp_path / "live.db").stat().st_size == before
