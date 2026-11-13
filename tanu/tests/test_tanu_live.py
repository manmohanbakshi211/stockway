"""Cutover pieces (ADR-0009): live minute bars, the universe planner, the live loop against replay (golden), a late
start, the full rehearsal through a fake broker websocket, the token keeper, alerts, Telegram, the setup routes'
guards, the live gates and the cutover switch. No network, no broker."""
import datetime as dt
import json
import os
import socket
import stat

import numpy as np
import pandas as pd
import pytest

from tanu.core.secrets import Secrets
from tanu.data import bars as B
from tanu.data.facts import Facts
from tanu.live import universe as U
from tanu.live.bars import MinuteBars

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
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


@pytest.fixture(scope="module")
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


# ---------------------------------------------------------------- bars, universe
def test_live_bars_match_the_tape_rebuild():
    from tanu.brokers.upstox import feed_pb2 as pb
    from tanu.brokers.upstox.decode import decode
    rng = np.random.default_rng(3)
    recs, mb, t0 = [], MinuteBars(), _t(DAY, 0)
    vol = 0
    for i in range(400):
        sec = t0 + int(i * 2.7)
        fr = pb.FeedResponse()
        fr.type, fr.currentTs = 1, sec * 1000
        m = fr.feeds["NSE_FO|1"].fullFeed.marketFF
        vol += int(rng.integers(1, 50))
        m.ltpc.ltp, m.ltpc.ltt, m.vtt, m.oi = float(100 + rng.normal()), sec * 1000, vol, 7
        q = m.marketLevel.bidAskQuote.add()
        q.bidP, q.askP = 99.9, 100.1
        raw = fr.SerializeToString()
        recs.append((sec + 0.3, 0, raw))
        mb.add(decode(raw)["ticks"], sec + 0.3)
    want = B.from_records(recs)
    got = pd.DataFrame(mb.close(t0 + 3600))
    assert len(got) == len(want)
    for c in ("t", "o", "h", "l", "c", "vol", "cum_vol", "oi", "bid", "ask", "ticks"):
        assert np.allclose(got[c].astype(float), want[c].astype(float)), c
    mb.add([{"key": "NSE_FO|1", "ltp": 1.0, "ltt": t0 * 1000}], t0)          # a tick for a closed minute
    assert mb.late == 1


def _rows(sym="NIFTY", spot=25000, n=30, exp="2026-11-17", stocks=0):
    ms = int(dt.datetime.fromisoformat(exp + "T15:30:00+05:30").timestamp() * 1000)
    out = []
    for k in range(spot - n * 50, spot + n * 50 + 1, 50):
        for side in ("CE", "PE"):
            out.append({"segment": "NSE_FO", "instrument_type": side, "instrument_key": f"NSE_FO|{sym}{k}{side}",
                        "underlying_symbol": sym, "underlying_key": "NSE_INDEX|Nifty 50", "underlying_type": "INDEX",
                        "expiry": ms, "strike_price": k, "lot_size": 65, "tick_size": 5.0})
    for i in range(stocks):
        for k in (90, 100, 110):
            for side in ("CE", "PE"):
                out.append({"segment": "NSE_FO", "instrument_type": side, "instrument_key": f"NSE_FO|S{i}{k}{side}",
                            "underlying_symbol": f"S{i:03d}", "underlying_key": f"NSE_EQ|S{i}", "underlying_type": "EQUITY",
                            "expiry": ms, "strike_price": k, "lot_size": 500, "tick_size": 5.0})
    return out


