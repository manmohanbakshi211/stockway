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

    def rolling(self, sym, flag, off, side, frm, to, code=1, interval=1, security_id=None, instrument="OPTIDX"):
        FakeDhan.calls += 1
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


def test_daily_call_budget_stops_and_resumes(tmp_path, monkeypatch):
    from lab import dhan_hist as H
    monkeypatch.setattr(H, "START", dt.date(2024, 8, 1))
    monkeypatch.setattr(H, "DAILY_CAP", 42 * 25)            # about one index month
    monkeypatch.setattr(H, "stock_ids", lambda cache, syms, fetch=True: {})
    logs = []
    H.download(FakeDhan(), tmp_path, [], log=logs.append)
    got = sorted(p.name for p in (H.root(tmp_path) / "history").glob("*/raw_*.parquet"))
    assert len(got) == 1 and any("budget" in m for m in logs)
    monkeypatch.setattr(H, "_budget", lambda d: {"f": tmp_path / "b.json", "day": "x", "used": 0})
    monkeypatch.setattr(H, "DAILY_CAP", 10 ** 6)
    H.download(FakeDhan(), tmp_path, [], log=logs.append)
    assert len(list((H.root(tmp_path) / "history").glob("*/raw_*.parquet"))) > 1


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
