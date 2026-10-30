"""F&O universe from Upstox's public instrument file (refreshed daily ~6 AM)."""
from __future__ import annotations

import datetime as dt
import gzip
import json
from dataclasses import dataclass, field
from pathlib import Path

from .clock import IST

INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"

# Index symbols in the F&O file -> display name; India VIX is tracked as an extra
INDEX_NAMES = {
    "NIFTY": "Nifty 50",
    "BANKNIFTY": "Bank Nifty",
    "FINNIFTY": "Fin Nifty",
    "MIDCPNIFTY": "Midcap Select",
    "NIFTYNXT50": "Nifty Next 50",
}
VIX_KEY = "NSE_INDEX|India VIX"
SENSEX_KEY = "BSE_INDEX|SENSEX"


@dataclass
class Option:
    key: str
    symbol: str
    kind: str          # CE / PE
    strike: float
    expiry: dt.date
    lot: int


@dataclass
class Underlying:
    symbol: str
    name: str
    key: str                       # NSE_EQ|... or NSE_INDEX|...
    is_index: bool
    lot: int
    fut_key: str | None = None
    fut_expiry: dt.date | None = None
    expiries: list = field(default_factory=list)          # option expiries, sorted
    chains: dict = field(default_factory=dict)            # expiry -> {strike: {"CE": Option, "PE": Option}}

    def near_expiry(self, today: dt.date) -> dt.date | None:
        for e in self.expiries:
            if e >= today:
                return e
        return None

    def strikes(self, expiry) -> list:
        return sorted(self.chains.get(expiry, {}))


@dataclass
class Universe:
    underlyings: dict                 # symbol -> Underlying
    by_key: dict                      # instrument key -> ("UND"|"FUT"|"OPT", symbol, Option|None)
    vix_key: str = VIX_KEY
    built: str = ""

    @property
    def indices(self):
        return [u for u in self.underlyings.values() if u.is_index]

    @property
    def stocks(self):
        return [u for u in self.underlyings.values() if not u.is_index]


def _date(ms) -> dt.date:
    return dt.datetime.fromtimestamp(ms / 1000, IST).date()


def build(rows: list, today: dt.date | None = None) -> Universe:
    today = today or dt.datetime.now(IST).date()
    names = {r["instrument_key"]: (r.get("short_name") or r.get("name") or r.get("trading_symbol"))
             for r in rows if r.get("segment") in ("NSE_EQ", "NSE_INDEX")}
    fo = [r for r in rows if r.get("segment") == "NSE_FO"]
    unds: dict[str, Underlying] = {}
    futs: dict[str, list] = {}
    for r in fo:
        sym = r.get("underlying_symbol")
        if not sym:
            continue
        if sym not in unds:
            is_index = r.get("underlying_type") == "INDEX"
            unds[sym] = Underlying(symbol=sym, name=INDEX_NAMES.get(sym) or names.get(r["underlying_key"], sym),
                                   key=r["underlying_key"], is_index=is_index, lot=int(r.get("lot_size") or 1))
        if r.get("instrument_type") == "FUT":
            futs.setdefault(sym, []).append((_date(r["expiry"]), r["instrument_key"]))
    by_key = {}
    for r in fo:
        if r.get("instrument_type") not in ("CE", "PE"):
            continue
        u = unds.get(r.get("underlying_symbol"))
        if not u:
            continue
        exp = _date(r["expiry"])
        if exp < today:
            continue
        o = Option(key=r["instrument_key"], symbol=u.symbol, kind=r["instrument_type"],
                   strike=float(r["strike_price"]), expiry=exp, lot=int(r.get("lot_size") or u.lot))
        u.chains.setdefault(exp, {}).setdefault(o.strike, {})[o.kind] = o
        by_key[o.key] = ("OPT", u.symbol, o)
    for sym, u in unds.items():
        u.expiries = sorted(u.chains)
        live = sorted(x for x in futs.get(sym, []) if x[0] >= today)
        if live:
            u.fut_expiry, u.fut_key = live[0]
            by_key[u.fut_key] = ("FUT", sym, None)
        by_key[u.key] = ("UND", sym, None)
    by_key[VIX_KEY] = ("VIX", "INDIAVIX", None)
    # keep only underlyings that actually have options
    unds = {s: u for s, u in unds.items() if u.expiries}
    return Universe(underlyings=unds, by_key=by_key, built=today.isoformat())


def load(cache_dir: str, fetch=True, log=print) -> Universe:
    """Download today's instrument file once a day (cached)."""
    d = Path(cache_dir) / "instruments"
    d.mkdir(parents=True, exist_ok=True)
    today = dt.datetime.now(IST).date()
    f = d / f"NSE_{today.isoformat()}.json.gz"
    if not f.exists() and fetch:
        import requests
        log("Downloading today's instrument list from Upstox...")
        r = requests.get(INSTRUMENTS_URL, timeout=120)
        r.raise_for_status()
        f.write_bytes(r.content)
        for old in d.glob("NSE_*.json.gz"):
            if old != f:
                old.unlink(missing_ok=True)
    if not f.exists():
        olds = sorted(d.glob("NSE_*.json.gz"))
        if not olds:
            raise RuntimeError("No instrument list yet. Connect to the internet and try again.")
        f = olds[-1]
    rows = json.load(gzip.open(f))
    uni = build(rows, today)
    log(f"Universe: {len(uni.indices)} indices, {len(uni.stocks)} F&O stocks")
    return uni


def strike_step(strikes: list, spot: float) -> float:
    if len(strikes) < 2:
        return 1.0
    near = sorted(strikes, key=lambda k: abs(k - spot))[:6]
    near.sort()
    gaps = [b - a for a, b in zip(near, near[1:]) if b > a]
    return min(gaps) if gaps else 1.0


def atm_window(strikes: list, spot: float, n: int) -> list:
    """n strikes each side of the strike nearest to spot."""
    if not strikes:
        return []
    s = sorted(strikes)
    i = min(range(len(s)), key=lambda j: abs(s[j] - spot))
    return s[max(0, i - n): i + n + 1]