def test_universe_plan_fits_limits_and_recentres_only_after_moves():
    u = U.Universe(_rows(stocks=400), DAY)
    p = u.plan({"NIFTY": 25010, **{f"S{i:03d}": 100 for i in range(400)}})
    lim = u.spec["limits"]
    assert all(len(v) <= lim[m] for m, v in p.items())
    assert "NSE_INDEX|Nifty 50" in p["full"] and len([k for k in p["full"] if k.startswith("NSE_FO|NIFTY")]) == 42
    assert len(p["option_greeks"]) == 1998                      # 333 stocks x 3 strikes x 2: the rest dropped
    u2 = U.Universe(_rows(), DAY)
    first = u2.plan({"NIFTY": 25010})
    assert u2.plan({"NIFTY": 25060}) == first                    # one strike: no churn
    moved = u2.plan({"NIFTY": 25120})                            # two strikes: re-centred
    sub, unsub = U.diff(first, moved)
    assert sub["full"] and unsub and not set(unsub) & set(moved["full"])
    assert u2.expiry("NIFTY") == "2026-11-17" and u2.lot("NIFTY") == 65


# ---------------------------------------------------------------- live loop == replay
def _session(market, tmp_path, clock="exchange"):
    from tanu.live.fakebroker import FakeBroker
    from tanu.live.session import Session
    base, legacy = market
    fake = FakeBroker(DAY, base, legacy, ("NIFTY",), minute_s=0)
    spec = U.load_spec()
    spec.update(indices=["NIFTY"], index_strikes_each_side=999)
    sess = Session(base, DAY, fake.rows(), url=fake.url, token="t", out_dir=tmp_path / "live",
                   journal_path=tmp_path / "j.db", tape_dir=None, clock=clock, spec=spec, lots="facts")
    return fake, sess


def _ticks(fake, sec):
    out = []
    for key, px, cv, oi in fake.ticks[sec]:
        t = {"key": key, "ltp": px, "ltt": sec * 1000}
        if cv is not None:
            t.update(vol=cv, oi=oi, bid=round(px * 0.9986, 2), ask=round(px * 1.0014, 2))
        out.append(t)
    return out


def _cmp(live, ref):
    cols = ["setup", "sym", "d", "side", "strike", "entry_t", "exit_t", "entry", "exit", "reason", "pnl_rs"]
    a = pd.DataFrame(live)[cols].sort_values(["setup", "d", "entry_t"]).reset_index(drop=True)
    b = pd.DataFrame(ref)[cols].sort_values(["setup", "d", "entry_t"]).reset_index(drop=True)
    return a, b


def test_live_loop_trades_equal_replay(market, tmp_path):
    from tanu.live.rehearse import replay_day
    fake, sess = _session(market, tmp_path)
    for sec in sorted(fake.ticks):
        sess.loop.on_ticks(_ticks(fake, sec))
    sess.loop.finish()
    live = sess.engine.trades()
    ref = [t for t in replay_day(market[0], market[1], DAY, ["NIFTY"]) if t["day"] == DAY]
    assert len(live) >= 2 and any(t["setup"] == "random_baseline" for t in live)
    a, b = _cmp(live, ref)
    pd.testing.assert_frame_equal(a, b)
    st = json.loads((tmp_path / "live" / "status.json").read_text())
    assert st["done"] and st["minute"] == 375 and st["counts"]["late"] == 0
    assert (tmp_path / "live" / "chains" / f"{DAY}.parquet").exists()
    j = sess.journal.trades(day=DAY, account="shadow")
    assert len(j) == len([t for t in live if t["entry"] is not None])


def test_late_start_backfill_keeps_the_day_state(market, tmp_path):
    from tanu.live.rehearse import replay_day
    base, _legacy = market
    fake, sess = _session(market, tmp_path)
    hist = pd.read_parquet(base / "history" / "NIFTY.parquet")
    cut = _t(DAY, 25)                                            # Tanu started at 09:40
    morning = hist[(hist["t"] >= _t(DAY, 0)) & (hist["t"] < cut)]
    rows = [[dt.datetime.fromtimestamp(r.t, IST).isoformat(), r.o, r.h, r.l, r.c, 0, 0] for r in morning.itertuples()]
    assert sess.backfill(lambda key: rows, cut + 1) == 25
    for sec in sorted(fake.ticks):
        if sec >= cut:
            sess.loop.on_ticks(_ticks(fake, sec))
    sess.loop.finish()
    live = [t for t in sess.engine.trades() if t["entry_t"] and t["entry_t"] >= cut]
    ref = [t for t in replay_day(base, market[1], DAY, ["NIFTY"]) if t["day"] == DAY and t["entry_t"] and t["entry_t"] > cut]
    assert live and any(t["setup"] == "random_baseline" for t in live)   # needs the 09:15 open to know the move
    a, b = _cmp(live, ref)
    pd.testing.assert_frame_equal(a, b)


