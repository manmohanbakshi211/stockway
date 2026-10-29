"""Insider and promoter trades (SEBI insider-trading disclosures on NSE) and quarterly results, for F&O stocks.

India's version of tracking what insiders do: under SEBI's insider-trading rules, promoters, directors and key
managers must disclose their trades within two trading days. NSE publishes them. This module reads them for every
F&O stock, keeps the meaningful ones (promoters, directors and key managers trading in the market, above a size),
and raises an alert for each new one. It also reads each company's last five quarterly results from NSE.

A disclosure is public information filed after the trade; it is not a tip and not inside information.
"""
from __future__ import annotations

import datetime as dt
import time

from .clock import IST

KEY_PEOPLE = ("Promoter", "Promoter Group", "Director", "Key Managerial Personnel", "KMP", "Designated Person")
ALERT_MIN_RS = 1e7          # ₹1 crore
SHOW_MIN_RS = 5e6           # ₹50 lakh


def _num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _date(s):
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(str(s), fmt)
        except (TypeError, ValueError):
            continue
    return None


def normalise(r: dict) -> dict:
    typ = (r.get("tdpTransactionType") or "").strip()
    buy = typ.lower().startswith("buy") or (_num(r.get("buyValue")) > 0 and not _num(r.get("sellValue")))
    value = _num(r.get("secVal")) or _num(r.get("buyValue")) or _num(r.get("sellValue"))
    cat = (r.get("personCategory") or "").strip()
    mode = (r.get("acqMode") or "").strip()
    d = _date(r.get("date")) or _date(r.get("intimDt"))
    t0, t1 = _date(r.get("acqfromDt")), _date(r.get("acqtoDt"))
    return {"pid": str(r.get("pid") or r.get("did") or ""), "sym": r.get("symbol"), "company": r.get("company"),
            "person": (r.get("acqName") or "").strip(), "category": cat, "side": "Buy" if buy else "Sell" if typ else typ,
            "mode": mode, "qty": _num(r.get("secAcq")), "value": value, "sec_type": r.get("secType"),
            "before_pct": _num(r.get("befAcqSharesPer")), "after_pct": _num(r.get("afterAcqSharesPer")),
            "traded": t1.date().isoformat() if t1 else (t0.date().isoformat() if t0 else None),
            "filed": d.isoformat(timespec="minutes") if d else None,
            "days_to_file": (d.date() - t1.date()).days if d and t1 else None, "xbrl": r.get("xbrl"),
            "key": any(k.lower() in cat.lower() for k in KEY_PEOPLE),
            "market": mode.lower().startswith("market") or mode.lower() in ("market purchase", "market sale")}


def meaningful(x: dict) -> bool:
    return x["key"] and x["market"] and x["value"] >= SHOW_MIN_RS and x["side"] in ("Buy", "Sell")


def _pct(v):
    """New-format XBRL gives holdings as a fraction (0.0123 = 1.23%)."""
    x = _num(v)
    return round(x * 100, 4) if x <= 1 else x


def parse_pit_xml(text: str, meta: dict) -> list:
    """Rows from one NSE PIT XBRL filing (the format NSE uses from May 2026). One filing can hold several trades."""
    import re
    main, per = {}, {}
    for tag, ctx, val in re.findall(r'<in-bse-co:([A-Za-z]+)\s+contextRef="([^"]+)"[^>]*>([^<]*)<', text):
        (main if ctx == "MainI" else per.setdefault(ctx, {}))[tag] = val.strip()
    filed = _date(meta.get("broadcastDateTime")) or _date(meta.get("exchdisstime"))
    rows = []
    for ctx, f in per.items():
        typ = f.get("SecuritiesAcquiredOrDisposedTransactionType", "")
        side = "Buy" if typ.lower() == "buy" else "Sell" if typ.lower() == "sell" else typ
        cat = f.get("CategoryOfPerson", "")
        mode = f.get("ModeOfAcquisitionOrDisposal", "")
        t1 = f.get("DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyToDate") or \
            f.get("DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyFromDate")
        try:
            traded = dt.date.fromisoformat(t1) if t1 else None
        except ValueError:
            traded = None
        rows.append({"pid": f"g{meta.get('appId')}:{ctx}", "app": str(meta.get("appId")),
                     "sym": meta.get("symbol") or main.get("Symbol"),
                     "company": meta.get("companyName") or main.get("NameOfTheCompany"),
                     "person": f.get("NameOfThePerson", ""), "category": cat, "side": side, "mode": mode,
                     "qty": _num(f.get("SecuritiesAcquiredOrDisposedNumberOfSecurity")),
                     "value": _num(f.get("SecuritiesAcquiredOrDisposedValueOfSecurity")),
                     "sec_type": f.get("TypeOfInstrument"),
                     "before_pct": _pct(f.get("SecuritiesHeldPriorToAcquisitionOrDisposalPercentageOfShareholding")),
                     "after_pct": _pct(f.get("SecuritiesHeldPostAcquistionOrDisposalPercentageOfShareholding")),
                     "traded": traded.isoformat() if traded else None,
                     "filed": filed.isoformat(timespec="minutes") if filed else None,
                     "days_to_file": (filed.date() - traded).days if filed and traded else None,
                     "xbrl": meta.get("ixbrl") or meta.get("xmlFileName"),
                     "key": any(k.lower() in cat.lower() for k in KEY_PEOPLE),
                     "market": mode.lower().startswith("market")})
    return rows


