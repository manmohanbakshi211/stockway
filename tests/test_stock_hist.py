"""Stock option history (2.17): download with a stand-in Upstox, then the Lab reads stocks like indices."""
import datetime as dt
import json
import math

import numpy as np
import pandas as pd

from lab import factory as F
from lab import stock_hist, upstox_hist as U
from lab.data import UNDERLYINGS, iter_days, register_stocks

E1, E2 = dt.date(2026, 8, 27), dt.date(2026, 9, 29)


def _days(a, b):
    d = a
    while d <= b:
        if d.weekday() < 5:
            yield d
        d += dt.timedelta(days=1)


def _spot(d, m):
    return 1000 + 15 * math.sin(d.toordinal() / 3) + 6 * math.sin(m / 40)


class FakeUp:
    calls = 0

    def __init__(self, token):
        pass

    def expiries(self, sym):
        return [E1.isoformat(), E2.isoformat()]

    def contracts(self, sym, expiry):
        return [{"strike_price": k, "instrument_type": s, "expired_instrument_key": f"FO|{sym}|{expiry}|{k}|{s}", "lot_size": 500}
                for k in range(900, 1101, 10) for s in ("CE", "PE")]

    def _mins(self, a, b, f):
        out = []
        for d in _days(a, b):
            for m in range(375):
                t = dt.datetime(d.year, d.month, d.day, 9, 15) + dt.timedelta(minutes=m)
                out.append([t.isoformat() + "+05:30", *f(d, m)])
        return out

    def index_minutes(self, sym, a, b):
        return self._mins(a, b, lambda d, m: [_spot(d, m)] * 4 + [1000])

    def index_daily(self, sym, a, b):
        return [[f"{d.isoformat()}T00:00:00+05:30", 1000, 1020, 980, 1000 + d.day % 7, 1] for d in _days(a, b)]

    def expired_candles(self, key, a, b):
        FakeUp.calls += 1
        _, _, exp, k, s = key.split("|")
        k = float(k)

        def px(d, m):
            S = _spot(d, m)
            intr = max(0.0, S - k) if s == "CE" else max(0.0, k - S)
            p = intr + 8 + (dt.date.fromisoformat(exp) - d).days * 0.4
            return [p, p * 1.01, p * 0.99, p, 100, 1000]
        return self._mins(a, b, px)


def test_download_registers_stock_and_lab_reads_it(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "UpstoxHistory", FakeUp)
    logs = []
    try:
        stock_hist.download("tok", tmp_path, {"TESTCO": "NSE_EQ|INE000TEST01"}, months=2, end=E2, log=logs.append)
        hd = tmp_path / "history" / "TESTCO"
        assert sorted(p.name for p in hd.glob("raw_*")) == [f"raw_{E1}.parquet", f"raw_{E2}.parquet"]
        meta = json.loads((hd / "meta.json").read_text())
        assert meta == {"key": "NSE_EQ|INE000TEST01", "step": 10.0, "lot": 500, "expiry": E2.isoformat()}
        raw = pd.read_parquet(hd / f"raw_{E2}.parquet")
        assert raw["strike"].between(940, 1060).all() and raw["strike"].nunique() == 13   # day range ± 3 strikes, not all 21
        # resumable: a second run fetches nothing new
        n = FakeUp.calls
        stock_hist.download("tok", tmp_path, {"TESTCO": "NSE_EQ|INE000TEST01"}, months=2, end=E2, log=logs.append)
        assert FakeUp.calls == n and "up to date" in logs[-1]

        UNDERLYINGS.pop("TESTCO", None)
        assert register_stocks(tmp_path) == ["TESTCO"] and UNDERLYINGS["TESTCO"]["stock"]
        days = list(iter_days(tmp_path, "TESTCO"))
        assert len(days) > 20 and all(d.step == 10 for d in days)
        df = F.build(tmp_path, syms=["TESTCO"], log=lambda m: None)
        assert len(df) and set(df["dir"].unique()) == {1, -1}     # stocks: option buying only
        assert df["ret_b3"].dtype == np.float32
    finally:
        UNDERLYINGS.pop("TESTCO", None)


def test_stock_rules_take_one_trade_a_day_across_all_stocks():
    rows = []
    for k in range(20):
        day = dt.date(2025, 1, 1) + dt.timedelta(days=k)
        for sym, tod in (("AAA", 60.0), ("BBB", 30.0), ("NIFTY", 45.0)):
            base = {f: 0.0 for f in F.FEATURES}
            base.update(tod=tod, ret_b3=1.0)
            rows.append({"sym": sym, "date": day, "bar": int(tod // 5), **base, "dir": 1, "premium": 9000,
                         **{"pnl_" + r: (100.0 if sym == "BBB" else -50.0) for r in F.EXITS}})
    b = F.Book(pd.DataFrame(rows).sort_values(["date", "sym", "bar"]))
    assert b.stock_syms == ["AAA", "BBB"] and [F.STOCKS] in b.sym_choices
    s = F.validate({"dir": 1, "conds": [["ret_b3", ">", 0.5]], "win": [15, 330], "exit": "std", "syms": [F.STOCKS]})
    t = b.trades(s)
    assert len(t) == 20 and set(t["sym"]) == {"BBB"}             # the earliest stock signal of the day, not alphabetical
    assert "any F&O stock" in F.words(s)
    assert F.sym_ok(s["syms"], "RELIANCE") and not F.sym_ok(s["syms"], "NIFTY")
    sell = F.validate({"dir": 0, "conds": [["tod", ">", 0]], "win": [5, 5], "exit": "ss25", "syms": [F.STOCKS]})
    assert sell["syms"] == ["BANKNIFTY", "NIFTY"]                 # no stock straddle selling
