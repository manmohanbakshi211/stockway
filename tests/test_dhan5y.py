"""2.19: Dhan 200-level order book parsing and the 5-year Lab (its own folder, the current Lab untouched)."""
import datetime as dt
import math
import struct

import pandas as pd

from live import dhandepth as D


def _pkt(code, sid, levels):
    body = b"".join(struct.pack("<dII", p, q, o) for p, q, o in levels)
    return struct.pack("<hBBiI", 12 + len(body), code, 2, sid, len(levels)) + body


def test_depth_parse_and_stats():
    bids = [(100.0 - i * 0.05, 100 + i, 3) for i in range(200)]
    asks = [(100.1 + i * 0.05, 50, 2) for i in range(200)]
    asks[5] = (asks[5][0], 9000, 40)                        # a sell wall
    out = D.parse(_pkt(41, 777, bids) + _pkt(51, 777, asks))
    assert [(s, side, len(lv)) for s, side, lv in out] == [(777, "bid", 200), (777, "ask", 200)]
    st = D.stats(out[0][2], out[1][2])
    assert st["mid"] == 100.05 and st["wall_ask_qty"] == 9000 and st["imb"] > 0
    assert -1 <= st["imb_near"] <= 1


class FakeDhan:
    calls = 0

    fail = 0                      # next N calls raise a network error
    empty_days = set()            # days Dhan has no data for

    def rolling(self, sym, flag, off, side, frm, to, code=1, interval=1, security_id=None, instrument="OPTIDX"):
        from lab import dhan_budget
        from lab.dhan import DhanError
        FakeDhan.calls += 1
        dhan_budget.spend(1)
        if FakeDhan.fail > 0:
            FakeDhan.fail -= 1
            raise DhanError("Network error: offline")
        if frm in FakeDhan.empty_days:
            return {}
        a, b = dt.date.fromisoformat(frm), dt.date.fromisoformat(to)
        ts, d = [], a
        while d < b:
            if d.weekday() < 5:
                t0 = int(dt.datetime(d.year, d.month, d.day, 9, 15, tzinfo=dt.timezone.utc).timestamp()) - 19800
                ts += [t0 + 60 * m for m in range(375)]
            d += dt.timedelta(days=1)
        step = 50 if instrument == "OPTIDX" else 10
        spot = [1000 + 5 * math.sin(i / 50) for i in range(len(ts))]
        k = [round(s / step) * step + off * step for s in spot]
        px = [max(1, (s - kk if side == "CE" else kk - s)) + 10 for s, kk in zip(spot, k)]
        return {"timestamp": ts, "open": px, "high": px, "low": px, "close": px, "iv": [15] * len(ts), "volume": [1] * len(ts),
                "strike": k, "oi": [1] * len(ts), "spot": spot}


def test_five_year_download_is_separate_and_resumable(tmp_path, monkeypatch):
    from lab import dhan_hist as H
    monkeypatch.setattr(H, "START", dt.date(2024, 7, 1))
    monkeypatch.setattr(H, "stock_ids", lambda cache, syms, fetch=True: {"TESTCO": {"id": 1333, "lot": 500}})
    up = tmp_path / "history" / "NIFTY"
    up.mkdir(parents=True)
    pd.DataFrame({"ts": [1]}).to_parquet(up / "raw_2024-10-03.parquet")      # Upstox history begins Oct 2024
    rep = H.download(FakeDhan(), tmp_path, ["TESTCO"], log=lambda m: None)
    r5 = H.root(tmp_path) / "history"
    nifty = sorted(p.name for p in (r5 / "NIFTY").glob("raw_*"))
    assert "raw_2024-07.parquet" in nifty and "raw_2024-10-03.parquet" in nifty   # Dhan months + linked Upstox file
    assert not any(p.name == "raw_2024-10.parquet" for p in (r5 / "NIFTY").glob("*"))
    assert (r5 / "TESTCO" / "meta.json").exists() and rep["stocks"] == ["TESTCO"]
    assert sorted(p.name for p in up.iterdir()) == ["raw_2024-10-03.parquet"]     # the current history is untouched
    n = FakeDhan.calls
    H.download(FakeDhan(), tmp_path, ["TESTCO"], log=lambda m: None)
    assert FakeDhan.calls == n                                                      # resumable: nothing refetched
    from lab import factory as F
    from lab.data import UNDERLYINGS
    try:
        df = F.build(H.root(tmp_path), syms=["TESTCO"], log=lambda m: None)
        assert len(df) and df["ret_b3"].dtype.name == "float32" and set(df["bar"] % 3) == {2}   # compact: 15-min entries
    finally:
        UNDERLYINGS.pop("TESTCO", None)


def _days_on_disk(tmp_path):
    from lab import dhan_hist as H
    m = H.Manifest(H.root(tmp_path))
    return sum(len(v["d"]) for v in m.m.values())


