"""Shared fixtures for Tanu's tests."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from tanu.data.facts import Facts

DAY = "2026-11-12"


def _days(n=26, last=DAY):
    f, out, d = Facts(), [], dt.date.fromisoformat(last)
    while len(out) < n:
        s = f.session(d)
        if s and s[0].hour == 9:
            out.append(d.isoformat())
        d -= dt.timedelta(days=1)
    return sorted(out)


def _t(day, m):
    return int(dt.datetime.fromisoformat(f"{day}T09:15:00+05:30").timestamp()) + 60 * m


@pytest.fixture(scope="session")
def market(tmp_path_factory):
    """Tanu data dir with NIFTY minute history, and a legacy history dir with NIFTY option minutes for the last
    three days (strikes every 50 around the money)."""
    root = tmp_path_factory.mktemp("mkt")
    base, legacy = root / "tanu", root / "legacy_history"
    (base / "history").mkdir(parents=True)
    (legacy / "NIFTY").mkdir(parents=True)
    rng = np.random.default_rng(7)
    rows, px = [], 25000.0
    days = _days()
    for i, d in enumerate(days):
        drift = (0.8 if i % 3 == 0 else -0.6 if i % 3 == 1 else 0.05)
        for m in range(375):
            o = px
            c = px + drift + rng.normal(0, 4)
            h, l = max(o, c) + abs(rng.normal(0, 2)), min(o, c) - abs(rng.normal(0, 2))
            rows.append((_t(d, m), round(o, 2), round(h, 2), round(l, 2), round(c, 2)))
            px = c
    hist = pd.DataFrame(rows, columns=["t", "o", "h", "l", "c"])
    hist.to_parquet(base / "history" / "NIFTY.parquet", index=False)
    opt = []
    for d in days[-3:]:
        day = hist[(hist["t"] >= _t(d, 0)) & (hist["t"] <= _t(d, 374))]
        lo, hi = int(day["l"].min() // 50 * 50) - 300, int(day["h"].max() // 50 * 50) + 350
        for r in day.itertuples(index=False):
            for k in range(lo, hi, 50):
                for side, sg in (("CE", 1), ("PE", -1)):
                    f = lambda x: round(max(1.0, 120 + 0.5 * sg * (x - k)), 2)
                    o_, c_ = f(r.o), f(r.c)
                    hh, ll = (f(r.h), f(r.l)) if sg > 0 else (f(r.l), f(r.h))
                    opt.append((r.t, 0, side, o_, max(hh, o_, c_), min(ll, o_, c_), c_, 0.15, 1000, 50000, float(k), r.c))
    pd.DataFrame(opt, columns=["ts", "off", "side", "open", "high", "low", "close", "iv", "volume", "oi", "strike",
                               "spot"]).to_parquet(legacy / "NIFTY" / "raw_2026-11-17.parquet", index=False)
    return base, legacy


