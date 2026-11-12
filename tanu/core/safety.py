"""The hard rule, enforced in code: market data only.

Mandeep: "algo trade means real trade nahi lena ... real money traders ya real trading account mein kuch nahi karna".
No order, modify or cancel call to any broker; no funds, positions, holdings, margins or portfolio; no GTT. Every
Tanu HTTP or websocket client must pass each URL through check_url() before sending, so a mistake fails here,
before it reaches a broker. tanu/tests/test_tanu_safety.py also scans the source for such calls.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# words that belong to trading or account access anywhere in the path (Upstox, Dhan, Kite, Angel and similar APIs),
# plus whole path pieces that are too short to match as parts of words
_BLOCKED = re.compile(
    r"(order|portfolio|fund|margin|holding|position|basket|killswitch|edis|tpin|brokerage|charges)"
    r"|(^|/)(trades?|gtt|convert|exit-?all)(/|$)",
    re.IGNORECASE)


class BlockedCall(RuntimeError):
    """Raised when code tries to reach a trading or account endpoint."""


def is_blocked(url: str) -> bool:
    path = urlparse(url).path or url
    return bool(_BLOCKED.search(path))


def check_url(url: str) -> str:
    if is_blocked(url):
        raise BlockedCall(f"Blocked: {url} is a trading / account endpoint. Tanu uses market data only.")
    return url
