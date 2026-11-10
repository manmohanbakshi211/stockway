"""2.55: the promoted Strategy Lab straddle rules become one range-day sell signal per index per day; naked paper
selling is off (Mandeep, 11 Nov 2026: sell only sideways and risk-controlled; "i think it's not hedging")."""
import datetime as dt
import json
from pathlib import Path

from live import chartdata as CD

ROOT = Path(__file__).resolve().parents[1]


class E:
    def __init__(self, code):
        self.regimes = {"NIFTY": {"code": code, "label": code.title()}}


def _cd(code):
    c = object.__new__(CD.ChartData)
    c.e = E(code)
    c.calls = []
    c._paper_straddle = lambda sym, m, r, now: c.calls.append(sym) or "NIFTY 25000 CE + PE"
    return c


def _rule(i):
    return {"id": f"S0000000{i}", "rule": f"Sell the ATM straddle rule {i}.", "exit": "ss25", "dir": 0}


def test_many_copies_one_signal_out_of_focus_and_no_paper():
    c, out = _cd("range"), []
    now = dt.datetime(2026, 11, 11, 10, 30)
    for i in range(5):
        c._range_sell("NIFTY", {"spot": 25000.0}, _rule(i), {"n": 30, "avg": 400}, now, out.append)
    assert len(out) == 1 and c.calls == []
    a = out[0]
    assert a["kind"] == "range_sell" and a["severity"] == 2 and a["direction"] == "neutral"
    assert a["data"]["fits"] and "Range day" in a["detail"] and "naked selling is off" in a["detail"]
    assert len(c._rs_day[("NIFTY", now.date())]["ids"]) == 5                     # the others counted as agreeing
    c._range_sell("NIFTY", {"spot": 25000.0}, _rule(9), {}, now + dt.timedelta(days=1), out.append)
    assert len(out) == 2                                                          # a new day: a new signal


def test_trend_day_says_skip():
    c, out = _cd("trend_up"), []
    c._range_sell("NIFTY", {"spot": 25000.0}, _rule(1), {}, dt.datetime(2026, 11, 11, 10, 30), out.append)
    assert not out[0]["data"]["fits"] and "Not a range day" in out[0]["detail"]


def test_naked_paper_only_when_asked_and_only_on_range_days(monkeypatch):
    monkeypatch.setattr(CD, "range_sell_cfg", lambda: {"paper": "naked", "max_signals_per_day": 1, "regimes": ["range"], "severity": 2})
    c, out = _cd("range"), []
    c._range_sell("NIFTY", {"spot": 25000.0}, _rule(1), {}, dt.datetime(2026, 11, 11, 10, 30), out.append)
    assert c.calls == ["NIFTY"] and "Paper-sold" in out[0]["detail"]
    c2, out2 = _cd("trend_down"), []
    c2._range_sell("NIFTY", {"spot": 25000.0}, _rule(1), {}, dt.datetime(2026, 11, 11, 10, 30), out2.append)
    assert c2.calls == []


def test_defaults_are_safe():
    cfg = json.loads((ROOT / "rules" / "range_sell.json").read_text())
    assert cfg["paper"] == "off" and cfg["severity"] < 3 and cfg["max_signals_per_day"] == 1
    assert CD.range_sell_cfg()["paper"] == "off"
    src = (ROOT / "live" / "chartdata.py").read_text()
    assert src.count("self._paper_straddle(") == 1 and 'cfg.get("paper") == "naked" and fits' in src
