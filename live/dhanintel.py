"""Dhan Pro market intelligence (Data API, read-only): live news headlines, market movers and technical values.

- News headlines (POST /v2/data/newsheadline): latest 50, merged into the app's news feed (same symbol matching
  and tone), so news alerts and the "news with / against" fact get them within a minute.
- Market movers (POST /v2/data/marketmovers): stock options / futures by OI gainers and volume, F&O stocks by price,
  every minute. A stock in these lists is "in play"; its rank is a fact for the conviction model and the scanner.
- Technical data (POST /v2/data/technical) is deliberately not used for signals: it has no history, so rules built on it
  can't be backtested; the Lab computes the same indicators itself, identically live and in the backtest.

Nothing here orders anything or reads the trading account. The universe is never "PORTFOLIO".
"""
from __future__ import annotations

import csv
import datetime as dt
import time
from pathlib import Path

MOVER_LISTS = [  # (key, exchangeSegment, instruments, category, needs expiry, universe)
    ("opt_oi_gainers", "NSE_FNO", ["OPTSTK"], "OI_GAINERS", True, None),
    ("opt_volume", "NSE_FNO", ["OPTSTK"], "TOP_VOLUME", True, None),
    ("fut_oi_gainers", "NSE_FNO", ["FUTSTK"], "OI_GAINERS", True, None),
    ("price_gainers", "NSE_EQ", ["EQUITY"], "PRICE_GAINERS", False, "FNO_STOCKS"),
    ("price_losers", "NSE_EQ", ["EQUITY"], "PRICE_LOSERS", False, "FNO_STOCKS"),
]


def _rows(res) -> list:
    """The list of records in a Dhan response, whatever it is wrapped in."""
    if isinstance(res, list):
        return [x for x in res if isinstance(x, dict)]
    if isinstance(res, dict):
        for k in ("data", "news", "items", "result", "movers", "list"):
            v = res.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
            if isinstance(v, dict):
                r = _rows(v)
                if r:
                    return r
    return []


def _pick(d: dict, *keys):
    low = {k.lower(): v for k, v in d.items()}
    for k in keys:
        v = low.get(k.lower())
        if v not in (None, ""):
            return v
    return None


def id_map(cache_dir) -> dict:
    """Dhan security id -> underlying symbol, for equities and F&O contracts (from the cached scrip master)."""
    fs = sorted(Path(cache_dir).glob("dhan_scrips_*.csv"))
    if not fs:
        from lab.dhan_hist import stock_ids
        stock_ids(cache_dir, [])
        fs = sorted(Path(cache_dir).glob("dhan_scrips_*.csv"))
    out = {}
    with open(fs[-1], newline="", encoding="utf-8", errors="ignore") as fh:
        for row in csv.DictReader(fh):
            if row.get("SEM_EXM_EXCH_ID") != "NSE":
                continue
            inst = row.get("SEM_INSTRUMENT_NAME")
            sym = row.get("SEM_TRADING_SYMBOL") or ""
            if inst == "EQUITY" or inst in ("OPTSTK", "FUTSTK", "OPTIDX", "FUTIDX"):
                try:
                    out[str(int(float(row["SEM_SMST_SECURITY_ID"])))] = sym.split("-")[0]
                except (ValueError, KeyError):
                    pass
    return out


class DhanIntel:
    def __init__(self, dhan, cache_dir, log=print):
        self.d, self.cache_dir, self.log = dhan, Path(cache_dir), log
        self.ids = {}
        self.movers: dict = {}            # list key -> [symbol, ...] in rank order
        self.movers_at = 0.0
        self.errors: dict = {}

    def _ids(self):
        if not self.ids:
            try:
                self.ids = id_map(self.cache_dir)
            except Exception as e:
                self.errors["scrips"] = str(e)[:120]
        return self.ids

    # ---------------- news
    def news(self) -> list:
        """[{title, link, source, ts, dhan_syms}] newest first (Dhan headlines)."""
        try:
            res = self.d.post("/data/newsheadline", {"dhanClientId": self.d.client_id, "categories": ["ALL"], "limit": 50})
            self.errors.pop("news", None)
        except PermissionError:
            raise
        except Exception as e:
            self.errors["news"] = str(e)[:160]
            return []
        out = []
        ids = self._ids()
        for r in _rows(res):
            title = _pick(r, "headline", "title", "newsHeadline", "heading")
            if not title:
                continue
            ts = _pick(r, "timestamp", "publishedAt", "publishTime", "time", "date", "newsDate")
            try:
                ts = float(ts) / (1000 if float(ts) > 1e11 else 1)
            except (TypeError, ValueError):
                try:
                    ts = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
                except Exception:
                    ts = time.time()
            sids = _pick(r, "securityIds", "stockList", "securityId", "stocks") or []
            sids = sids if isinstance(sids, list) else [sids]
            syms = sorted({ids.get(str(x.get("securityId") if isinstance(x, dict) else x)) for x in sids} - {None})
            out.append({"title": str(title).strip(), "link": _pick(r, "url", "link", "sourceUrl") or "", "source": "Dhan",
                        "ts": ts, "dhan_syms": syms})
        return out

    # ---------------- market movers
    def refresh_movers(self, expiry: dt.date | None):
        ids = self._ids()
        out = {}
        for key, seg, inst, cat, needs_exp, uni in MOVER_LISTS:
            body = {"exchangeSegment": seg, "instrument": inst, "category": cat, "limit": 50}
            if needs_exp:
                if not expiry:
                    continue
                body["expiry"] = expiry.isoformat()
            if uni:
                body["universe"] = uni
            try:
                res = self.d.post("/data/marketmovers", body)
            except PermissionError:
                raise
            except Exception as e:
                self.errors["movers"] = str(e)[:160]
                continue
            syms = []
            for r in _rows(res):
                s = _pick(r, "underlyingSymbol", "symbol", "tradingSymbol", "displayName")
                if s:
                    s = str(s).split("-")[0].split(" ")[0].upper()
                else:
                    s = ids.get(str(_pick(r, "securityId", "secId")))
                if s and s not in syms:
                    syms.append(s)
            out[key] = syms
        if out:
            self.movers, self.movers_at = out, time.time()
            self.errors.pop("movers", None)

    def mover_rank(self, sym: str) -> dict:
        """Best (lowest) rank of a symbol across the mover lists, and which lists it is in."""
        if time.time() - self.movers_at > 600:
            return {}
        lists = {k: v.index(sym) + 1 for k, v in self.movers.items() if sym in v}
        return {"rank": min(lists.values()), "lists": sorted(lists)} if lists else {"rank": None, "lists": []}
