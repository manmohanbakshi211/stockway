"""Expiry desk: plan from the 10-year test, entry before expiry, settlement at intrinsic value."""
import datetime as dt
import json
from types import SimpleNamespace as NS

from lab.expiry_desk import ExpiryDesk, parse_pid, weekdays_before


class Store:
    def __init__(self):
        self.d = {}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def put(self, k, v):
        self.d[k] = v


def _engine(tmp_path, exp):
    (tmp_path / "lab").mkdir()
    year = dt.datetime.now().year
    rep = {"results": {"BANKNIFTY|iron_condor": {"sym": "BANKNIFTY", "family": "iron_condor", "name": "IC",
           "years": [{"year": year, "pid": "iron_condor|entry=1,flt=none,short=1.0,wing=1.0", "train": {"avg": 900}}]},
           "NIFTY|iron_condor": {"sym": "NIFTY", "family": "iron_condor", "name": "IC", "years": [{"year": year, "pid": None}]}}}
    (tmp_path / "lab" / "eod_results.json").write_text(json.dumps(rep))
    chain, quotes = {}, {"IDX": NS(ltp=50000.0, cp=50000.0, bid=None, ask=None)}
    for k in range(48000, 52001, 100):
        chain[float(k)] = {}
        for side in ("CE", "PE"):
            key = f"{k}{side}"
            chain[float(k)][side] = NS(key=key)
            otm = (k - 50000) if side == "CE" else (50000 - k)
            px = max(5.0, 300 - otm * 0.25)
            quotes[key] = NS(ltp=px, bid=px - 0.5, ask=px + 0.5, cp=px)
    u = NS(symbol="BANKNIFTY", key="IDX", lot=30, chains={exp: chain}, near_expiry=lambda d: exp if d <= exp else None)
    e = NS(data_dir=tmp_path, store=Store(), source="sim", uni=NS(underlyings={"BANKNIFTY": u}), state=quotes,
           cfg=lambda: {"lab": {"capital": 500000, "risk_pct": 1.5}}, log=lambda *a: None)
    return e


def test_plan_entry_and_settlement(tmp_path):
    assert parse_pid("iron_condor|entry=after_expiry,flt=vrp,short=1.5,wing=2.0") == ("iron_condor", {"entry": "after_expiry", "flt": "vrp", "short": 1.5, "wing": 2.0})
    exp = dt.date(2026, 11, 24)
    assert weekdays_before(exp, 1) == dt.date(2026, 11, 23) and weekdays_before(dt.date(2026, 11, 30), 1) == dt.date(2026, 11, 27)
    e = _engine(tmp_path, exp)
    d = ExpiryDesk(e)
    assert [p["sym"] for p in d.plan] == ["BANKNIFTY"] and any("NIFTY" in w for w in d.why.values())
    d.tick(dt.datetime(2026, 11, 23, 15, 5))         # prepare legs
    assert "BANKNIFTY" in d.pending
    d.tick(dt.datetime(2026, 11, 23, 15, 21))        # enter
    t = d.trades()
    assert len(t) == 1 and t[0]["status"] == "open" and t[0]["credit"] > 0 and t[0]["lots"] >= 1
    e.state["IDX"].ltp = 50050.0                      # expires calm, between the short strikes
    d.tick(dt.datetime(2026, 11, 24, 15, 31))
    c = d.trades()[0]
    assert c["status"] == "closed" and c["net"] > 0 and c["settle"] == 50050.0
