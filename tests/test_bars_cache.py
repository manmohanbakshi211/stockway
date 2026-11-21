"""2.80: the cached bar-table build (lab/factory.py _build_sym_cached) gives exactly the table a full build gives:
first run, after a new day, after an old day's data changes, and when the build's code or settings change. Only the
new / changed days (and days whose context changed) are priced again."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from lab import factory as F

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
STRIKES = [19700 + 50 * i for i in range(13)]


def _trading_days(n, start=dt.date(2025, 1, 6)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _day_rows(d, rng, px0):
    """One day of NIFTY option minutes: spot walk, 13 strikes, a weekly expiry on Thursdays (time value collapses)."""
    t0 = dt.datetime.combine(d, dt.time(9, 15), tzinfo=IST).timestamp()
    dte = (3 - d.weekday()) % 7
    spot = px0 + np.cumsum(rng.normal(0, 6, 375))
    rows = []
    for m in range(375):
        left = (375 - m) / 375
        tv = 25 + 22 * dte + (40 * left if dte == 0 else 0)
        if dte == 0:
            tv = 3 + 60 * left
        for k in STRIKES:
            for side in ("CE", "PE"):
                intr = max(0.0, spot[m] - k) if side == "CE" else max(0.0, k - spot[m])
                p = round(intr + tv * np.exp(-abs(spot[m] - k) / 300), 2)
                rows.append((t0 + 60 * m, spot[m], k, side, p, p * 1.01, p * 0.99, 14 + rng.normal(0, 0.3), 1000.0))
    return rows, float(spot[-1])


def make_history(root, days, seed=3, bump=None):
    rng = np.random.default_rng(seed)
    hd = root / "history" / "NIFTY"
    hd.mkdir(parents=True, exist_ok=True)
    by_month, px = {}, 20000.0
    for d in days:
        rows, px = _day_rows(d, rng, px)
        if bump and d == bump:                              # an old day's data changes (e.g. a gap filled again)
            rows = [r[:4] + (r[4] * 1.05, r[5] * 1.05, r[6] * 1.05) + r[7:] for r in rows]
        by_month.setdefault(d.strftime("%Y-%m"), []).extend(rows)
    for mth, rows in by_month.items():
        df = pd.DataFrame(rows, columns=["ts", "spot", "strike", "side", "close", "high", "low", "iv", "oi"])
        f = hd / f"raw_{mth}.parquet"
        old = pd.read_parquet(f) if f.exists() else None
        if old is None or not old.equals(df):
            df.to_parquet(f, index=False)


def _same(a, b):
    assert list(a.columns) == list(b.columns)
    k = ["date", "sym", "bar", "dir"]
    pd.testing.assert_frame_equal(a.sort_values(k).reset_index(drop=True), b.sort_values(k).reset_index(drop=True))


@pytest.fixture(autouse=True)
def _no_extras(monkeypatch):
    monkeypatch.setattr(F, "_file_exits", lambda d: [])          # no strategy packs in a temp folder anyway


def test_cached_build_equals_full_build_through_new_and_changed_days(tmp_path):
    days = _trading_days(30)
    make_history(tmp_path, days[:24])
    logs = []
    full = F.build(tmp_path, syms=["NIFTY"], log=lambda m: None, cache=False)
    first = F.build(tmp_path, syms=["NIFTY"], log=logs.append, cache=True)
    assert len(full) > 1000
    _same(full, first)
    assert any("24 priced, 0 kept" in x for x in logs)
    # a new day arrives (it changes the current month's file): only it and the days whose context changed are priced
    make_history(tmp_path, days[:25])
    logs.clear()
    inc = F.build(tmp_path, syms=["NIFTY"], log=logs.append, cache=True)
    _same(F.build(tmp_path, syms=["NIFTY"], log=lambda m: None, cache=False), inc)
    done = next(x for x in logs if "days ready (" in x)
    priced = int(done.split("(")[1].split(" priced")[0])
    assert 1 <= priced <= 8, done                          # the new day + recent days whose expiry / gap context moved
    # an old day's data changes: that day and its neighbours' context are priced again, the rest kept
    make_history(tmp_path, days[:25], bump=days[3])
    logs.clear()
    inc2 = F.build(tmp_path, syms=["NIFTY"], log=logs.append, cache=True)
    _same(F.build(tmp_path, syms=["NIFTY"], log=lambda m: None, cache=False), inc2)
    done = next(x for x in logs if "days ready (" in x)
    assert "kept" in done and int(done.split(", ")[1].split(" kept")[0]) >= 10, done


def test_code_or_settings_change_prices_everything_again(tmp_path, monkeypatch):
    make_history(tmp_path, _trading_days(12))
    F.build(tmp_path, syms=["NIFTY"], log=lambda m: None, cache=True)
    monkeypatch.setattr(F, "_sig", lambda *a, **k: "another-version")
    logs = []
    again = F.build(tmp_path, syms=["NIFTY"], log=logs.append, cache=True)
    assert any("pricing every day again" in x for x in logs) and any("12 priced, 0 kept" in x for x in logs)
    _same(F.build(tmp_path, syms=["NIFTY"], log=lambda m: None, cache=False), again)


def test_a_stopped_build_leaves_the_saved_rows_whole(tmp_path):
    days = _trading_days(14)
    make_history(tmp_path, days[:10])
    F.build(tmp_path, syms=["NIFTY"], log=lambda m: None, cache=True)
    saved = pd.read_parquet(tmp_path / "lab" / "bars_cache" / "NIFTY.parquet")
    make_history(tmp_path, days)
    n = [0]

    def stop():
        n[0] += 1
        return n[0] > 3
    F.build(tmp_path, syms=["NIFTY"], log=lambda m: None, should_stop=stop, cache=True)
    pd.testing.assert_frame_equal(saved, pd.read_parquet(tmp_path / "lab" / "bars_cache" / "NIFTY.parquet"))
    _same(F.build(tmp_path, syms=["NIFTY"], log=lambda m: None, cache=False),
          F.build(tmp_path, syms=["NIFTY"], log=lambda m: None, cache=True))
