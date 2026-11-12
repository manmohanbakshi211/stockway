"""2.52: Kite (Zerodha) as the backup live feed and cross-check for Upstox. Market data only, never orders.

Mandeep, 10 Nov 2026: "okay let's do it" (Kite, Dhan and Upstox, each at its best)."""
import datetime as dt
import hashlib
import struct
import time
import types
from pathlib import Path

import pytest

from live import feeds as F
from live import kite as K
from live.clock import IST

ROOT = Path(__file__).resolve().parents[1]


def _pk(*ints):
    return b"".join(struct.pack(">i", x) for x in ints)


def _msg(*packets):
    out = struct.pack(">H", len(packets))
    for p in packets:
        out += struct.pack(">H", len(p)) + p
    return out


def test_binary_ticks_full_quote_index_and_ltp():
    tok_opt = (43919 << 8) | 2                                     # NFO segment 2 -> paise
    depth = b"".join(struct.pack(">iih", q, px, n) + b"\0\0" for q, px, n in
                     [(150, 10050, 3)] + [(0, 0, 0)] * 4 + [(75, 10100, 2)] + [(0, 0, 0)] * 4)
    full = _pk(tok_opt, 10075, 75, 10060, 123450, 9000, 8000, 9900, 10200, 9800, 9950,
               1794300000, 555000, 600000, 500000, 1794300001) + depth
    assert len(full) == 184
    quote = _pk((2885 << 8) | 1, 250050, 10, 249900, 1000000, 5000, 6000, 248000, 251000, 247000, 248500)
    idx = _pk(256265, 2500000, 2510000, 2490000, 2495000, 2480000, 20000, 1794300002)
    ltp = _pk((5 << 8) | 1, 12345)
    t = K.parse_binary(_msg(full, quote, idx, ltp))
    f, q, i, l = t
    assert f["token"] == tok_opt and f["ltp"] == 100.75 and f["ltq"] == 75 and f["vol"] == 123450 and f["oi"] == 555000
    assert f["bid"] == 100.5 and f["bidq"] == 150 and f["ask"] == 101.0 and f["ltt"] == 1794300000 and f["cp"] == 99.5
    assert q["ltp"] == 2500.5 and q["day_h"] == 2510.0 and "ltt" not in q
    assert i["ltp"] == 25000.0 and i["day_h"] == 25100.0 and i["day_o"] == 24950.0 and i["cp"] == 24800.0 and i["ts"] == 1794300002
    assert l == {"token": (5 << 8) | 1, "ltp": 123.45}
    tick = K.to_tick(f, "NSE_FO|43919")
    assert tick["key"] == "NSE_FO|43919" and tick["ltt"] == 1794300000000 and tick["vol"] == 123450 and "token" not in tick
    assert K.to_tick(i, "NSE_INDEX|Nifty 50")["ltt"] == 1794300002000            # index: exchange time
    assert K.parse_binary(b"\x00") == []


def test_login_checksum_url_and_token_expiry(tmp_path):
    assert K.checksum("k", "r", "s") == hashlib.sha256(b"krs").hexdigest()
    assert K.login_url("abc") == "https://kite.zerodha.com/connect/login?v=3&api_key=abc"
    assert K.token_expiry(dt.datetime(2026, 11, 11, 10, 0, tzinfo=IST)).isoformat().startswith("2026-11-12T06:00")
    assert K.token_expiry(dt.datetime(2026, 11, 11, 5, 0, tzinfo=IST)).isoformat().startswith("2026-11-11T06:00")
    st = K.KiteTokenStore(tmp_path / "kite_token.json")
    st.save({"access_token": "x", "user_id": "AB1234"})
    assert st.load()["user"] == "AB1234" and oct((tmp_path / "kite_token.json").stat().st_mode)[-3:] == "600"
    st.clear()
    assert st.load() is None


CSV = """instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,instrument_type,segment,exchange
256265,1001,NIFTY 50,NIFTY 50,0,,0,0,0,EQ,INDICES,NSE
260105,1016,NIFTY BANK,NIFTY BANK,0,,0,0,0,EQ,INDICES,NSE
264969,1035,INDIA VIX,INDIA VIX,0,,0,0,0,EQ,INDICES,NSE
265,1,SENSEX,SENSEX,0,,0,0,0,EQ,INDICES,BSE
738561,2885,RELIANCE,RELIANCE INDUSTRIES,0,,0,0.1,1,EQ,NSE,NSE
11243266,43919,NIFTY25NOV25000CE,NIFTY,0,2026-11-25,25000,0.05,75,CE,NFO-OPT,NFO
9999,1,GOLD,GOLD,0,,0,1,1,FUT,MCX-FUT,MCX
"""