def test_rehearsal_through_the_fake_broker_passes(market):
    from tanu.live.rehearse import rehearse
    rep = rehearse(DAY, market[0], market[1], syms=("NIFTY",), minute_s=0.002, timeout=120)
    assert rep["finished"] and rep["same_trades"], rep["differences"]
    assert rep["tape_rebuilds_index_bars"] == {"NIFTY": True}
    assert rep["passed"] and rep["trades_live"] >= 2
    assert (market[0] / "rehearsal" / DAY / "journal.db").exists()
    assert not (market[0] / "store" / "journal.db").exists()     # the real journal is never touched


# ---------------------------------------------------------------- token keeper, secrets
def test_token_keeper_login_flow_and_secret_files(tmp_path):
    from tanu.brokers.upstox import auth as A
    now = [dt.datetime(2026, 11, 16, 8, 50, tzinfo=IST)]
    sec = Secrets(tmp_path)
    k = A.TokenKeeper(sec, clock=lambda: now[0])
    with pytest.raises(ValueError):
        k.save_app("x", "y")
    with pytest.raises(PermissionError):
        k.start_login(8765)
    k.save_app("apikey12345", "secret12345")
    assert stat.S_IMODE(os.stat(tmp_path / "secrets" / "upstox_app.json").st_mode) == 0o600
    assert stat.S_IMODE(os.stat(tmp_path / "secrets").st_mode) == 0o700
    url = k.start_login(8765)
    assert url.startswith("https://api.upstox.com/v2/login/authorization/dialog?") and "client_id=apikey12345" in url
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8765%2Fupstox%2Fcallback" in url
    state = dict(x.split("=") for x in url.split("?")[1].split("&"))["state"]
    with pytest.raises(PermissionError):
        k.finish_login("code", "wrong-state", 8765)
    k.start_login(8765)
    state = k._state
    seen = {}

    def fake_post(u, data):
        seen.update(url=u, **data)
        return 200, json.dumps({"access_token": "TOKEN-abc", "user_name": "Mandeep"}).encode()
    st = k.finish_login("code1", state, 8765, transport=fake_post)
    assert st == {"app_saved": True, "logged_in": True, "user": "Mandeep", "expires_at": "2026-11-17T03:30:00+05:30"}
    assert "TOKEN" not in json.dumps(st) and seen["client_secret"] == "secret12345" and seen["code"] == "code1"
    assert k.token() == "TOKEN-abc"
    now[0] = dt.datetime(2026, 11, 17, 3, 31, tzinfo=IST)
    assert k.token() is None and k.status()["logged_in"] is False
    assert A.token_expiry(dt.datetime(2026, 11, 17, 2, 0, tzinfo=IST)).isoformat() == "2026-11-17T03:30:00+05:30"


# ---------------------------------------------------------------- alerts, telegram
class _Sender:
    def __init__(self):
        self.msgs = []

    def send(self, text):
        self.msgs.append(text)


def test_alerts_only_for_trial_or_live_setups_and_exit_shows_rupees(market, tmp_path):
    from tanu.notify.alerts import Alerts
    fake, sess = _session(market, tmp_path)
    sess.engine.setups = [s.model_copy(update={"status": "trial"}) if s.id == "random_baseline" else s
                          for s in sess.engine.setups]           # one setup on trial, for this test only
    snd = _Sender()
    al = Alerts(snd, sess.accounts, evidence={"random_baseline": "Evidence: test"})
    sess.engine.listeners.append(al)
    for sec in sorted(fake.ticks):
        sess.loop.on_ticks(_ticks(fake, sec))
    sess.loop.finish()
    entries = [m for m in snd.msgs if m.startswith("ENTRY")]
    exits = [m for m in snd.msgs if m.startswith("EXIT")]
    assert len(entries) == 1 and "NIFTY" in entries[0] and "Stop:" in entries[0] and "Target:" in entries[0]
    assert "Evidence: test" in entries[0] and "[trial]" in entries[0]
    assert len(exits) == 1 and "₹" in exits[0] and "after costs" in exits[0]


