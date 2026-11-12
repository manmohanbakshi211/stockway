"""Market data only: the URL guard blocks trading / account endpoints, and Tanu's source has no order calls."""
import re
from pathlib import Path

import pytest

from tanu.core import safety

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("url", [
    "https://api.upstox.com/v2/order/place", "https://api.upstox.com/v3/order/modify", "https://api.upstox.com/v2/order/cancel",
    "https://api.upstox.com/v2/portfolio/long-term-holdings", "https://api.upstox.com/v2/portfolio/short-term-positions",
    "https://api.upstox.com/v2/user/get-funds-and-margin", "https://api.dhan.co/v2/orders", "https://api.dhan.co/v2/positions",
    "https://api.dhan.co/v2/fundlimit/funds", "https://api.kite.trade/orders/regular", "https://api.kite.trade/gtt/triggers",
    "https://api.kite.trade/portfolio/holdings", "https://api.upstox.com/v2/order/trades/get-trades-for-day"])
def test_trading_and_account_urls_are_blocked(url):
    assert safety.is_blocked(url)
    with pytest.raises(safety.BlockedCall):
        safety.check_url(url)


@pytest.mark.parametrize("url", [
    "https://api.upstox.com/v3/historical-candle/NSE_INDEX%7CNifty%2050/minutes/1/2026-11-12/2026-11-01",
    "https://api.upstox.com/v2/market-quote/quotes?instrument_key=NSE_INDEX|Nifty 50",
    "https://api.upstox.com/v2/option/chain?instrument_key=NSE_INDEX|Nifty 50&expiry_date=2026-11-17",
    "https://api.upstox.com/v3/feed/market-data-feed/authorize", "https://api.dhan.co/v2/charts/intraday"])
def test_market_data_urls_pass(url):
    assert safety.check_url(url) == url


def test_no_order_calls_in_tanu_source():
    words = ["place" + "_order", "modify" + "_order", "cancel" + "_order", "/order" + "/", "place" + "Order"]
    pat = re.compile("|".join(re.escape(w) for w in words))
    hits = []
    for f in (ROOT / "tanu").rglob("*.py"):
        if "tests" in f.parts:
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if pat.search(line):
                hits.append(f"{f.relative_to(ROOT)}:{i}")
    assert not hits, hits
