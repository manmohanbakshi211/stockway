"""The broker's instrument list (Upstox's public NSE file, refreshed daily): lot sizes, tick sizes, option keys and
expiries per underlying. Public file, no login. Live lot sizes always come from here (tanu/facts/lots.json is only for
history)."""
from __future__ import annotations

import gzip
import json
import urllib.request
from pathlib import Path

from ..core import safety

UPSTOX_NSE = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"


def download(dest: Path | str, url: str = UPSTOX_NSE, timeout: float = 60) -> Path:
    safety.check_url(url)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = r.read()
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return dest


def load(path: Path | str) -> list[dict]:
    p = Path(path)
    op = gzip.open if p.suffix == ".gz" else open
    with op(p, "rt") as f:
        return json.load(f)


def underlyings(rows: list[dict]) -> dict[str, dict]:
    """{symbol: {"key", "is_index", "lot", "tick", "expiries": sorted ISO dates}} for every F&O underlying."""
    import datetime as dt
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    out: dict = {}
    for r in rows:
        if r.get("segment") != "NSE_FO" or r.get("instrument_type") not in ("CE", "PE"):
            continue
        s = r.get("underlying_symbol") or r.get("name")
        u = out.setdefault(s, {"key": r.get("underlying_key"), "is_index": r.get("underlying_type") == "INDEX",
                               "lot": int(r.get("lot_size") or 0), "tick": (r.get("tick_size") or 5) / 100,
                               "expiries": set()})
        if r.get("expiry"):
            u["expiries"].add(dt.datetime.fromtimestamp(r["expiry"] / 1000, ist).date().isoformat())
    for u in out.values():
        u["expiries"] = sorted(u["expiries"])
    return out
