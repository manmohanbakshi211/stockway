"""World monitor: global indices, currencies, commodities, rates, crypto, Indian ADRs, economic and results
calendars, and global news with impact tags. All free sources (Yahoo Finance via yfinance, ForexFactory, NSE, RSS).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from pathlib import Path

import requests

from .clock import IST
from .notify import News

ASSETS = {
    "Global indices": [("S&P 500 futures", "ES=F", "us"), ("Nasdaq 100 futures", "NQ=F", "us"), ("Dow futures", "YM=F", "us"),
                       ("FTSE 100", "^FTSE", "eu"), ("DAX", "^GDAXI", "eu"), ("Nikkei 225", "^N225", "asia"),
                       ("Hang Seng", "^HSI", "asia"), ("Shanghai", "000001.SS", "asia"), ("Kospi", "^KS11", "asia"),
                       ("Taiwan", "^TWII", "asia"),
                       # 2.29: the cash indices behind the futures, and the rest of Europe / Asia-Pacific that trade
                       # before or with the Indian session
                       ("Dow Jones", "^DJI", "us"), ("Nasdaq Composite", "^IXIC", "us"), ("S&P 500", "^GSPC", "us"),
                       ("CAC 40", "^FCHI", "eu"), ("Euro Stoxx 50", "^STOXX50E", "eu"), ("ASX 200", "^AXJO", "asia"),
                       ("Straits Times", "^STI", "asia")],
    "Currencies": [("Dollar index", "DX-Y.NYB", "us"), ("USD/INR", "INR=X", "in"), ("EUR/USD", "EURUSD=X", "eu"),
                   ("USD/JPY", "JPY=X", "asia"), ("USD/CNH", "CNH=X", "asia"), ("GBP/USD", "GBPUSD=X", "eu")],
    "Commodities": [("Brent crude", "BZ=F", "us"), ("WTI crude", "CL=F", "us"), ("Gold", "GC=F", "us"), ("Silver", "SI=F", "us"),
                    ("Copper", "HG=F", "us"), ("Natural gas", "NG=F", "us")],
    "Rates & fear": [("US 10Y yield", "^TNX", "us"), ("US 5Y yield", "^FVX", "us"), ("US 3M bill", "^IRX", "us"),
                     ("US VIX", "^VIX", "us")],
    "Crypto (risk mood)": [("Bitcoin", "BTC-USD", "us"), ("Ethereum", "ETH-USD", "us")],
    "Indian indices (other)": [("Sensex", "^BSESN", "local"), ("Nifty 50", "^NSEI", "local"), ("Bank Nifty", "^NSEBANK", "local"),
                               ("Fin Nifty", "NIFTY_FIN_SERVICE.NS", "local"), ("Midcap Select", "NIFTY_MID_SELECT.NS", "local"),
                               ("India VIX", "^INDIAVIX", "local")],
    "Indian ADRs (New York)": [("Infosys ADR", "INFY", "us"), ("HDFC Bank ADR", "HDB", "us"), ("ICICI Bank ADR", "IBN", "us"),
                               ("Wipro ADR", "WIT", "us")],
}
ADR = {"INFY": ("INFY.NS", 1), "HDB": ("HDFCBANK.NS", 3), "IBN": ("ICICIBANK.NS", 2), "WIT": ("WIPRO.NS", 1)}
NIFTY = "^NSEI"

# moves big enough to flag as a macro alert (absolute % change on the day)
ALERT_MOVES = {"ES=F": 1.0, "NQ=F": 1.3, "BZ=F": 2.5, "CL=F": 2.5, "GC=F": 1.5, "DX-Y.NYB": 0.6, "INR=X": 0.4,
               "^VIX": 10.0, "BTC-USD": 5.0, "^HSI": 2.0, "^N225": 2.0, "000001.SS": 2.0, "^TNX": 3.0, "JPY=X": 1.0}

GLOBAL_FEEDS = {
    "Investing.com (markets)": "https://www.investing.com/rss/news_25.rss",
    "Investing.com (forex)": "https://www.investing.com/rss/news_1.rss",
    "Investing.com (commodities)": "https://www.investing.com/rss/news_11.rss",
    "Investing.com (economy)": "https://www.investing.com/rss/news_14.rss",
    "MarketWatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "Mint economy": "https://www.livemint.com/rss/economy",
    "ET economy": "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms",
}
HIGH = re.compile(r"\b(fed|fomc|powell|rate (cut|hike)|interest rate|inflation|cpi|pce|payrolls?|jobs report|gdp|recession|rbi|"
                  r"repo rate|war|missile|sanction|tariff|opec|crude|oil prices?|default|crash|plunge|surge|record high|"
                  r"stimulus|yuan|devaluation|rupee|bond yields?|treasury|ecb|boj|bank of japan|china)\b", re.I)
INDIA = re.compile(r"\b(india|indian|nifty|sensex|rupee|rbi|sebi|nse|bse|dalal street|modi|fpi|fii)\b", re.I)
NEG = re.compile(r"\b(fall|falls|drop|slump|plunge|crash|tumble|slide|war|sanction|tariff|recession|default|hike|selloff|sell-off|weak)\b", re.I)
POS = re.compile(r"\b(rise|rises|gain|jump|surge|rally|record high|cut|stimulus|deal|truce|eases?|strong|beat)\b", re.I)


# the app's index symbols on Yahoo (for all-time / 52-week highs and lows, and prices when the scanner is off)
YAHOO_INDEX = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
               "MIDCPNIFTY": "NIFTY_MID_SELECT.NS", "SENSEX": "^BSESN"}
# what the Live page shows under "World markets", in this order (name shown, ticker)
LIVE_BOARD = [
    ("US", [("Dow Jones", "^DJI"), ("Nasdaq", "^IXIC"), ("S&P 500", "^GSPC"), ("Dow fut", "YM=F"), ("Nasdaq 100 fut", "NQ=F"),
            ("S&P 500 fut", "ES=F"), ("US VIX", "^VIX")]),
    ("Asia", [("GIFT Nifty", "_GIFT"), ("Nikkei 225", "^N225"), ("Hang Seng", "^HSI"), ("Shanghai", "000001.SS"),
              ("Kospi", "^KS11"), ("Taiwan", "^TWII"), ("ASX 200", "^AXJO"), ("Straits Times", "^STI")]),
    ("Europe", [("FTSE 100", "^FTSE"), ("DAX", "^GDAXI"), ("CAC 40", "^FCHI"), ("Euro Stoxx 50", "^STOXX50E")]),
    ("Money & commodities", [("USD/INR", "INR=X"), ("Dollar index", "DX-Y.NYB"), ("US 10Y yield", "^TNX"), ("Brent crude", "BZ=F"),
                             ("Gold", "GC=F"), ("Silver", "SI=F"), ("Bitcoin", "BTC-USD")]),
]


def _pct(a, b):
    return (a / b - 1) * 100 if a and b else None


class World:
    def __init__(self, data_dir, log=print, ai=None):
        self.data_dir = Path(data_dir)
        self.log = log
        self.ai = ai
        self.rows = {}            # ticker -> row
        self.corr = {}            # ticker -> {"same": r, "next": r, "beta_next": b, "beta_same": b}
        self.adr = []
        self.calendar = []
        self.results = []
        self.news = News(log, GLOBAL_FEEDS)
        self.tags = {}            # headline -> {"impact","direction","why"}
        self.extremes = {}        # ticker -> all-time / 52-week high and low
        self.errors = {}
        self.updated = {}
        self.on_macro = None      # callback(alert dict)
        self.flagged = {}         # (ticker, day) -> level already alerted
        self.event_flagged = set()
        self._load()

    # ------------------------------------------------------------ cache
    def _cache(self):
        return self.data_dir / "world_cache.json"

    def _load(self):
        f = self._cache()
        if f.exists():
            try:
                c = json.loads(f.read_text())
                self.rows, self.corr, self.adr = c.get("rows", {}), c.get("corr", {}), c.get("adr", [])
                self.calendar, self.results = c.get("calendar", []), c.get("results", [])
                self.updated = c.get("updated", {})
                self.extremes = c.get("extremes", {})
            except Exception:
                pass

    def _save(self):
        try:
            self._cache().write_text(json.dumps({"rows": self.rows, "corr": self.corr, "adr": self.adr, "calendar": self.calendar,
                                                 "results": self.results, "updated": self.updated,
                                                 "extremes": getattr(self, "extremes", {})}, default=str))
        except Exception:
            pass

    # ------------------------------------------------------------ prices
    def _tickers(self):
        return [t for g in ASSETS.values() for _, t, _ in g]

    def refresh_prices(self):
        import yfinance as yf
        ticks = self._tickers() + [NIFTY] + [v[0] for v in ADR.values()]
        df = yf.download(ticks, period="5d", interval="1d", progress=False, group_by="ticker", auto_adjust=False, threads=4)
        names = {t: (n, g, reg) for g, items in ASSETS.items() for n, t, reg in items}
        for t in ticks:
            try:
                s = df[t]["Close"].dropna()
                if len(s) < 2:
                    continue
                last, prev = float(s.iloc[-1]), float(s.iloc[-2])
                n, g, reg = names.get(t, (t, None, None))
                old = self.rows.get(t) or {}
                row = {"ticker": t, "name": n, "group": g, "region": reg, "last": last, "prev": prev,
                       "chg_pct": _pct(last, prev), "asof": str(s.index[-1].date()), "spark": [float(x) for x in s.tail(5)],
                       "d_last": last, "d_prev": prev}
                if old.get("live_date") and old["live_date"] >= row["asof"] and old.get("live_ts", 0) > time.time() - 900:
                    # a fresher intraday price is already known: keep it, against the right previous close
                    pv = last if old["live_date"] > row["asof"] else prev
                    row.update(last=old["last"], prev=pv, chg_pct=_pct(old["last"], pv), live_ts=old["live_ts"],
                               live_date=old["live_date"], spark_day=old.get("spark_day"), spark_pc=old.get("spark_pc"),
                               **{k: old[k] for k in ("day_h", "day_l", "hi52", "lo52") if old.get(k) is not None})
                self.rows[t] = row
            except Exception:
                continue
        # ADR premium vs the NSE price
        usd = (self.rows.get("INR=X") or {}).get("last")
        self.adr = []
        for adr, (nse, ratio) in ADR.items():
            a, n = self.rows.get(adr), self.rows.get(nse)
            if a and n and usd:
                implied = a["last"] * usd / ratio
                self.adr.append({"adr": adr, "name": a["name"], "nse": nse.replace(".NS", ""), "adr_usd": a["last"],
                                 "implied_inr": implied, "nse_inr": n["last"], "premium_pct": _pct(implied, n["last"]),
                                 "adr_chg": a["chg_pct"]})
        self.updated["prices"] = time.time()
        self._check_moves()
        self._save()

    @staticmethod
    def _chart(t: str) -> dict | None:
        """One market from Yahoo's chart endpoint: last price, the exchange's own previous close, today's 2-minute line.
        Lighter and more exact than a bulk download (the % change matches Yahoo's own page)."""
        import requests
        from urllib.parse import quote
        r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(t)}", params={"range": "1d", "interval": "2m"},
                         headers={"User-Agent": "Mozilla/5.0 (Macintosh) Stockway"}, timeout=8)
        r.raise_for_status()
        res = (r.json().get("chart") or {}).get("result") or []
        if not res:
            return None
        m = res[0].get("meta") or {}
        ts = res[0].get("timestamp") or []
        q = ((res[0].get("indicators") or {}).get("quote") or [{}])[0]
        pts = [(a, c) for a, c in zip(ts, q.get("close") or []) if c is not None]
        last = m.get("regularMarketPrice") or (pts[-1][1] if pts else None)
        pc = m.get("chartPreviousClose") or m.get("previousClose")
        if last is None or not pc:
            return None
        line = [c for _, c in pts]
        step = max(1, len(line) // 120)
        hi = [x for x in (q.get("high") or []) if x is not None]
        lo = [x for x in (q.get("low") or []) if x is not None]
        num = lambda v: float(v) if isinstance(v, (int, float)) else None
        return {"last": float(last), "prev": float(pc), "ts": float(m.get("regularMarketTime") or (pts[-1][0] if pts else time.time())),
                "line": [float(x) for x in line[::step]] + ([float(last)] if line else []),
                "day_h": num(m.get("regularMarketDayHigh")) or (max(hi) if hi else None),
                "day_l": num(m.get("regularMarketDayLow")) or (min(lo) if lo else None),
                "hi52": num(m.get("fiftyTwoWeekHigh")), "lo52": num(m.get("fiftyTwoWeekLow"))}

    def refresh_live(self):
        """Intraday prices for every market, between the slower daily refreshes: Yahoo's chart endpoint in parallel
        (about 2 seconds for all of them); falls back to the bulk 1-minute download if that fails."""
        if not self.rows:
            self.refresh_prices()
        from concurrent.futures import ThreadPoolExecutor
        ticks = list(dict.fromkeys(list(self.rows) + self._tickers()))     # a ticker the daily download missed too
        got = {}
        with ThreadPoolExecutor(max_workers=8) as ex:
            for t, res in zip(ticks, ex.map(lambda x: self._safe_chart(x), ticks)):
                if res:
                    got[t] = res
        if len(got) >= max(3, len(ticks) // 2):
            names = {t: (n, g, reg) for g, items in ASSETS.items() for n, t, reg in items}
            for t, c in got.items():
                if t not in self.rows:
                    n, g, reg = names.get(t, (t, None, None))
                    self.rows[t] = {"ticker": t, "name": n, "group": g, "region": reg, "asof": "", "d_last": None, "d_prev": c["prev"]}
                r = self.rows[t]
                bar_date = dt.datetime.fromtimestamp(c["ts"], dt.timezone.utc).date().isoformat()
                # previous close from the daily table (Yahoo's chart 'previousClose' is sometimes a day stale for
                # Indian indices); the chart's own value only if the daily table is missing
                pv = (r.get("d_last") if bar_date > r.get("asof", "") else r.get("d_prev")) or c["prev"]
                r.update(last=c["last"], prev=pv, chg_pct=_pct(c["last"], pv), live_ts=c["ts"], live_date=bar_date,
                         spark_day=c["line"] if len(c["line"]) > 2 else r.get("spark_day"), spark_pc=pv)
                for k in ("day_h", "day_l", "hi52", "lo52"):
                    if c.get(k) is not None:
                        r[k] = c[k]
            self._after_live()
            return len(got)
        return self._refresh_live_bulk()

    def refresh_extremes(self):
        """All-time and 52-week highs and lows for the Indian indices and the main world indices (monthly bars since
        Yahoo's first data). Once every few hours; each ticker on its own, so one failure skips only that one."""
        from concurrent.futures import ThreadPoolExecutor
        ticks = list(dict.fromkeys(list(YAHOO_INDEX.values()) + [t for _, items in LIVE_BOARD for _, t in items if not t.startswith("_")]))
        ext = dict(getattr(self, "extremes", {}) or {})
        with ThreadPoolExecutor(max_workers=6) as ex:
            for t, res in zip(ticks, ex.map(self._safe_extremes, ticks)):
                if res:
                    ext[t] = res
        self.extremes = ext
        self.updated["extremes"] = time.time()
        self._save()
        return len(ext)

    def _safe_extremes(self, t):
        try:
            import requests
            from urllib.parse import quote
            r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(t)}", params={"range": "max", "interval": "1mo"},
                             headers={"User-Agent": "Mozilla/5.0 (Macintosh) Stockway"}, timeout=10)
            r.raise_for_status()
            return extremes_from_chart(r.json())
        except Exception:
            return None

    def _safe_chart(self, t):
        try:
            return self._chart(t)
        except Exception:
            return None

    def _after_live(self):
        usd = (self.rows.get("INR=X") or {}).get("last")
        for a in self.adr:
            r = self.rows.get(a["adr"])
            n = self.rows.get(a["nse"] + ".NS")
            if r and usd:
                a.update(adr_usd=r["last"], adr_chg=r["chg_pct"], implied_inr=r["last"] * usd / ADR[a["adr"]][1])
                if n:
                    a["nse_inr"] = n["last"]
                a["premium_pct"] = _pct(a["implied_inr"], a["nse_inr"])
        self.updated["live"] = time.time()
        self._check_moves()

    def _refresh_live_bulk(self):
        import yfinance as yf
        ticks = [t for t in self.rows]
        df = yf.download(ticks, period="2d", interval="1m", progress=False, group_by="ticker", auto_adjust=False,
                         threads=4, prepost=False)
        n_up = 0
        for t in ticks:
            try:
                s = df[t]["Close"].dropna()
            except Exception:
                continue
            if s.empty:
                continue
            r = self.rows[t]
            last = float(s.iloc[-1])
            ts = s.index[-1]
            bar_date = str(ts.date())
            # previous close: yesterday's if the daily table already has today's bar, else the daily table's last
            pv = r.get("d_last") if bar_date > r.get("asof", "") else r.get("d_prev")
            if not pv:
                continue
            today = s[s.index.date == ts.date()]
            day = today.iloc[:: max(1, len(today) // 40)].tolist() + [last] if len(today) > 1 else None
            r.update(last=last, prev=pv, chg_pct=_pct(last, pv), live_ts=float(ts.timestamp()), live_date=bar_date,
                     spark_day=[float(x) for x in day] if day else r.get("spark_day"))
            n_up += 1
        usd = (self.rows.get("INR=X") or {}).get("last")
        for a in self.adr:
            r = self.rows.get(a["adr"])
            n = self.rows.get(a["nse"] + ".NS")
            if r and usd:
                a.update(adr_usd=r["last"], adr_chg=r["chg_pct"], implied_inr=r["last"] * usd / ADR[a["adr"]][1])
                if n:
                    a["nse_inr"] = n["last"]
                a["premium_pct"] = _pct(a["implied_inr"], a["nse_inr"])
        self.updated["live"] = time.time()
        self._check_moves()
        return n_up

    def refresh_correlations(self):
        """How each market's daily move relates to Nifty's: the same day, and Nifty's next day (overnight lead)."""
        import numpy as np
        import yfinance as yf
        ticks = self._tickers() + [NIFTY]
        df = yf.download(ticks, period="9mo", interval="1d", progress=False, group_by="ticker", auto_adjust=False, threads=4)
        closes = {}
        for t in ticks:
            try:
                closes[t] = df[t]["Close"].dropna()
            except Exception:
                pass
        if NIFTY not in closes:
            return
        import pandas as pd
        nf = closes[NIFTY].pct_change()
        nf.index = pd.to_datetime(nf.index).date
        for t, s in closes.items():
            if t == NIFTY:
                continue
            r = s.pct_change()
            r.index = pd.to_datetime(r.index).date
            j = pd.concat([r.rename("a"), nf.rename("n")], axis=1).dropna()
            j_next = pd.concat([r.rename("a"), nf.shift(-1).rename("n")], axis=1).dropna()
            out = {}
            for k, d in (("same", j), ("next", j_next)):
                if len(d) >= 60 and d["a"].std() > 0:
                    c = float(np.corrcoef(d["a"], d["n"])[0, 1])
                    out[k] = round(c, 2)
                    out["beta_" + k] = round(float(c * d["n"].std() / d["a"].std()), 3)
            self.corr[t] = out
        self.updated["corr"] = time.time()
        self._save()

    def pressure(self) -> dict:
        """Indicative pressure on Nifty from markets that trade after/around India, using their past relation."""
        parts = []
        for t, r in self.rows.items():
            c = self.corr.get(t) or {}
            if r.get("region") in ("us", "eu"):
                beta, rho = c.get("beta_next"), c.get("next")
            elif r.get("region") in ("asia", "in"):
                beta, rho = c.get("beta_same"), c.get("same")
            else:
                continue
            if beta is None or rho is None or r.get("chg_pct") is None or abs(rho) < 0.15:
                continue
            parts.append({"name": r["name"], "move": r["chg_pct"], "impact": beta * r["chg_pct"], "weight": abs(rho)})
        if not parts:
            return {}
        w = sum(p["weight"] for p in parts)
        imp = sum(p["impact"] * p["weight"] for p in parts) / w
        parts.sort(key=lambda p: -abs(p["impact"] * p["weight"]))
        label = "strongly positive" if imp > 0.35 else "positive" if imp > 0.1 else "strongly negative" if imp < -0.35 else "negative" if imp < -0.1 else "neutral"
        return {"implied_pct": round(imp, 2), "label": label, "drivers": parts[:6]}

    def _check_moves(self):
        if not self.on_macro:
            return
        day = dt.datetime.now(IST).date().isoformat()
        for t, thr in ALERT_MOVES.items():
            r = self.rows.get(t)
            if not r or r.get("chg_pct") is None:
                continue
            lvl = int(abs(r["chg_pct"]) // thr)
            k = (t, day)
            if lvl >= 1 and lvl > self.flagged.get(k, 0):
                self.flagged[k] = lvl
                c = self.corr.get(t) or {}
                beta = c.get("beta_next") if r["region"] in ("us", "eu") else c.get("beta_same")
                imp = f" Past relation suggests about {beta * r['chg_pct']:+.2f}% for Nifty." if beta else ""
                d = "up" if (beta or 0) * r["chg_pct"] > 0 else "down" if (beta or 0) * r["chg_pct"] < 0 else "neutral"
                self.on_macro({"sym": "GLOBAL", "kind": "macro", "direction": d, "severity": 3 if lvl >= 2 else 2,
                               "title": f"{r['name']} {r['chg_pct']:+.2f}% today", "detail": f"Now {r['last']:,.2f}.{imp}",
                               "price": None, "data": {"ticker": t}})

    # ------------------------------------------------------------ calendars
    def refresh_calendar(self, fo_symbols=None, extra_events=()):
        ev = []
        r = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", timeout=20,
                         headers={"User-Agent": "Mozilla/5.0 Stockway"})
        r.raise_for_status()
        for x in r.json():
            if x.get("impact") not in ("High", "Medium"):
                continue
            try:
                t = dt.datetime.fromisoformat(x["date"]).astimezone(IST)
            except Exception:
                continue
            ev.append({"time": t.isoformat(), "country": x.get("country"), "title": x.get("title"), "impact": x.get("impact"),
                       "forecast": x.get("forecast"), "previous": x.get("previous")})
        for d, title in extra_events:
            ev.append({"time": dt.datetime.combine(d, dt.time(10, 0), IST).isoformat(), "country": "INR", "title": title,
                       "impact": "High", "forecast": "", "previous": ""})
        self.calendar = sorted(ev, key=lambda e: e["time"])
        self.updated["calendar"] = time.time()
        self._save()

    def refresh_results(self, nse, fo_symbols: set):
        rows = nse.api("/api/event-calendar") or []
        today = dt.datetime.now(IST).date()
        out = []
        for x in rows:
            try:
                d = dt.datetime.strptime(x["date"], "%d-%b-%Y").date()
            except Exception:
                continue
            if not (today <= d <= today + dt.timedelta(days=10)):
                continue
            if fo_symbols and x.get("symbol") not in fo_symbols:
                continue
            if "result" not in (x.get("purpose") or "").lower():
                continue
            out.append({"date": d.isoformat(), "sym": x.get("symbol"), "company": x.get("company"), "purpose": x.get("purpose")})
        self.results = sorted(out, key=lambda r: (r["date"], r["sym"]))
        self.updated["results"] = time.time()
        self._save()

    def upcoming(self, minutes=90) -> list:
        now = dt.datetime.now(IST)
        out = []
        for e in self.calendar:
            t = dt.datetime.fromisoformat(e["time"])
            mins = (t - now).total_seconds() / 60
            if -30 <= mins <= minutes:
                out.append({**e, "mins": round(mins)})
        return out

    def check_events(self):
        """Heads-up alert 15 minutes before a high-impact event."""
        if not self.on_macro:
            return
        for e in self.upcoming(16):
            if e["impact"] == "High" and 0 <= e["mins"] <= 15 and (e["title"], e["time"]) not in self.event_flagged:
                self.event_flagged.add((e["title"], e["time"]))
                fc = f" Forecast {e['forecast']}, previous {e['previous']}." if e.get("forecast") else ""
                self.on_macro({"sym": "GLOBAL", "kind": "macro", "direction": "neutral", "severity": 2,
                               "title": f"{e['country']} {e['title']} in {e['mins']} min",
                               "detail": "High-impact event: expect a volatility burst; tighten stops or wait." + fc,
                               "price": None, "data": {"event": e}})

    # ------------------------------------------------------------ news with impact tags
    def refresh_news(self):
        new = self.news.fetch({})
        for n in new:
            t = n["title"]
            high = bool(HIGH.search(t))
            india = bool(INDIA.search(t))
            d = -1 if NEG.search(t) and not POS.search(t) else 1 if POS.search(t) and not NEG.search(t) else 0
            self.tags[t] = {"impact": "high" if high and india else "medium" if high or india else "low",
                            "direction": d, "india": india, "by": "rules"}
        if self.ai and self.ai.enabled():
            todo = [n["title"] for n in new if self.tags.get(n["title"], {}).get("impact") != "low"][:20]
            if todo:
                self._ai_tag(todo)
        self.updated["news"] = time.time()
        return new

    def _ai_tag(self, titles):
        prompt = ("For each headline, judge the likely impact on Indian equity indices (Nifty/Bank Nifty) today. Reply ONLY with a "
                  "JSON list of objects {\"i\": index, \"impact\": \"high\"|\"medium\"|\"low\", \"direction\": 1|0|-1, \"why\": "
                  "\"<8 words\"}.\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(titles)))
        text = self.ai.ask(prompt, 900)
        if not text:
            return
        try:
            m = re.search(r"\[.*\]", text, re.S)
            for x in json.loads(m.group(0)):
                t = titles[int(x["i"])]
                self.tags[t] = {"impact": x.get("impact", "low"), "direction": int(x.get("direction", 0)),
                                "why": x.get("why"), "india": True, "by": "AI"}
        except Exception:
            pass

    # ------------------------------------------------------------ view
    def view(self) -> dict:
        groups = []
        for g, items in ASSETS.items():
            rows = []
            for n, t, reg in items:
                r = self.rows.get(t)
                if not r:
                    continue
                c = self.corr.get(t) or {}
                beta = c.get("beta_next") if reg in ("us", "eu") else c.get("beta_same")
                rows.append({**r, "corr": c.get("next") if reg in ("us", "eu") else c.get("same"),
                             "lead": "next day" if reg in ("us", "eu") else "same day",
                             "nifty_impact": beta * r["chg_pct"] if beta is not None and r.get("chg_pct") is not None else None})
            groups.append({"group": g, "rows": rows})
        news = []
        for n in self.news.items[:80]:
            tg = self.tags.get(n["title"]) or {}
            news.append({**n, **tg})
        return {"groups": groups, "pressure": self.pressure(), "adr": self.adr, "calendar": self.calendar,
                "upcoming": self.upcoming(120), "results": self.results[:120], "news": news,
                "nifty": self.rows.get(NIFTY), "errors": self.errors, "updated": self.updated}


def extremes_from_chart(js: dict) -> dict | None:
    """Yahoo monthly chart JSON -> {ath, ath_date, atl, atl_date, since, hi52, lo52} (None if empty)."""
    res = ((js or {}).get("chart") or {}).get("result") or []
    if not res:
        return None
    ts = res[0].get("timestamp") or []
    q = ((res[0].get("indicators") or {}).get("quote") or [{}])[0]
    rows = [(t, h, l) for t, h, l in zip(ts, q.get("high") or [], q.get("low") or []) if h is not None and l is not None and h > 0 and l > 0]
    if not rows:
        return None
    day = lambda t: dt.datetime.fromtimestamp(t, IST).date().isoformat()
    hi = max(rows, key=lambda r: r[1])
    lo = min(rows, key=lambda r: r[2])
    last12 = rows[-12:]
    m = res[0].get("meta") or {}
    return {"ath": round(float(hi[1]), 2), "ath_date": day(hi[0])[:7], "atl": round(float(lo[2]), 2), "atl_date": day(lo[0])[:7],
            "since": day(rows[0][0])[:4], "months": len(rows),
            "hi52": float(m["fiftyTwoWeekHigh"]) if isinstance(m.get("fiftyTwoWeekHigh"), (int, float)) else round(max(r[1] for r in last12), 2),
            "lo52": float(m["fiftyTwoWeekLow"]) if isinstance(m.get("fiftyTwoWeekLow"), (int, float)) else round(min(r[2] for r in last12), 2)}


def short_history(ext: dict | None, today: dt.date | None = None) -> bool:
    """2.56.2: Yahoo has almost no history for some indices (Nifty Midcap Select: one monthly bar on 11 Nov 2026), so
    its "52-week" range was just today's range. Less than a year of monthly bars is not a 52-week range."""
    if not ext:
        return True
    if ext.get("months") is not None:
        return ext["months"] < 12
    try:
        return int(ext.get("since") or 0) >= (today or dt.date.today()).year
    except (TypeError, ValueError):
        return True


def extremes_from_daily(rows: list, day_h=None, day_l=None, now: float | None = None) -> dict | None:
    """52-week high / low from daily bars [[ts, o, h, l, c, v], ...] (the chart's saved Dhan daily bars), today's live
    high / low included. All-time high / low only when the bars go back 3+ years (else None: not a claim we can make)."""
    import time as _t
    now = now or _t.time()
    rows = [r for r in rows or [] if r and r[2] and r[3]]
    yr = [r for r in rows if r[0] >= now - 365 * 86400]
    if len(yr) < 200:
        return None
    his = [r[2] for r in yr] + ([day_h] if day_h else [])
    los = [r[3] for r in yr] + ([day_l] if day_l else [])
    day = lambda t: dt.datetime.fromtimestamp(t, IST).date().isoformat()
    out = {"hi52": round(float(max(his)), 2), "lo52": round(float(min(los)), 2), "since": day(rows[0][0])[:4], "src": "daily bars",
           "ath": None, "ath_date": None, "atl": None, "atl_date": None}
    if rows[0][0] <= now - 3 * 365 * 86400:
        hi = max(rows, key=lambda r: r[2])
        lo = min(rows, key=lambda r: r[3])
        out.update(ath=round(float(max(hi[2], day_h or 0)), 2), ath_date=day(hi[0])[:7], atl=round(float(lo[3]), 2), atl_date=day(lo[0])[:7])
    return out
