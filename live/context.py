"""P2: who is positioned where, and what just happened. All free sources.

NSE archives (no login): participant-wise OI, MWPL / ban list, bulk and block deals, Nifty 500 sectors.
NSE API (browser-like session): live corporate filings, FII/DII cash.
Yahoo Finance (via yfinance): global markets for the pre-market picture.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import time
import zipfile
from pathlib import Path

import requests

from .clock import IST

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
ARCH = "https://nsearchives.nseindia.com"
SITE = "https://www.nseindia.com"

GLOBAL = {
    "US S&P 500 fut": "ES=F", "US Nasdaq fut": "NQ=F", "Dow fut": "YM=F", "Nikkei": "^N225", "Hang Seng": "^HSI",
    "Kospi": "^KS11", "Brent crude": "BZ=F", "Gold": "GC=F", "Dollar index": "DX-Y.NYB", "USD/INR": "INR=X",
    "US 10Y yield": "^TNX", "US VIX": "^VIX",
}
# approximate index weights (%), used for "who is moving the index"; edit data/index_weights.json to update
WEIGHTS = {
    "BANKNIFTY": {"HDFCBANK": 28.0, "ICICIBANK": 25.0, "SBIN": 9.5, "KOTAKBANK": 8.5, "AXISBANK": 8.5,
                  "INDUSINDBK": 2.5, "BANKBARODA": 3.0, "FEDERALBNK": 2.7, "PNB": 2.3, "CANBK": 2.6,
                  "IDFCFIRSTB": 2.1, "AUBANK": 2.3, "YESBANK": 1.2},
    "NIFTY": {"HDFCBANK": 12.8, "ICICIBANK": 8.9, "RELIANCE": 8.4, "INFY": 4.8, "BHARTIARTL": 4.7, "LT": 3.9,
              "ITC": 3.3, "TCS": 2.9, "SBIN": 3.1, "AXISBANK": 3.0, "KOTAKBANK": 2.8, "M&M": 2.6, "BAJFINANCE": 2.3,
              "HINDUNILVR": 1.9, "SUNPHARMA": 1.6, "ETERNAL": 1.6, "MARUTI": 1.6, "HCLTECH": 1.5, "NTPC": 1.4,
              "TITAN": 1.3, "ULTRACEMCO": 1.2, "TATAMOTORS": 1.1, "POWERGRID": 1.1, "BEL": 1.1, "TATASTEEL": 1.1,
              "ADANIPORTS": 1.0, "ONGC": 0.9, "JSWSTEEL": 0.9, "BAJAJFINSV": 0.9, "HINDALCO": 0.9, "TRENT": 0.9,
              "COALINDIA": 0.8, "WIPRO": 0.7, "TECHM": 0.7, "ASIANPAINT": 0.8, "GRASIM": 0.8, "SHRIRAMFIN": 0.8,
              "CIPLA": 0.6, "SBILIFE": 0.7, "HDFCLIFE": 0.7, "NESTLEIND": 0.7, "EICHERMOT": 0.7, "APOLLOHOSP": 0.6,
              "DRREDDY": 0.6, "ADANIENT": 0.6, "BAJAJ-AUTO": 0.8, "HEROMOTOCO": 0.5, "INDIGO": 0.9, "JIOFIN": 0.8,
              "MAXHEALTH": 0.6},
}
SIM_SECTORS = {"RELIANCE": "Oil Gas & Consumable Fuels", "ONGC": "Oil Gas & Consumable Fuels",
               "HDFCBANK": "Financial Services", "ICICIBANK": "Financial Services", "SBIN": "Financial Services",
               "AXISBANK": "Financial Services", "KOTAKBANK": "Financial Services", "BAJFINANCE": "Financial Services",
               "INDUSINDBK": "Financial Services", "PNB": "Financial Services", "CANBK": "Financial Services",
               "IRFC": "Financial Services", "PAYTM": "Financial Services", "INFY": "Information Technology",
               "TCS": "Information Technology", "HCLTECH": "Information Technology", "WIPRO": "Information Technology",
               "LT": "Construction", "ITC": "Fast Moving Consumer Goods", "HINDUNILVR": "Fast Moving Consumer Goods",
               "BHARTIARTL": "Telecommunication", "MARUTI": "Automobile and Auto Components",
               "TATAMOTORS": "Automobile and Auto Components", "M&M": "Automobile and Auto Components",
               "SUNPHARMA": "Healthcare", "TITAN": "Consumer Durables", "DIXON": "Consumer Durables",
               "ADANIENT": "Metals & Mining", "ADANIPORTS": "Services", "TATASTEEL": "Metals & Mining",
               "JSWSTEEL": "Metals & Mining", "HINDALCO": "Metals & Mining", "VEDL": "Metals & Mining",
               "COALINDIA": "Oil Gas & Consumable Fuels", "ULTRACEMCO": "Construction Materials", "NTPC": "Power",
               "POWERGRID": "Power", "HAL": "Capital Goods", "BEL": "Capital Goods", "ZOMATO": "Consumer Services",
               "SWIGGY": "Consumer Services", "DLF": "Realty"}

FILING_RULES = [   # (regex on description + text, label, severity, tone)
    (r"fraud|default|insolven|forensic|raid|search and seizure|sebi order|penalt|show cause", "Red flag", 2, -1),
    (r"financial result|results for the|outcome of board meeting.*result", "Results", 2, 0),
    (r"buy ?back", "Buyback", 2, 1),
    (r"bonus|stock split|sub-division", "Bonus / split", 2, 1),
    (r"acqui|amalgamation|merger|scheme of arrangement|demerger|takeover|open offer", "M&A", 2, 0),
    (r"award|bagging|receipt of order|order win|letter of intent|loa |contract", "Order win", 2, 1),
    (r"resignation|cessation", "Resignation", 2, -1),
    (r"pledge|encumbrance", "Pledge", 1, -1),
    (r"credit rating|rating", "Rating", 1, 0),
    (r"fund raising|qualified institutions placement|qip|preferential issue|rights issue", "Fund raising", 1, 0),
    (r"dividend", "Dividend", 1, 1),
]
FILING_SKIP = re.compile(r"trading window|newspaper|investor presentation|analysts?/institutional|con\. call|"
                         r"loss of share|certificate under|esop|esos|monitoring agency|shareholders meeting|"
                         r"general updates|clarification|copy of|record date|book closure|transcript|"
                         r"spurt in volume|price movement", re.I)


class NSE:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"})
        self.warm = 0.0

    def _warmup(self):
        if time.time() - self.warm > 600:
            try:
                self.s.get(SITE, timeout=10)
            except Exception:
                pass
            self.warm = time.time()

    def text(self, url) -> str | None:
        r = self.s.get(url, timeout=20)
        if r.status_code != 200 or r.text.lstrip().startswith("<"):
            return None
        return r.text

    def api(self, path):
        self._warmup()
        r = self.s.get(SITE + path, timeout=20, headers={"Referer": SITE + "/"})
        if r.status_code == 401 or r.status_code == 403:
            self.warm = 0
            self._warmup()
            r = self.s.get(SITE + path, timeout=20, headers={"Referer": SITE + "/"})
        r.raise_for_status()
        return r.json()

    def latest(self, fmt, days=7, parse=None):
        """Try today, then earlier days, for a dated archive file."""
        d = dt.datetime.now(IST).date()
        for i in range(days):
            day = d - dt.timedelta(days=i)
            if day.weekday() >= 5:
                continue
            url = ARCH + fmt.format(d=day)
            try:
                out = parse(url) if parse else self.text(url)
            except Exception:
                out = None
            if out:
                return day, out
        return None, None

    # ---------------- daily files
    def participant_oi(self):
        def parse(url):
            t = self.text(url)
            if not t:
                return None
            lines = [l for l in t.splitlines() if l.strip()]
            start = next(i for i, l in enumerate(lines) if l.startswith("Client Type"))
            rdr = csv.DictReader(io.StringIO("\n".join(lines[start:])))
            out = {}
            for row in rdr:
                row = {k.strip(): v.strip() for k, v in row.items() if k}
                out[row["Client Type"]] = {k: float(v) for k, v in row.items() if k != "Client Type" and v}
            return out
        day, today = self.latest("/content/nsccl/fao_participant_oi_{d:%d%m%Y}.csv", parse=parse)
        if not today:
            return None
        prev_day = day - dt.timedelta(days=1)
        prev = None
        for i in range(7):
            p = prev_day - dt.timedelta(days=i)
            if p.weekday() >= 5:
                continue
            try:
                prev = parse(ARCH + f"/content/nsccl/fao_participant_oi_{p:%d%m%Y}.csv")
            except Exception:
                prev = None
            if prev:
                break
        rows = []
        for who in ("FII", "DII", "Pro", "Client"):
            x = today.get(who)
            if not x:
                continue
            fl, fs = x.get("Future Index Long", 0), x.get("Future Index Short", 0)
            cl, cs = x.get("Option Index Call Long", 0), x.get("Option Index Call Short", 0)
            pl, ps = x.get("Option Index Put Long", 0), x.get("Option Index Put Short", 0)
            r = {"who": who, "fut_long": fl, "fut_short": fs, "fut_net": fl - fs,
                 "fut_long_pct": round(fl / (fl + fs) * 100, 1) if fl + fs else None,
                 "call_net": cl - cs, "put_net": pl - ps, "stock_fut_net": x.get("Future Stock Long", 0) - x.get("Future Stock Short", 0)}
            if prev and prev.get(who):
                y = prev[who]
                r["fut_net_chg"] = r["fut_net"] - (y.get("Future Index Long", 0) - y.get("Future Index Short", 0))
                r["call_net_chg"] = r["call_net"] - (y.get("Option Index Call Long", 0) - y.get("Option Index Call Short", 0))
                r["put_net_chg"] = r["put_net"] - (y.get("Option Index Put Long", 0) - y.get("Option Index Put Short", 0))
            rows.append(r)
        return {"date": day.isoformat(), "rows": rows}

    def mwpl(self):
        def parse(url):
            r = self.s.get(url, timeout=30)
            if r.status_code != 200 or not r.content[:2] == b"PK":
                return None
            z = zipfile.ZipFile(io.BytesIO(r.content))
            name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
            rdr = csv.DictReader(io.StringIO(z.read(name).decode("utf-8", "ignore")))
            out = {}
            for row in rdr:
                row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
                sym = row.get("NSE Symbol")
                try:
                    mw = float(row.get("MWPL") or 0)
                    feo = float(row.get("Future Equivalent Open Interest") or row.get("Open Interest") or 0)
                except ValueError:
                    continue
                if sym and mw:
                    out[sym] = {"pct": round(feo / mw * 100, 1),
                                "ban": "no fresh" in (row.get("Limit for Next Day") or "").lower()}
            return out
        day, data = self.latest("/archives/nsccl/mwpl/combineoi_{d:%d%m%Y}.zip", parse=parse)
        return {"date": day.isoformat(), "symbols": data} if data else None

    def deals(self):
        out = []
        for kind, path in (("Bulk", "/content/equities/bulk.csv"), ("Block", "/content/equities/block.csv")):
            t = self.text(ARCH + path)
            if not t:
                continue
            for row in csv.DictReader(io.StringIO(t)):
                row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
                try:
                    qty = float(row.get("Quantity Traded") or 0)
                    px = float(row.get("Trade Price / Wght. Avg. Price") or 0)
                except ValueError:
                    continue
                out.append({"type": kind, "date": row.get("Date"), "sym": row.get("Symbol"), "client": row.get("Client Name"),
                            "side": row.get("Buy/Sell"), "qty": qty, "price": px, "value_cr": round(qty * px / 1e7, 2)})
        return sorted(out, key=lambda d: -d["value_cr"])

    def sectors(self) -> dict:
        t = self.text(ARCH + "/content/indices/ind_nifty500list.csv")
        if not t:
            return {}
        return {r["Symbol"].strip(): r["Industry"].strip() for r in csv.DictReader(io.StringIO(t)) if r.get("Symbol")}

    # ---------------- live API
    def fii_dii(self):
        data = self.api("/api/fiidiiTradeReact")
        out = {}
        for r in data or []:
            cat = "FII" if "FII" in r.get("category", "") else "DII"
            out[cat] = {"date": r.get("date"), "buy": float(r["buyValue"]), "sell": float(r["sellValue"]),
                        "net": float(r["netValue"])}
        return out

    def announcements(self):
        return self.api("/api/corporate-announcements?index=equities") or []


def classify_filing(desc: str, text: str):
    blob = f"{desc} {text}".lower()
    if FILING_SKIP.search(desc or ""):
        return None
    for rx, label, sev, tone in FILING_RULES:
        if re.search(rx, blob):
            if label in ("M&A", "Resignation") and re.search(r"subsidiar|independent director|company secretary", blob):
                sev = 1
            return label, sev, tone
    return None


def global_markets(log=print) -> list:
    try:
        import yfinance as yf
    except ImportError:
        return []
    try:
        df = yf.download(list(GLOBAL.values()), period="5d", interval="1d", progress=False, group_by="ticker",
                         auto_adjust=False, threads=True)
    except Exception as e:
        log(f"Global markets failed: {e}")
        return []
    out = []
    for name, t in GLOBAL.items():
        try:
            s = df[t]["Close"].dropna()
            if len(s) < 2:
                continue
            last, prev = float(s.iloc[-1]), float(s.iloc[-2])
            out.append({"name": name, "symbol": t, "last": last, "chg_pct": (last / prev - 1) * 100,
                        "asof": str(s.index[-1].date())})
        except Exception:
            continue
    return out


class Context:
    """Holds all market context; refreshes on its own thread."""

    def __init__(self, data_dir: Path, log=print, live=True):
        self.data_dir = Path(data_dir)
        self.log = log
        self.nse = NSE()
        self.live = live
        self.participants = None
        self.mwpl = None
        self.deals = []
        self.fii_dii = None
        self.global_ = []
        self.filings = []           # newest first, F&O names only
        self.all_filings = []
        self.seen_seq = set()
        self.sector_map = {}
        self.errors = {}
        self.updated = {}
        self.on_filing = None       # callback(filing) for alerts
        self.fo_symbols: set = set()
        self.weights = self._load_weights()
        self._load_cache()

    def _load_weights(self):
        f = self.data_dir / "index_weights.json"
        if f.exists():
            try:
                return json.loads(f.read_text())
            except Exception:
                pass
        return WEIGHTS

    def _load_cache(self):
        f = self.data_dir / "context_cache.json"
        if f.exists():
            try:
                c = json.loads(f.read_text())
                self.participants, self.mwpl = c.get("participants"), c.get("mwpl")
                self.deals, self.fii_dii = c.get("deals") or [], c.get("fii_dii")
                self.global_ = c.get("global") or []
                self.sector_map = c.get("sectors") or {}
                self.updated = c.get("updated") or {}
            except Exception:
                pass

    def _save_cache(self):
        try:
            (self.data_dir / "context_cache.json").write_text(json.dumps({
                "participants": self.participants, "mwpl": self.mwpl, "deals": self.deals[:200], "fii_dii": self.fii_dii,
                "global": self.global_, "sectors": self.sector_map, "updated": self.updated}, default=str))
        except Exception:
            pass

    def _do(self, name, fn):
        try:
            v = fn()
            self.errors.pop(name, None)
            self.updated[name] = time.time()
            return v
        except Exception as e:
            self.errors[name] = str(e)[:160]
            return None

    def refresh_daily(self):
        p = self._do("participants", self.nse.participant_oi)
        if p:
            self.participants = p
        m = self._do("mwpl", self.nse.mwpl)
        if m:
            self.mwpl = m
        d = self._do("deals", self.nse.deals)
        if d is not None:
            self.deals = d
        if not self.sector_map or time.time() - self.updated.get("sectors", 0) > 7 * 86400:
            s = self._do("sectors", self.nse.sectors)
            if s:
                self.sector_map = s
        f = self._do("fii_dii", self.nse.fii_dii)
        if f:
            self.fii_dii = f
        self._save_cache()

    def refresh_global(self):
        g = global_markets(self.log)
        if g:
            self.global_ = g
            self.updated["global"] = time.time()
            self._save_cache()

    def poll_filings(self):
        rows = self._do("filings", self.nse.announcements) or []
        new = []
        for r in rows:
            seq = r.get("seq_id")
            if not seq or seq in self.seen_seq:
                continue
            self.seen_seq.add(seq)
            cls = classify_filing(r.get("desc", ""), r.get("attchmntText", ""))
            item = {"seq": seq, "sym": r.get("symbol"), "name": r.get("sm_name"), "desc": r.get("desc"),
                    "text": (r.get("attchmntText") or "")[:300], "time": r.get("an_dt"), "link": r.get("attchmntFile"),
                    "label": cls[0] if cls else None, "severity": cls[1] if cls else 0, "tone": cls[2] if cls else 0,
                    "fo": not self.fo_symbols or r.get("symbol") in self.fo_symbols}
            new.append(item)
        new.sort(key=lambda x: x["seq"])
        first_load = not self.all_filings
        self.all_filings = (list(reversed(new)) + self.all_filings)[:400]
        self.filings = [x for x in self.all_filings if x["fo"]][:150]
        if self.on_filing and not first_load:
            for x in new:
                if x["fo"] and x["label"]:
                    self.on_filing(x)
        return new

    def run_forever(self, should_poll_filings):
        t_daily = t_glob = t_fil = 0.0
        while True:
            now = time.time()
            try:
                if now - t_daily > 1800:
                    t_daily = now
                    self.refresh_daily()
                if now - t_glob > 300:
                    t_glob = now
                    self.refresh_global()
                if should_poll_filings() and now - t_fil > 60:
                    t_fil = now
                    self.poll_filings()
            except Exception as e:
                self.log(f"Context refresh failed: {e}")
            time.sleep(10)

    def sector_of(self, sym):
        return self.sector_map.get(sym) or SIM_SECTORS.get(sym) or "Other"

    def view(self) -> dict:
        ban = near = []
        if self.mwpl:
            syms = self.mwpl["symbols"]
            ban = sorted([s for s, v in syms.items() if v["ban"]])
            near = sorted([{"sym": s, **v} for s, v in syms.items() if not v["ban"] and v["pct"] >= 75 and
                           (not self.fo_symbols or s in self.fo_symbols)], key=lambda d: -d["pct"])[:20]
        fo_deals = [d for d in self.deals if not self.fo_symbols or d["sym"] in self.fo_symbols]
        return {"participants": self.participants, "fii_dii": self.fii_dii, "ban": ban, "near_ban": near,
                "mwpl_date": self.mwpl["date"] if self.mwpl else None, "deals_fo": fo_deals[:40], "deals_all": self.deals[:60],
                "filings": self.filings[:60], "global": self.global_, "errors": self.errors,
                "updated": {k: v for k, v in self.updated.items()}}
