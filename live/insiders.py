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
    for fmt in ("%d-%b-%Y %H:%M", "%d-%b-%Y", "%d-%m-%Y"):
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


class Insiders:
    def __init__(self, engine):
        self.e = engine
        self.rows = {}                 # pid -> row
        self.seen = set(engine.store.get("insiders:seen", []) or [])
        self.updated = None
        self.running = False
        self.results_cache = {}

    def refresh(self, symbols, log=print, pause=0.6):
        """One request per stock: its latest 20 disclosures."""
        if self.running:
            return
        self.running = True
        nse = self.e.context.nse
        new_alerts = []
        try:
            for i, sym in enumerate(sorted(symbols)):
                try:
                    d = nse.api(f"/api/corporates-pit?index=equities&symbol={sym}") or {}
                except Exception:
                    time.sleep(pause * 3)
                    continue
                for r in (d.get("data") if isinstance(d, dict) else d) or []:
                    x = normalise(r)
                    if not x["pid"]:
                        continue
                    self.rows[x["pid"]] = x
                    if x["pid"] not in self.seen:
                        self.seen.add(x["pid"])
                        if self.updated is not None and meaningful(x) and x["value"] >= ALERT_MIN_RS:
                            new_alerts.append(x)
                time.sleep(pause)
            self.updated = time.time()
            self.e.store.put("insiders:seen", sorted(self.seen)[-20000:])
            log(f"Insider trades: {len(self.rows):,} disclosures read, {len(new_alerts)} new big ones")
        finally:
            self.running = False
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
        return {"rows": rows[:400], "net": net[:60], "updated": self.updated, "days": days,
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