def test_map_upstox_keys_to_kite_tokens():
    idx = K.index_csv(CSV)
    assert ("MCX", "1") not in idx[0]                                # only NSE / NFO / BSE / BFO kept
    keys = ["NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty Bank", "NSE_INDEX|India VIX", "BSE_INDEX|SENSEX",
            "NSE_EQ|INE002A01018", "NSE_FO|43919", "NSE_FO|99999", "NSE_EQ|UNKNOWN"]
    m = K.build_map(keys, {"NSE_EQ|INE002A01018": "2885", "NSE_FO|43919": "43919"}, idx, F.cfg()["index_map"])
    assert m == {"NSE_INDEX|Nifty 50": 256265, "NSE_INDEX|Nifty Bank": 260105, "NSE_INDEX|India VIX": 264969,
                 "BSE_INDEX|SENSEX": 265, "NSE_EQ|INE002A01018": 738561, "NSE_FO|43919": 11243266}


class FakeFeed:
    def __init__(self):
        self.on_ticks, self.subs, self.connected, self.msgs, self.last_msg, self.tokens = None, {"full": set()}, True, 0, 0.0, set()
        self.started = self.stopped = False
        self.error = None

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def subscribe(self, keys, mode="full"):
        if isinstance(next(iter(keys), None), int):
            self.tokens.update(keys)
        else:
            self.subs.setdefault(mode, set()).update(keys)

    def unsubscribe(self, keys):
        for k in keys:
            self.tokens.discard(k)
            for v in self.subs.values():
                v.discard(k)

    def counts(self):
        return {m: len(v) for m, v in self.subs.items()}


class Inst:
    def __init__(self, ltp, ltt):
        self.ltp, self.ltt = ltp, ltt


def _router(mode="shadow", state=None, factory=None, open_=True):
    got, up, kite = [], FakeFeed(), FakeFeed()
    mapping = {"NSE_INDEX|Nifty 50": 256265, "NSE_FO|43919": 11243266}
    r = F.FeedRouter(up, got.extend, log=lambda m: None,
                     backup_factory=factory or (lambda on_t, on_s: (kite, mapping)),
                     state_get=(state or {}).get, is_open=lambda: open_, mode_fn=lambda: mode,
                     by_key={"NSE_FO|43919": ("OPT", "NIFTY", None)})
    return r, up, kite, got


def test_router_mirrors_subscriptions_and_keeps_kite_in_shadow():
    r, up, kite, got = _router()
    r.subscribe(["NSE_INDEX|Nifty 50"], "full")
    r._start_backup()
    assert kite.started and kite.tokens == {256265} and r.backup_status == "running"
    r.subscribe(["NSE_FO|43919", "NSE_EQ|NOT_IN_KITE"], "option_greeks")
    assert kite.tokens == {256265, 11243266} and up.subs["option_greeks"] == {"NSE_FO|43919", "NSE_EQ|NOT_IN_KITE"}
    r.unsubscribe(["NSE_FO|43919"])
    assert kite.tokens == {256265}
    up.on_ticks([{"key": "NSE_INDEX|Nifty 50", "ltp": 25000.0}])
    r._on_backup([{"token": 256265, "ltp": 25001.0, "ts": 1794300000}])
    assert got == [{"key": "NSE_INDEX|Nifty 50", "ltp": 25000.0}]                 # only Upstox reaches the app
    assert r.kite_last["NSE_INDEX|Nifty 50"][0] == 25001.0
    assert r.view()["backup"]["status"] == "running" and r.view()["failover"] == "shadow"


def test_failover_on_uses_kite_while_upstox_is_quiet_and_goes_back():
    r, up, kite, got = _router(mode="on")
    r._start_backup()
    now = time.time()
    r.primary_last = now - 20
    kite.last_msg = now
    r.tick_failover(now)
    assert r.active == "kite" and r.switches == 1
    r._on_backup([{"token": 256265, "ltp": 25010.0, "ts": 1794300000}])
    assert got[-1]["ltp"] == 25010.0 and got[-1]["key"] == "NSE_INDEX|Nifty 50"   # Kite feeds the app now
    up.on_ticks([{"key": "NSE_INDEX|Nifty 50", "ltp": 25011.0}])                  # Upstox is back
    assert r.active == "upstox" and r.covered_s >= 8 and got[-1]["ltp"] == 25011.0
    r._on_backup([{"token": 256265, "ltp": 25012.0, "ts": 1794300001}])
    assert got[-1]["ltp"] == 25011.0                                              # Kite is a backup again