NEW_FEED_FROM = dt.date(2026, 5, 1)      # NSE moved PIT disclosures to XBRL filings ('corporates-pit-gg') here


class Insiders:
    """Reads NSE's market-wide insider-disclosure list by date, keeps F&O stocks, and reads each new filing once.

    From May 2026 NSE lists filings in 'corporates-pit-gg' (details in one small XBRL file per filing); older
    disclosures are in the archive feed ('corporates-pit'). Parsed rows are kept on disk, so after the first read
    each refresh only fetches the last ten days.
    """

    def __init__(self, engine):
        self.e = engine
        st = engine.store
        self.rows = {x["pid"]: x for x in (st.get("insiders:rows", []) or []) if x.get("pid")}
        self.done = set(st.get("insiders:done", []) or [])        # filings already read
        self.seen = set(st.get("insiders:seen", []) or [])
        self.updated = st.get("insiders:updated")
        self.seeded = bool(self.updated)                          # alerts only once a first read exists
        self.running = False
        self.progress = None
        self.results_cache = {}

    def _list(self, path, a: dt.date, b: dt.date, chunk=30):
        out, d = [], a
        nse = self.e.context.nse
        while d <= b:
            e = min(b, d + dt.timedelta(days=chunk - 1))
            try:
                r = nse.api(f"{path}&from_date={d:%d-%m-%Y}&to_date={e:%d-%m-%Y}") or {}
                out += (r.get("data") if isinstance(r, dict) else r) or []
            except Exception:
                pass
            d = e + dt.timedelta(days=1)
            time.sleep(0.5)
        return out

    def _save(self):
        st = self.e.store
        keep = sorted(self.rows.values(), key=lambda x: x.get("filed") or "", reverse=True)[:8000]
        st.put("insiders:rows", keep)
        st.put("insiders:done", sorted(self.done)[-30000:])
        st.put("insiders:seen", sorted(self.seen)[-30000:])

    def refresh(self, symbols, log=print, pause=0.35, history_days=365):
        if self.running:
            return []
        self.running = True
        fo = set(symbols)
        nse = self.e.context.nse
        today = dt.datetime.now(IST).date()
        first = not self.updated
        start = today - dt.timedelta(days=history_days if first else 10)
        new_alerts, fresh = [], []
        try:
            # 1. archive feed (before May 2026): the rows come complete
            if start < NEW_FEED_FROM:
                self.progress = "Reading the archive (before May 2026)"
                for r in self._list("/api/corporates-pit?index=equities", start, NEW_FEED_FROM - dt.timedelta(days=1)):
                    if r.get("symbol") in fo:
                        x = normalise(r)
                        if x["pid"]:
                            self.rows[x["pid"]] = x
                            self.seen.add(x["pid"])
            # 2. current feed: list, then one XBRL file per new filing (newest first)
            lst = self._list("/api/corporates-pit-gg?index=equities", max(start, NEW_FEED_FROM), today)
            lst = [m for m in lst if m.get("symbol") in fo and m.get("xmlFileName") and str(m.get("appId")) not in self.done]
            lst.sort(key=lambda m: _date(m.get("broadcastDateTime")) or dt.datetime.min, reverse=True)
            for i, m in enumerate(lst):
                self.progress = f"Reading filings {i + 1}/{len(lst)}"
                try:
                    rows = parse_pit_xml(nse.s.get(m["xmlFileName"], timeout=30).text, m)
                except Exception:
                    time.sleep(pause * 3)
                    continue
                if m.get("prevAppId"):                       # a revision replaces the earlier filing
                    old = f"g{m['prevAppId']}:"
                    for k in [k for k in self.rows if k.startswith(old)]:
                        self.rows.pop(k, None)
                for x in rows:
                    self.rows[x["pid"]] = x
                    if x["pid"] not in self.seen:
                        self.seen.add(x["pid"])
                        fresh.append(x)
                self.done.add(str(m["appId"]))
                if i % 50 == 49:
                    self._save()
                time.sleep(pause)
            recent = (today - dt.timedelta(days=4)).isoformat()
            if self.seeded:
                new_alerts = [x for x in fresh if meaningful(x) and x["value"] >= ALERT_MIN_RS and (x["filed"] or "") >= recent]
            self.seeded = True
            self.updated = time.time()
            self.e.store.put("insiders:updated", self.updated)
            self._save()
            log(f"Insider trades: {len(lst)} new filings read ({len(self.rows):,} disclosures kept), {len(new_alerts)} new big ones")
        finally:
            self.running = False
            self.progress = None
        return new_alerts

    def view(self, days=60, all_rows=False) -> dict:
        cut = (dt.datetime.now(IST) - dt.timedelta(days=days)).date().isoformat()
        rows = [x for x in self.rows.values() if (x["filed"] or "") >= cut and (all_rows or meaningful(x))]
        rows.sort(key=lambda x: x["filed"] or "", reverse=True)
        by = {}
        for x in rows:
            if not meaningful(x):
                continue
            b = by.setdefault(x["sym"], {"sym": x["sym"], "company": x["company"], "buy": 0.0, "sell": 0.0, "n": 0, "people": set()})
            b["buy" if x["side"] == "Buy" else "sell"] += x["value"]
            b["n"] += 1
            b["people"].add(x["person"])
        net = sorted(({**b, "net": b["buy"] - b["sell"], "people": len(b["people"])} for b in by.values()),
                     key=lambda b: -abs(b["net"]))
        return {"rows": rows[:400], "net": net[:60], "updated": self.updated, "days": days, "progress": self.progress,
                "running": self.running,
                "note": "Disclosures under SEBI's insider-trading rules: filed after the trade (usually within 2 trading days). Public information, not a tip."}

    def for_symbol(self, sym, days=365) -> list:
        cut = (dt.datetime.now(IST) - dt.timedelta(days=days)).date().isoformat()
        return sorted((x for x in self.rows.values() if x["sym"] == sym and (x["filed"] or "") >= cut and x["key"]),
                      key=lambda x: x["filed"] or "", reverse=True)[:30]

    # ------------------------------------------------------------ results
    def results(self, sym, quarters=6) -> dict:
        """Last quarterly results from NSE's integrated filings (XBRL), consolidated where filed."""
        c = self.results_cache.get(sym)
        if c and time.time() - c[0] < 12 * 3600:
            return c[1]
        nse = self.e.context.nse
        d = nse.api(f"/api/integrated-filing-results?index=equities&symbol={sym}") or {}
        fins = [x for x in (d.get("data") or []) if "Financial" in (x.get("type") or "") and x.get("xbrl")]
        pick = {}
        for x in fins:                                  # one filing per quarter: consolidated first, newest version
            q = _date((x.get("qe_Date") or "").title())
            if not q:
                continue
            k = q.date().isoformat()
            rank = (1 if (x.get("consolidated") or "").startswith("Consolidated") else 0, x.get("creation_Date") or "")
            if k not in pick or rank > pick[k][0]:
                pick[k] = (rank, x)
        out_q = []
        for k in sorted(pick, reverse=True)[:quarters]:
            x = pick[k][1]
            try:
                facts = parse_xbrl(nse.s.get(x["xbrl"], timeout=30).text)
            except Exception:
                continue
            rev, pat = facts.get("RevenueFromOperations"), facts.get("ProfitLossForPeriod")
            out_q.append({"quarter_end": k, "basis": "consolidated" if pick[k][0][0] else "standalone",
                          "revenue_cr": round(rev / 1e7, 1) if rev is not None else None,
                          "profit_cr": round(pat / 1e7, 1) if pat is not None else None,
                          "eps": facts.get("eps"), "margin_pct": round(pat / rev * 100, 1) if rev and pat is not None else None,
                          "audited": facts.get("audited"), "filed": x.get("creation_Date")})
        out = {"sym": sym, "quarters": out_q,
               "note": "From the company's results filed with NSE (integrated filing, XBRL). ₹ crore."}
        by = {q["quarter_end"]: q for q in out_q}
        if out_q:
            a = out_q[0]
            ly = (dt.date.fromisoformat(a["quarter_end"]).replace(year=dt.date.fromisoformat(a["quarter_end"]).year - 1)).isoformat()
            b = by.get(ly)
            if b and b["basis"] == a["basis"]:
                out["yoy"] = {"revenue_pct": round((a["revenue_cr"] / b["revenue_cr"] - 1) * 100, 1) if b.get("revenue_cr") else None,
                              "profit_pct": round((a["profit_cr"] / b["profit_cr"] - 1) * 100, 1) if (b.get("profit_cr") or 0) > 0 else None}
        self.results_cache[sym] = (time.time(), out)
        return out


def parse_xbrl(text: str) -> dict:
    """Current-quarter facts (context 'OneD') from an NSE integrated-filing XBRL file. Values in rupees."""
    import re
    out = {}
    for tag, ctx, val in re.findall(r'<[a-zA-Z0-9-]+:([A-Za-z]+)\s+[^>]*contextRef="([^"]+)"[^>]*>([^<]{1,60})<', text):
        if ctx != "OneD":
            continue
        if tag in ("RevenueFromOperations", "ProfitLossForPeriod") and tag not in out:
            try:
                out[tag] = float(val)
            except ValueError:
                pass
        elif "DilutedEarnings" in tag and "eps" not in out:
            try:
                out["eps"] = float(val)
            except ValueError:
                pass
        elif tag == "WhetherResultsAreAuditedOrUnaudited":
            out["audited"] = val
    return out
