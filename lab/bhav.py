"""NSE F&O bhavcopy: the exchange's free end-of-day file with every futures and options contract (open, high,
low, close, settlement price, open interest, volume). Available for well over 10 years.

Only index contracts (Nifty, Bank Nifty, Fin Nifty, Midcap Nifty) are kept, one parquet per year under
data/history/eod/. The download is resumable and polite (a few requests a second); holidays come back as 404 and
are remembered so they are not asked for again.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

SYMS = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY")
NEW_FORMAT_FROM = dt.date(2024, 7, 8)
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
COLS = ["date", "sym", "kind", "expiry", "strike", "side", "open", "high", "low", "close", "settle", "oi", "chg_oi",
        "volume", "underlying"]


def eod_dir(data_dir) -> Path:
    d = Path(data_dir) / "history" / "eod"
    d.mkdir(parents=True, exist_ok=True)
    return d


def url_for(d: dt.date) -> str:
    if d >= NEW_FORMAT_FROM:
        return f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
    mon = d.strftime("%b").upper()
    return f"https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{d.year}/{mon}/fo{d:%d}{mon}{d.year}bhav.csv.zip"


def parse(raw: bytes, d: dt.date) -> pd.DataFrame:
    z = zipfile.ZipFile(io.BytesIO(raw))
    df = pd.read_csv(z.open(z.namelist()[0]))
    if "TckrSymb" in df.columns:                          # UDiFF format (from July 2024)
        df = df[df["TckrSymb"].isin(SYMS) & df["FinInstrmTp"].isin(["IDO", "IDF"])]
        out = pd.DataFrame({
            "sym": df["TckrSymb"], "kind": df["FinInstrmTp"].map({"IDO": "OPT", "IDF": "FUT"}),
            "expiry": pd.to_datetime(df["XpryDt"]).dt.date, "strike": df["StrkPric"].fillna(0.0).astype(float),
            "side": df["OptnTp"].fillna("XX"), "open": df["OpnPric"], "high": df["HghPric"], "low": df["LwPric"],
            "close": df["ClsPric"], "settle": df["SttlmPric"], "oi": df["OpnIntrst"], "chg_oi": df["ChngInOpnIntrst"],
            "volume": df["TtlTradgVol"], "underlying": df["UndrlygPric"]})
    else:                                                 # the older format
        df.columns = [c.strip() for c in df.columns]
        df = df[df["SYMBOL"].isin(SYMS) & df["INSTRUMENT"].isin(["OPTIDX", "FUTIDX"])]
        out = pd.DataFrame({
            "sym": df["SYMBOL"], "kind": df["INSTRUMENT"].map({"OPTIDX": "OPT", "FUTIDX": "FUT"}),
            "expiry": pd.to_datetime(df["EXPIRY_DT"], format="%d-%b-%Y").dt.date, "strike": df["STRIKE_PR"].astype(float),
            "side": df["OPTION_TYP"], "open": df["OPEN"], "high": df["HIGH"], "low": df["LOW"], "close": df["CLOSE"],
            "settle": df["SETTLE_PR"], "oi": df["OPEN_INT"], "chg_oi": df["CHG_IN_OI"], "volume": df["CONTRACTS"],
            "underlying": float("nan")})
    out.insert(0, "date", d)
    return out[COLS].reset_index(drop=True)


class Bhavcopy:
    def __init__(self, data_dir, log=print):
        self.dir = eod_dir(data_dir)
        self.log = log
        self.state_f = self.dir / "_done.json"
        self.done = json.loads(self.state_f.read_text()) if self.state_f.exists() else {"ok": [], "missing": []}
        self.lock = threading.Lock()

    def status(self) -> dict:
        ok = sorted(self.done["ok"])
        return {"days": len(ok), "first": ok[0] if ok else None, "last": ok[-1] if ok else None,
                "mb": round(sum(f.stat().st_size for f in self.dir.glob("fo_*.parquet")) / 1e6, 1)}

    def _fetch(self, d: dt.date):
        import requests
        for k in range(3):
            try:
                r = requests.get(url_for(d), headers={"User-Agent": UA, "Referer": "https://www.nseindia.com/"}, timeout=30)
                if r.status_code == 404:
                    return d, None, 404
                if r.status_code == 200 and r.content[:2] == b"PK":
                    return d, parse(r.content, d), 200
                time.sleep(2 * (k + 1))
            except Exception:
                time.sleep(2 * (k + 1))
        return d, None, 0

    def download(self, start: dt.date, end: dt.date | None = None, should_stop=lambda: False,
                 progress=lambda a, b: None, workers: int = 3):
        end = end or dt.date.today() - dt.timedelta(days=1)
        have = set(self.done["ok"]) | set(self.done["missing"])
        days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
        todo = [d for d in days if d.weekday() < 5 and d.isoformat() not in have]
        if not todo:
            self.log("End-of-day history is up to date.")
            return
        self.log(f"Downloading NSE end-of-day F&O files: {len(todo):,} days to check ({todo[0]} → {todo[-1]})")
        by_year: dict[int, list] = {}
        n = 0
        with ThreadPoolExecutor(workers) as ex:
            for i in range(0, len(todo), 60):                 # batches, so a stop request is honoured quickly
                if should_stop():
                    break
                for d, df, code in ex.map(self._fetch, todo[i:i + 60]):
                    n += 1
                    if code == 200 and df is not None:
                        by_year.setdefault(d.year, []).append(df)
                        self.done["ok"].append(d.isoformat())
                    elif code == 404:
                        self.done["missing"].append(d.isoformat())
                progress(n, len(todo))
                self._flush(by_year)
                by_year = {}
                time.sleep(0.5)
        self._flush(by_year)
        st = self.status()
        self.log(f"End-of-day history: {st['days']:,} trading days ({st['first']} → {st['last']}), {st['mb']} MB")

    def _flush(self, by_year):
        for y, parts in by_year.items():
            f = self.dir / f"fo_{y}.parquet"
            new = pd.concat(parts, ignore_index=True)
            if f.exists():
                old = pd.read_parquet(f)
                new = pd.concat([old, new], ignore_index=True).drop_duplicates(["date", "sym", "kind", "expiry", "strike", "side"])
            new.sort_values(["date", "sym", "expiry", "strike", "side"]).to_parquet(f, index=False, compression="zstd")
        with self.lock:
            self.done["ok"] = sorted(set(self.done["ok"]))
            self.done["missing"] = sorted(set(self.done["missing"]))
            self.state_f.write_text(json.dumps(self.done))

    def load(self, sym: str, start: dt.date | None = None) -> pd.DataFrame:
        parts = []
        for f in sorted(self.dir.glob("fo_*.parquet")):
            if start and int(f.stem[3:]) < start.year:
                continue
            d = pd.read_parquet(f)
            parts.append(d[d["sym"] == sym])
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=COLS)