def test_failover_shadow_only_records_and_closed_market_never_switches():
    r, up, kite, got = _router(mode="shadow")
    r._start_backup()
    now = time.time()
    r.primary_last, kite.last_msg = now - 20, now
    r.tick_failover(now)
    assert r.active == "upstox" and len(r.would) == 1 and r.would[0]["secs"] is None
    r.primary_last = now + 1
    r.tick_failover(now + 2)
    assert r.would[0]["secs"] >= 8
    r2, up2, kite2, _ = _router(mode="on", open_=False)
    r2._start_backup()
    r2.primary_last, kite2.last_msg = now - 600, now
    r2.tick_failover(now)
    assert r2.active == "upstox" and r2.switches == 0                               # outside market hours: never


def test_cross_check_counts_matches_mismatches_and_who_is_behind():
    t0 = 1794300000000
    state = {"NSE_INDEX|Nifty 50": Inst(25000.0, t0), "NSE_FO|43919": Inst(100.0, t0)}
    r, up, kite, got = _router(state=state)
    r._start_backup()
    r.kite_last = {"NSE_INDEX|Nifty 50": (25005.0, t0 + 1000, 0), "NSE_FO|43919": (110.0, t0, 0)}
    c = r.cross_check()
    assert c["compared"] == 2 and c["match"] == 1 and c["mismatch"] == 1 and c["examples"][0]["what"] == "price differs"
    r.kite_last = {"NSE_INDEX|Nifty 50": (25005.0, t0 + 60000, 0)}                 # Kite traded a minute later
    c = r.cross_check()
    assert c["upstox_behind"] == 1 and c["compared"] == 0


def test_a_missing_or_broken_backup_never_touches_upstox():
    def not_ready(on_t, on_s):
        raise F.BackupNotReady("login_needed", "Log in with Kite")
    r, up, kite, got = _router(factory=not_ready)
    r._start_backup()
    assert r.backup is None and r.backup_status == "login_needed"
    r.subscribe(["NSE_INDEX|Nifty 50"], "full")
    up.on_ticks([{"key": "NSE_INDEX|Nifty 50", "ltp": 1.0}])
    assert got and up.subs["full"] == {"NSE_INDEX|Nifty 50"}

    def boom(on_t, on_s):
        raise RuntimeError("network down")
    r2, up2, _, got2 = _router(factory=boom)
    r2._start_backup()
    assert r2.backup_status == "error" and "network down" in r2.backup_msg
    r2.tick_failover(time.time())
    r2.cross_check()
    r2.stop()
    assert up2.stopped


def test_kite_code_reads_prices_only():
    for f in ("live/kite.py", "live/feeds.py"):
        s = (ROOT / f).read_text()
        for bad in ("/orders", "place_order", "modify_order", "cancel_order", "order/place", "/gtt", "/portfolio"):
            assert bad not in s, (f, bad)
    s = (ROOT / "live" / "kite.py").read_text()
    sends = set(__import__("re").findall(r'"a": "?(\w+)', s))
    assert sends <= {"action", "mode"}                                             # subscribe / unsubscribe / mode only
    for p in list((ROOT / "live").glob("*.py")) + list((ROOT / "webapp").glob("*.py")) + list((ROOT / "lab").glob("*.py")):
        if p.name != "kite.py":
            t = p.read_text()
            assert "ws.kite.trade" not in t and "api.kite.trade" not in t, p.name   # one Kite connection point
    ign = (ROOT / ".gitignore").read_text().split()
    assert "data/kite_token.json" in ign and "data/upstox_token.json" in ign and "user_settings.json" in ign


def test_engine_builds_the_real_router_with_the_upstox_feed():
    """2.56.1: the router must build with the real Upstox feed class (11 Nov 2026: NameError on the Mac at 09:24,
    the scanner could not start; the simulator path never built the router)."""
    from live.engine import LiveEngine
    from live.upstox import UpstoxFeed
    e = object.__new__(LiveEngine)
    e._on_ticks = lambda ticks: None
    e._feed_status = lambda *a, **k: None
    e.log = lambda m: None
    e.state, e.uni = None, None
    e.cfg = lambda: {"live": {}}
    from live.feedproc import ProcFeed
    r = e._make_router({"access_token": "not-a-real-token"})
    assert isinstance(r, F.FeedRouter) and isinstance(r.primary, ProcFeed)          # 2.71: the feed in its own process
    assert r.mode() in ("shadow", "on", "off") and r.is_open() in (True, False)
    e.cfg = lambda: {"live": {"feed_reader": "thread"}}                             # the old in-process reader
    assert isinstance(e._make_router({"access_token": "not-a-real-token"}).primary, UpstoxFeed)