def test_daily_call_budget_stops_and_resumes(tmp_path, monkeypatch):
    from lab import dhan_budget as B
    from lab import dhan_hist as H
    monkeypatch.setattr(H, "START", dt.date(2026, 8, 1))
    monkeypatch.setattr(H, "DAILY_CAP", 42 * 25)            # about 25 index days
    monkeypatch.setattr(B, "_used", 0)
    monkeypatch.setattr(B, "_day", dt.date.today().isoformat())
    monkeypatch.setattr(H, "stock_ids", lambda cache, syms, fetch=True: {})
    logs = []
    r = H.download(FakeDhan(), tmp_path, [], log=logs.append)
    assert r["result"] == "budget" and any("budget" in m for m in logs)
    n1 = _days_on_disk(tmp_path)
    assert 0 < n1 <= 25
    monkeypatch.setattr(B, "_used", 0)                       # a new day
    monkeypatch.setattr(H, "DAILY_CAP", 10 ** 6)
    r = H.download(FakeDhan(), tmp_path, [], log=logs.append)
    assert r["result"] == "done" and _days_on_disk(tmp_path) > n1
    monkeypatch.setattr("lab.stock_hist.DEFAULT", [])
    ov = H.overview(tmp_path)
    assert ov["months_pending"] == 0 and ov["pct"] == 100.0


def test_holes_network_errors_and_empty_days(tmp_path, monkeypatch):
    """A hole inside a month is filled; a network error is retried (never taken as a holiday); a real no-data day is
    remembered and not asked again."""
    from lab import dhan_budget as B
    from lab import dhan_hist as H
    monkeypatch.setattr(H, "START", dt.date(2026, 9, 1))
    monkeypatch.setattr(H, "PAUSES", (0,))
    monkeypatch.setattr(H.time, "sleep", lambda s: None)
    monkeypatch.setattr(B, "_used", 0)
    monkeypatch.setattr(H, "stock_ids", lambda cache, syms, fetch=True: {})
    monkeypatch.setattr(H, "_months_for", lambda d, sym, end: [(dt.date(2026, 9, 1), dt.date(2026, 10, 1))] if sym == "NIFTY" else [])
    FakeDhan.empty_days = {"2026-09-15"}
    FakeDhan.fail = 4                                         # the first day hits a network drop
    H.download(FakeDhan(), tmp_path, [], log=lambda m: None)
    f = H.root(tmp_path) / "history" / "NIFTY" / "raw_2026-09.parquet"
    days = H.file_days(f)
    weekdays = {d for d in range(1, 31) if dt.date(2026, 9, d).weekday() < 5}
    assert days == weekdays - {15}                           # the network drop was retried, the 15th has no data
    man = H.Manifest(H.root(tmp_path))
    assert man.done("NIFTY", dt.date(2026, 9, 15))           # remembered as "no data", not asked again
    # punch a hole: drop two days from the file and the manifest, as an old download would have left them
    df = pd.read_parquet(f)
    keep = ~pd.to_datetime(df["ts"] + 19800, unit="s").dt.day.isin([8, 9])
    df[keep].to_parquet(f, index=False)
    (H.root(tmp_path) / "manifest.json").unlink()
    FakeDhan.empty_days, n = set(), FakeDhan.calls
    H.download(FakeDhan(), tmp_path, [], log=lambda m: None)
    assert H.file_days(f) == weekdays                         # holes (8th, 9th) and the 15th filled
    assert FakeDhan.calls - n == 3 * 42                       # only the three missing days were fetched


def test_news_and_movers_parsing(tmp_path):
    from live import dhanintel as I

    class FD:
        client_id = "1"

        def post(self, path, body):
            assert "PORTFOLIO" not in str(body) and "order" not in path
            if path == "/data/newsheadline":
                return {"data": [{"headline": "Reliance jumps on results", "timestamp": 1793000000000, "securityIds": ["2885"]}]}
            if body["category"] == "PRICE_GAINERS":
                return {"data": [{"symbol": "TATASTEEL"}, {"securityId": "2885"}]}
            if body["category"] == "PRICE_LOSERS":
                return {"data": []}
            return {"data": {"list": [{"tradingSymbol": "RELIANCE-Nov2026-1400-CE"}]}}
    it = I.DhanIntel(FD(), tmp_path)
    it.ids = {"2885": "RELIANCE"}
    n = it.news()
    assert n[0]["dhan_syms"] == ["RELIANCE"] and abs(n[0]["ts"] - 1793000000) < 1
    it.refresh_movers(dt.date(2026, 11, 24))
    assert it.movers["price_gainers"] == ["TATASTEEL", "RELIANCE"] and it.movers["opt_oi_gainers"] == ["RELIANCE"]
    r = it.mover_rank("RELIANCE")
    assert r["rank"] == 1 and "price_gainers" in r["lists"]
    from live.learn import _mover_dir
    assert _mover_dir(r) == 1 and _mover_dir({"rank": None, "lists": []}) == 0 and _mover_dir({}) is None
