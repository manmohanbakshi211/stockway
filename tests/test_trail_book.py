"""2.56: the "Trailing exit" paper account (same alerts as the main account, chandelier exit) and the Daily Radar
fixes (NSE holidays, its call recorded with bench signals). Mandeep, 11 Nov 2026: "go ahead" (trailing exit as a separate
account first; check the Daily Radar is not affecting the system's accuracy)."""
import datetime as dt
import json

import pandas as pd

from live import books as B
from live.clock import IST
from live.store import Store
from tests.test_books import MAIN, Inst, _ms, _t, _state, DAY


def _alert(kind="volume_surge", sev=2, R=None, key="O1", i=1):
    d = {"focus": False, "score": 40, "risk": {"verdict": "PASS", "lots": 1}}
    if R is not None:
        d["plan_R"] = {"chand": R, "atr2r": R / 1.5}
    return {"id": i, "sym": f"S{i}", "kind": kind, "severity": sev, "direction": "up", "title": "t", "und_key": "UND",
            "option": {"key": key, "label": f"S{i} 100 CE", "side": "CE", "lot": 100}, "data": d}


def test_trail_account_mirrors_main_and_uses_its_own_exit(tmp_path):
    st = Store(tmp_path / "t.db")
    bs = B.Books("upstox", st, dict(MAIN), None, log=lambda m: None)
    sp = B.cfg()["books"]["trail"]
    assert sp["exit_style"] == "chand" and sp["pick"]["same_as_main"]
    bk = bs.books["trail"]
    assert bk.cfg["kinds"] == MAIN["kinds"] and bk.cfg["min_severity"] == MAIN["min_severity"]
    now = _t(DAY, 10, 0)
    out = bs.consider(_alert(R=12.0), _state(O1=10.0), now)
    assert out["trail"] == "taken"
    t = st.trades(source="upstox#trail")[0]
    plan = json.loads(t["plan"])
    assert plan["style"] == "chand" and abs(plan["R"] - 12.0) < 1e-9          # the chart exit, not +30% / -20%
    assert out["best"] != "taken"                                              # weak alert: Best only says no
    assert bs.consider(_alert(R=None, i=2, key="O2"), _state(O2=10.0), now)["trail"].startswith("no chart risk")
    assert bs.consider(_alert(kind="orb", R=10.0, i=3, key="O3"), _state(O3=10.0), now)["trail"] != "taken"   # not a main kind
    assert st.trades(source="upstox") == []                                   # the main account is untouched


def test_unknown_exit_style_marks_only_that_account(tmp_path, monkeypatch):
    bad = {"books": {"best": B.cfg()["books"]["best"], "trail": {**B.cfg()["books"]["trail"], "exit_style": "nope"}}}
    monkeypatch.setattr(B, "cfg", lambda: bad)
    bs = B.Books("upstox", Store(tmp_path / "t.db"), dict(MAIN), None, log=lambda m: None)
    assert "trail" not in bs.books and "nope" in bs.errors["trail"] and "best" in bs.books


def test_radar_forecast_skips_nse_holidays():
    from radar.features import next_session
    from live.session import is_holiday
    assert is_holiday(dt.date(2026, 11, 10))
    assert next_session(pd.Timestamp("2026-11-09")) == pd.Timestamp("2026-11-11")
    assert next_session(pd.Timestamp("2026-11-06")) == pd.Timestamp("2026-11-09")


def test_radar_call_recorded_only_for_its_own_day(tmp_path):
    from live.engine import LiveEngine
    e = object.__new__(LiveEngine)
    e.data_dir = tmp_path / "data"
    e.data_dir.mkdir()
    e.cfg = lambda: {"output_dir": "output"}
    (tmp_path / "output").mkdir()
    f = tmp_path / "output" / "forecast_latest.json"
    f.write_text(json.dumps({"for_date": "2026-11-11", "prob_big": 0.31, "flag": True, "demo": False}))
    assert e._radar_today(dt.datetime(2026, 11, 11, 10, 0, tzinfo=IST)) == {"radar_big": 0.31, "radar_flag": 1}
    e._radar_c = None
    assert e._radar_today(dt.datetime(2026, 11, 12, 10, 0, tzinfo=IST)) == {}
    e._radar_c = None
    f.write_text(json.dumps({"for_date": "2026-11-11", "prob_big": 0.31, "flag": True, "demo": True}))
    assert e._radar_today(dt.datetime(2026, 11, 11, 10, 0, tzinfo=IST)) == {}