def test_telegram_off_until_saved_and_token_never_in_checked_url(tmp_path):
    from tanu.notify.telegram import Telegram
    sec = Secrets(tmp_path)
    tg = Telegram(sec, transport=lambda u, d: (200, b"{}"))
    assert tg.send("x") is False and tg.send_now("x") == (False, "Telegram is not set up")
    with pytest.raises(ValueError):
        tg.save("nocolon", "abc")
    calls = []
    tg = Telegram(sec, transport=lambda u, d: calls.append((u, d)) or (200, b"{}"))
    tg.save("123456:ABCdef", "987654")
    assert tg.send_now("hello") == (True, "sent")
    assert calls[0][0] == "https://api.telegram.org/bot123456:ABCdef/sendMessage" and calls[0][1]["chat_id"] == "987654"


# ---------------------------------------------------------------- web setup routes, gates, cutover
def test_setup_routes_refuse_other_pages_and_never_echo_secrets(tmp_path):
    from fastapi.testclient import TestClient

    from tanu.core.config import Settings
    from tanu.ui.api import create_app
    s = Settings(web_port=8790)
    c = TestClient(create_app(tmp_path, None, settings=s), base_url="http://127.0.0.1:8790")
    body = {"api_key": "apikey12345", "api_secret": "secret12345"}
    assert c.post("/api/setup/upstox", json=body).status_code == 403                       # no X-Tanu
    assert c.post("/api/setup/upstox", json=body, headers={"X-Tanu": "1", "Origin": "https://evil.example"}).status_code == 403
    bad_host = TestClient(create_app(tmp_path, None, settings=s), base_url="http://evil.example:8790")
    assert bad_host.get("/api/setup").status_code == 403
    r = c.post("/api/setup/upstox", json=body, headers={"X-Tanu": "1", "Origin": "http://127.0.0.1:8790"})
    assert r.status_code == 200 and r.json()["upstox"]["app_saved"]
    g = c.get("/api/setup").json()
    assert "secret12345" not in json.dumps(g) and "apikey12345" not in json.dumps(g)
    assert g["switch"] is False and g["redirect_uri"] == "http://127.0.0.1:8765/upstox/callback"
    assert c.get("/upstox/login", follow_redirects=False).status_code == 409                # before cutover
    live = c.get("/api/live").json()
    assert live["switch"] is False and live["status"] is None


def test_gates_and_cutover_switch(tmp_path):
    from tanu.core.config import Settings
    from tanu.live import app as A
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    busy = srv.getsockname()[1]
    s = Settings(legacy_port=busy)
    why = A.gates(s, tmp_path)
    assert any("switch is off" in w for w in why) and any(str(busy) in w for w in why)
    assert "Not switched" in A.cutover(tmp_path, s=s)
    assert not (tmp_path / "local.json").exists()
    srv.close()
    free = Settings(legacy_port=busy, live=True)
    assert A.gates(free, tmp_path) == []
    assert "ON" in A.cutover(tmp_path, s=Settings(legacy_port=busy))
    assert json.loads((tmp_path / "local.json").read_text()) == {"live": True, "web_port": busy}
    A.cutover(tmp_path, undo=True, s=s)
    assert json.loads((tmp_path / "local.json").read_text()) == {}
    lock = A.LiveLock(tmp_path)
    (tmp_path / "live").mkdir(exist_ok=True)
    (tmp_path / "live" / "live.lock").write_text("1")                 # pid 1 is alive: someone else is live
    assert lock.holder() == 1 and not lock.take()
    assert any("already live" in w for w in A.gates(free, tmp_path))
