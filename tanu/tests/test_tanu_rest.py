"""Upstox REST (market data only): retries, login errors, the URL guard, rate limits. A fake transport, no network."""
import json

import pytest

from tanu.brokers.upstox import rest as R
from tanu.core import safety


def _client(responses, calls):
    def transport(url, headers, timeout):
        calls.append((url, headers["Authorization"]))
        return responses.pop(0)
    return R.UpstoxRest("tok", transport=transport, sleep=lambda s: None)


def test_retries_server_errors_then_reads_candles():
    calls = []
    body = json.dumps({"status": "success", "data": {"candles": [["2026-11-13T09:16:00+05:30", 2, 3, 1, 2.5, 10, 0],
                                                                  ["2026-11-13T09:15:00+05:30", 1, 2, 1, 1.5, 5, 0]]}})
    c = _client([(500, b""), (429, b""), (200, body.encode())], calls)
    rows = c.intraday_1m("NSE_INDEX|Nifty 50")
    assert [r[0][11:16] for r in rows] == ["09:15", "09:16"] and len(calls) == 3
    assert calls[0][0].endswith("/v3/historical-candle/intraday/NSE_INDEX%7CNifty%2050/minutes/1")


def test_login_errors_stop_at_once_and_bad_status_raises():
    with pytest.raises(PermissionError):
        _client([(401, b"")], []).get("/v2/market-quote/ltp")
    with pytest.raises(RuntimeError):
        _client([(400, b"")], []).get("/v2/market-quote/ltp")


def test_trading_paths_never_leave_the_machine():
    calls = []
    c = _client([(200, b"{}")], calls)
    with pytest.raises(safety.BlockedCall):
        c.get("/v2/order/place")
    assert calls == []


def test_rate_limiter_waits_inside_upstox_limits():
    now, slept = [1000.0], []
    lim = R.RateLimiter(sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)), clock=lambda: now[0])
    for _ in range(20):
        lim.wait()
    assert slept == []
    lim.wait()                                           # the 21st call in the same second waits
    assert slept and 0.9 < slept[0] <= 1.1
