"""2.95 (Mandeep, 23 Nov 2026, 09:19, market open: "check" the Speed panel): the System page no longer parses two big Lab
files for one line, the 5-year download waits out market hours like the nightly Lab, the Strategy board adds up finished
days once. The answers are the same as before."""
import inspect

from live import bench as B
from tests.test_speed_294 import _bench, _plain


def _rows(out):
    return sorted(((r["kind"], r["variant"], r["style"], tuple(sorted(r["all"].items())), tuple(sorted(r["today"].items())),
                    tuple(sorted(r["regime"].items())), r["days"]) for r in out["rows"]), key=str)


def test_board_adds_up_finished_days_once_and_today_on_every_call(tmp_path, monkeypatch):
    b = _bench(tmp_path, days=("2026-11-19", "2026-11-20", "2026-11-23"), n=200)
    monkeypatch.setattr(B, "db_for", lambda store: b)
    B._DAYS.clear()
    B._ACC.clear()
    first = B.board(None, "upstox", 100000, 5.0, "range", 60, today="2026-11-23")
    assert len(B._ACC) == 1
    again = B.board(None, "upstox", 100000, 5.0, "range", 60, today="2026-11-23")
    assert _rows(again) == _rows(first) and again["n_closed"] == first["n_closed"]
    with b.lock:                                                    # new trades close today: only today's part moves
        b.db.execute("INSERT INTO bench_trades(ts, day, source, sym, kind, variant, style, regime, net, cost_lot, risk_lot, "
                     "need_lot, is_index, name) VALUES (2.0, '2026-11-23', 'upstox', 'SBIN', 'k1', '15m', 'sell', 'quiet', 777, "
                     "15000, 3000, NULL, 1, 'name k1')")
        b.db.commit()
    later = B.board(None, "upstox", 100000, 5.0, "range", 60, today="2026-11-23")
    assert later["n_closed"] == first["n_closed"] + 1
    B._DAYS.clear()
    B._ACC.clear()                                                  # the same answer worked out from nothing
    fresh = B.board(None, "upstox", 100000, 5.0, "range", 60, today="2026-11-23")
    assert _rows(fresh) == _rows(later) and fresh["best"] == later["best"]
    mixed = B.board(None, "upstox", 100000, 5.0, None, 60, today="2026-11-23", family="mixed")
    assert len(B._ACC) == 2 and mixed["family"] == "mixed"
    k1 = [r for r in later["rows"] if r["kind"] == "k1" and r["style"] == "sell" and r["variant"] == "15m"][0]
    assert k1["today"]["n"] == [r for r in first["rows"] if r["kind"] == "k1" and r["style"] == "sell" and r["variant"] == "15m"][0]["today"]["n"] + 1


def test_system_page_uses_the_light_download_status_and_the_download_waits_out_market_hours(monkeypatch):
    from webapp import server
    assert "DHAN5Y.brief()" in inspect.getsource(server.system_view)
    assert "dhan_hist.compare" not in inspect.getsource(server.Dhan5y.brief)
    assert 'memo.memo("d5_compare"' in inspect.getsource(server.Dhan5y.view)
    assert "_market_pause()" in inspect.getsource(server.Dhan5y.start)
    monkeypatch.setattr(server, "_market_pause", lambda: True)
    d = server.Dhan5y()
    monkeypatch.setattr(server, "_lab_cfg", lambda: {"dhan_client_id": "x", "dhan_token": "y"})
    from lab import dhan_token
    monkeypatch.setattr(dhan_token, "is_dead", lambda: False)
    assert d.should_auto_start() is False                           # market hours: never starts by itself
    br = d.brief()
    assert set(br) == {"running", "status", "overview"} and br["running"] is False


def test_live_page_gets_the_short_board():
    from webapp import live_api
    src = inspect.getsource(live_api.live_board)
    assert "mini" in src and 'k != "rows"' in src
    assert "ttl=10" in inspect.getsource(live_api._live_board)
    js = open("webapp/static/live.js").read()
    assert 'api("/api/live/board?regime=auto&mini=1")' in js
