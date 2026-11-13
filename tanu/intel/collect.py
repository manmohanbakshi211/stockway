"""Reads the public sources (sources.json) into data/tanu/intel/<id>.json, each in isolation: a source that fails is
recorded as failed with its error (its last good data stays on the page, marked stale) and the others carry on.

Display only (ADR-0012): alerts for new items (a big insider trade, a red-flag filing, a high-impact headline, the
day's FII / DII figures, a large world move) go to the alert book that the Market page shows; nothing here feeds a
setup, an account or a broker.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..core import log
from . import parsers as P
from .http import NSE_ARCHIVES, Http

HERE = Path(__file__).parent
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
FF_WEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NSEIX = "https://www.nseix.com/api/derivatives-watch?inst_type1=IDX&inst_type2=STK&type=live"
INDEX_TICKERS = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
                 "MIDCPNIFTY": "NIFTY_MID_SELECT.NS", "SENSEX": "^BSESN", "INDIAVIX": "^INDIAVIX"}
_log = log.get("intel")


def _load(name: str) -> dict:
    return json.loads((HERE / name).read_text())


def yahoo_ticker(sym: str) -> str:
    s = sym.strip().upper()
    return INDEX_TICKERS.get(s, s if s.startswith("^") or "." in s or "=" in s else s + ".NS")


class Collector:
    def __init__(self, base: Path | str, http: Http | None = None, now=time.time, on_alert=None):
        self.dir = Path(base) / "intel"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.http = http or Http()
        self.now = now
        self.on_alert = on_alert
        self.spec = _load("sources.json")
        self.world_spec = _load("world.json")
        self.feeds = _load("feeds.json")
        self.busy: set[str] = set()
        self.lock = threading.Lock()
        self.fetch = {"world": self._world, "calendar": self._calendar, "gift_nifty": self._gift, "fii_dii": self._fii_dii,
                      "participant_oi": self._participant_oi, "ban": self._ban, "deals": self._deals,
                      "insiders": self._insiders, "events": self._events, "filings": self._filings,
                      "news_india": lambda prev: self._news("india", prev), "news_global": lambda prev: self._news("global", prev),
                      "watchlist": self._watchlist}

    # -------------------------------------------------------------- cache
    def _file(self, sid: str) -> Path:
        return self.dir / f"{sid}.json"

    def cached(self, sid: str) -> dict:
        try:
            return json.loads(self._file(sid).read_text())
        except Exception:
            return {"id": sid, "ok": None, "at": None, "data": None, "error": None}

    def _write(self, sid: str, rec: dict) -> None:
        p = self._file(sid)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, default=str))
        tmp.replace(p)

    def sources(self) -> dict:
        return {k: v for k, v in self.spec["sources"].items() if v.get("on", True) and k in self.fetch}

    def due(self, sid: str) -> bool:
        c = self.cached(sid)
        every = self.spec["sources"].get(sid, {}).get("every_s", 600)
        last = c.get("tried_at") or c.get("at") or 0
        return self.now() - float(last) >= every

    # -------------------------------------------------------------- refresh
    def refresh(self, ids=None, force: bool = False) -> dict[str, bool]:
        """Reads the given sources (all switched-on ones by default) that are due; returns {id: ok}."""
        out = {}
        for sid in ids or list(self.sources()):
            if sid not in self.sources() or not (force or self.due(sid)):
                continue
            with self.lock:
                if sid in self.busy:
                    continue
                self.busy.add(sid)
            try:
                out[sid] = self._one(sid)
            finally:
                with self.lock:
                    self.busy.discard(sid)
        return out

    def refresh_async(self, ids, force: bool = False) -> list[str]:
        """Starts a background read for the due sources among ids (one at a time per source); returns their ids."""
        todo = [s for s in ids if s in self.sources() and s not in self.busy and (force or self.due(s))]
        if todo:
            threading.Thread(target=self.refresh, args=(todo, force), daemon=True, name="tanu-intel").start()
        return todo

    def run_forever(self, stop: threading.Event, every_s: float = 30.0) -> None:
        """The supervisor's reader after the switch-over: every source on its own schedule until stop is set."""
        while not stop.is_set():
            try:
                self.refresh()
            except Exception as e:                       # never let the reader thread die
                _log.warning("intel_loop_failed", error=str(e)[:200])
            stop.wait(every_s)

    def _one(self, sid: str) -> bool:
        prev = self.cached(sid)
        t0, m0 = self.now(), time.monotonic()
        try:
            data = self.fetch[sid](prev.get("data"))
            rec = {"id": sid, "ok": True, "at": t0, "tried_at": t0, "data": data, "error": None,
                   "took_s": round(time.monotonic() - m0, 2)}
            self._write(sid, rec)
            self._alerts(sid, prev.get("data"), data)
            _log.info("intel_read", source=sid, took_s=rec["took_s"])
            return True
        except Exception as e:                           # a bullet never breaks the gun: record, keep the old data
            rec = {**prev, "id": sid, "ok": False, "tried_at": t0, "error": f"{type(e).__name__}: {str(e)[:200]}"}
            self._write(sid, rec)
            _log.warning("intel_failed", source=sid, error=rec["error"])
            return False

    # -------------------------------------------------------------- sources
    def _charts(self, tickers: list[str]) -> dict:
        def one(t):
            try:
                return t, P.yahoo_chart(self.http.json(YAHOO + urllib.parse.quote(t, safe=""),
                                                       params={"range": "1d", "interval": "2m"}, timeout=8))
            except Exception:
                return t, None
        with ThreadPoolExecutor(max_workers=8) as ex:
            got = {t: r for t, r in ex.map(one, tickers) if r}
        return got

    def _world(self, prev):
        w = self.world_spec
        ticks = [t for items in w["groups"].values() for _, t, _ in items]
        adr_nse = [nse + ".NS" for nse, _ in w.get("adr", {}).values()]
        got = self._charts(list(dict.fromkeys(ticks + adr_nse)))
        if len(got) < max(3, len(ticks) // 3):
            raise RuntimeError(f"only {len(got)} of {len(ticks)} markets answered")
        old = {r["ticker"]: r for g in (prev or {}).get("groups", []) for r in g["rows"]}
        groups = []
        for g, items in w["groups"].items():
            rows = []
            for name, t, region in items:
                c = got.get(t) or ({**old[t], "stale": True} if t in old else None)
                if c:
                    rows.append({**c, "name": name, "ticker": t, "region": region})
            groups.append({"group": g, "rows": rows})
        usd = (got.get("INR=X") or {}).get("last")
        adr = []
        for t, (nse, ratio) in w.get("adr", {}).items():
            a, n = got.get(t), got.get(nse + ".NS")
            if a and n and usd:
                implied = a["last"] * usd / ratio
                adr.append({"adr": t, "nse": nse, "implied_inr": round(implied, 2), "nse_inr": n["last"],
                            "premium_pct": round((implied / n["last"] - 1) * 100, 2), "adr_chg_pct": a["chg_pct"]})
        return {"groups": groups, "adr": adr, "answered": len(got), "of": len(ticks)}

    def _calendar(self, prev):
        return {"events": P.ff_calendar(self.http.json(FF_WEEK, headers={"Accept": "application/json"}))}

    def _gift(self, prev):
        d = P.gift_nifty(self.http.json(NSEIX, headers={"Referer": "https://www.nseix.com/", "Accept": "application/json"}))
        if not d:
            raise RuntimeError("no NIFTY future in the NSE IX answer")
        return d

    def _fii_dii(self, prev):
        d = P.fii_dii(self.http.nse("/api/fiidiiTradeReact"))
        if not d:
            raise RuntimeError("empty FII / DII answer")
        hist = [h for h in (prev or {}).get("history", []) if h.get("date") != (d.get("FII") or {}).get("date")]
        if d.get("FII"):
            hist = ([{"date": d["FII"]["date"], "fii": d["FII"]["net"], "dii": (d.get("DII") or {}).get("net")}] + hist)[:30]
        return {**d, "history": hist}

    def _latest_archive(self, fmt: str, parse, days: int = 7, before: dt.date | None = None):
        d = before or dt.datetime.fromtimestamp(self.now(), IST).date()
        for i in range(days):
            day = d - dt.timedelta(days=i)
            if day.weekday() >= 5:
                continue
            try:
                out = parse(self.http.get(NSE_ARCHIVES + fmt.format(d=day)))
            except Exception:
                out = None
            if out:
                return day, out
        return None, None

    def _participant_oi(self, prev):
        parse = lambda b: P.participant_csv(b.decode("utf-8", "replace"))
        fmt = "/content/nsccl/fao_participant_oi_{d:%d%m%Y}.csv"
        day, today = self._latest_archive(fmt, parse)
        if not today:
            raise RuntimeError("no participant OI file in the last 7 days")
        _, before = self._latest_archive(fmt, parse, before=day - dt.timedelta(days=1))
        return P.participant_oi(today, before, day.isoformat())

    def _ban(self, prev):
        day, data = self._latest_archive("/archives/nsccl/mwpl/combineoi_{d:%d%m%Y}.zip", P.mwpl_zip)
        if not data:
            raise RuntimeError("no position-limit file in the last 7 days")
        return {"date": day.isoformat(), "symbols": data,
                "ban": sorted(s for s, x in data.items() if x["ban"]),
                "near": sorted(((s, x["pct"]) for s, x in data.items() if not x["ban"] and x["pct"] >= 80),
                               key=lambda r: -r[1])}

    def _deals(self, prev):
        out = []
        for kind, path in (("Bulk", "/content/equities/bulk.csv"), ("Block", "/content/equities/block.csv")):
            out += P.deals_csv(self.http.text(NSE_ARCHIVES + path), kind)
        return {"deals": sorted(out, key=lambda d: -d["value_cr"])[:200]}

    def _events(self, prev):
        rows = []
        for path in ("/api/event-calendar?index=equities", "/api/corporate-board-meetings?index=equities"):
            try:
                rows = P.events(self.http.nse(path))
            except Exception:
                rows = []
            if rows:
                break
        if not rows:
            raise RuntimeError("no events from NSE")
        return {"events": rows}

    def _filings(self, prev):
        rows = P.filings(self.http.nse("/api/corporate-announcements?index=equities"), self.feeds["filing_rules"],
                         self.feeds["filing_skip"])
        old = (prev or {}).get("items", [])
        seen = {x["seq"] for x in old}
        new = [x for x in rows if x["seq"] not in seen]
        return {"items": (new + old)[:300], "new": [x["seq"] for x in new]}

    def fo_symbols(self) -> set[str]:
        """F&O stocks: from the broker's public instrument list if Tanu has one, else from NSE's position-limit file."""
        files = sorted((self.dir.parent / "instruments").glob("*_NSE.json.gz"))
        if files:
            try:
                from ..data import instruments
                return {s for s, u in instruments.underlyings(instruments.load(files[-1])).items() if not u["is_index"]}
            except Exception:
                pass
        return set(((self.cached("ban").get("data") or {}).get("symbols") or {}))

    def _insiders(self, prev):
        cfg = self.spec["sources"]["insiders"]
        prev = prev or {}
        rows = {x["pid"]: x for x in prev.get("rows", [])}
        done = set(prev.get("done", []))
        today = dt.datetime.fromtimestamp(self.now(), IST).date()
        start = today - dt.timedelta(days=cfg.get("first_read_days", 30) if not prev else 10)
        r = self.http.nse(f"/api/corporates-pit-gg?index=equities&from_date={start:%d-%m-%Y}&to_date={today:%d-%m-%Y}")
        lst = (r.get("data") if isinstance(r, dict) else r) or []
        fo = self.fo_symbols()
        lst = [m for m in lst if isinstance(m, dict) and m.get("xmlFileName") and str(m.get("appId")) not in done
               and (not fo or m.get("symbol") in fo)]
        lst.sort(key=lambda m: P._date(m.get("broadcastDateTime")) or dt.datetime.min, reverse=True)
        fresh = []
        for m in lst[:cfg.get("max_filings_per_read", 150)]:
            try:
                got = P.insider_xbrl(self.http.text(m["xmlFileName"]), m)
            except Exception:
                continue
            if m.get("prevAppId"):                        # a revision replaces the earlier filing
                for k in [k for k in rows if k.startswith(f"g{m['prevAppId']}:")]:
                    rows.pop(k, None)
            for x in got:
                if x["pid"] not in rows:
                    fresh.append(x["pid"])
                rows[x["pid"]] = x
            done.add(str(m["appId"]))
        keep = sorted(rows.values(), key=lambda x: x.get("filed") or "", reverse=True)[:5000]
        return {"rows": keep, "done": sorted(done)[-20000:], "new": fresh, "left": max(0, len(lst) - len(fresh)),
                "show_min_rs": cfg.get("show_min_rs", 5e6)}

    def _news(self, which: str, prev):
        old = (prev or {}).get("items", [])
        seen = {x["title"] for x in old}
        new, errors = [], {}
        for src, url in self.feeds[which].items():
            try:
                for it in P.rss(self.http.get(url), src):
                    if it["title"] not in seen:
                        seen.add(it["title"])
                        new.append({**it, **P.tag_headline(it["title"], self.feeds), "ts": it["ts"] or self.now()})
            except Exception as e:
                errors[src] = str(e)[:120]
        if len(errors) == len(self.feeds[which]):
            raise RuntimeError("every feed failed: " + "; ".join(f"{k}: {v}" for k, v in errors.items()))
        items = sorted(new + old, key=lambda x: -(x.get("ts") or 0))[:self.feeds.get("keep", 300)]
        return {"items": items, "new": [x["title"] for x in new], "errors": errors}

    # -------------------------------------------------------------- watchlist
    def watch_file(self) -> Path:
        return self.dir / "watchlist_symbols.json"

    def watch_symbols(self) -> list[str]:
        try:
            return json.loads(self.watch_file().read_text())["symbols"]
        except Exception:
            return ["NIFTY", "BANKNIFTY", "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY"]

    def set_watch(self, symbols: list[str]) -> list[str]:
        clean = []
        for s in symbols:
            s = str(s).strip().upper()
            if s and len(s) <= 20 and all(c.isalnum() or c in "&-_" for c in s) and s not in clean:
                clean.append(s)
        clean = clean[:40]
        self.watch_file().write_text(json.dumps({"symbols": clean}))
        return clean

    def _watchlist(self, prev):
        syms = self.watch_symbols()
        got = self._charts([yahoo_ticker(s) for s in syms])
        if syms and not got:
            raise RuntimeError("no quotes answered")
        return {"quotes": {s: got.get(yahoo_ticker(s)) for s in syms}}

    # -------------------------------------------------------------- display-only alerts for new items
    def _alerts(self, sid: str, prev, data) -> None:
        if not self.on_alert or not prev:                # the first read is the starting point, not news
            return
        try:
            for a in alerts_for(sid, prev, data, self.spec["sources"], self.world_spec):
                self.on_alert(a)
        except Exception as e:
            _log.warning("intel_alert_failed", source=sid, error=str(e)[:200])


def alerts_for(sid: str, prev: dict, data: dict, cfg: dict, world: dict) -> list[dict]:
    out = []
    if sid == "insiders":
        lim = cfg.get("insiders", {}).get("alert_min_rs", 1e7)
        new = set(data.get("new", []))
        for x in data.get("rows", []):
            if x["pid"] in new and P.meaningful_insider(x, lim):
                d = "up" if x["side"] == "Buy" else "down"
                out.append({"sym": x["sym"], "kind": "insider", "direction": d, "severity": 2,
                            "title": f"{x['category'] or 'Insider'} {x['side'].lower()}s ₹{x['value'] / 1e7:,.1f} Cr of {x['sym']}",
                            "detail": f"{x['person']}, traded {x.get('traded')}, filed {x.get('filed')}.", "data": {"pid": x["pid"]}})
    elif sid == "filings":
        new = set(data.get("new", []))
        for x in data.get("items", []):
            if x["seq"] in new and x["severity"] >= 2:
                d = "up" if x["tone"] > 0 else "down" if x["tone"] < 0 else "neutral"
                out.append({"sym": x["sym"], "kind": "filing", "direction": d, "severity": 2,
                            "title": f"{x['sym']}: {x['label']}", "detail": (x.get("desc") or "")[:200],
                            "data": {"link": x.get("link")}})
    elif sid in ("news_india", "news_global"):
        new = set(data.get("new", []))
        for x in data.get("items", []):
            if x["title"] in new and x.get("impact") == "high":
                d = "up" if x["tone"] > 0 else "down" if x["tone"] < 0 else "neutral"
                out.append({"sym": "MARKET", "kind": "news", "direction": d, "severity": 1, "title": x["title"],
                            "detail": x["source"], "data": {"link": x["link"]}})
    elif sid == "fii_dii":
        f, p = data.get("FII") or {}, (prev or {}).get("FII") or {}
        if f.get("date") and f.get("date") != p.get("date"):
            dii = (data.get("DII") or {}).get("net")
            net = (f.get("net") or 0) + (dii or 0)
            out.append({"sym": "MARKET", "kind": "fii_dii", "direction": "up" if net > 0 else "down", "severity": 1,
                        "title": f"FII ₹{f.get('net', 0):,.0f} Cr, DII ₹{dii or 0:,.0f} Cr ({f['date']})",
                        "detail": "Provisional cash-market figures from NSE.", "data": {}})
    elif sid == "world":
        thr = world.get("alert_moves", {})
        was = {r["ticker"]: r.get("chg_pct") for g in (prev or {}).get("groups", []) for r in g["rows"]}
        for g in data.get("groups", []):
            for r in g["rows"]:
                t = r["ticker"]
                if t not in thr or r.get("stale") or r.get("chg_pct") is None:
                    continue
                lvl, before = int(abs(r["chg_pct"]) // thr[t]), int(abs(was.get(t) or 0) // thr[t])
                if lvl >= 1 and lvl > before:
                    out.append({"sym": "GLOBAL", "kind": "macro", "direction": "up" if r["chg_pct"] > 0 else "down",
                                "severity": 2 if lvl >= 2 else 1, "title": f"{r['name']} {r['chg_pct']:+.2f}% today",
                                "detail": f"Now {r['last']:,.2f}.", "data": {"ticker": t}})
    return out
