"""Pure parsers for the public sources (no network here; tests feed them saved samples). Each returns plain dicts and
lists, and turns an unknown shape into an empty result rather than an error where it can (a site changing its
format must not break the page). The formats follow the legacy app's readers (13 Nov 2026)."""
from __future__ import annotations

import csv
import datetime as dt
import html
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from email.utils import parsedate_to_datetime

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
KEY_PEOPLE = ("Promoter", "Promoter Group", "Director", "Key Managerial Personnel", "KMP", "Designated Person")


def num(x) -> float | None:
    try:
        if x in (None, "", "-"):
            return None
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _date(s) -> dt.datetime | None:
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y"):
        try:
            return dt.datetime.strptime(str(s).strip(), fmt)
        except (TypeError, ValueError):
            continue
    return None


# ------------------------------------------------------------------ Yahoo Finance chart
def yahoo_chart(d: dict) -> dict | None:
    """One market from Yahoo's v8 chart JSON (range 1d, 2-minute bars): last, previous close, % change, the day's line
    (at most ~120 points), day high / low, 52-week high / low, the quote time."""
    res = ((d or {}).get("chart") or {}).get("result") or []
    if not res:
        return None
    m = res[0].get("meta") or {}
    ts = res[0].get("timestamp") or []
    q = (((res[0].get("indicators") or {}).get("quote")) or [{}])[0]
    pts = [(a, c) for a, c in zip(ts, q.get("close") or []) if c is not None]
    last = num(m.get("regularMarketPrice")) or (pts[-1][1] if pts else None)
    prev = num(m.get("chartPreviousClose")) or num(m.get("previousClose"))
    if last is None or not prev:
        return None
    line = [float(c) for _, c in pts]
    step = max(1, len(line) // 120)
    hi = [x for x in (q.get("high") or []) if x is not None]
    lo = [x for x in (q.get("low") or []) if x is not None]
    return {"last": float(last), "prev": float(prev), "chg_pct": round((last / prev - 1) * 100, 3),
            "ts": num(m.get("regularMarketTime")) or (pts[-1][0] if pts else None),
            "line": line[::step] + ([float(last)] if line else []),
            "day_h": num(m.get("regularMarketDayHigh")) or (max(hi) if hi else None),
            "day_l": num(m.get("regularMarketDayLow")) or (min(lo) if lo else None),
            "hi52": num(m.get("fiftyTwoWeekHigh")), "lo52": num(m.get("fiftyTwoWeekLow")),
            "currency": m.get("currency")}


# ------------------------------------------------------------------ ForexFactory weekly calendar
def ff_calendar(rows, impacts=("High", "Medium")) -> list[dict]:
    out = []
    for x in rows if isinstance(rows, list) else []:
        if not isinstance(x, dict) or x.get("impact") not in impacts:
            continue
        try:
            t = dt.datetime.fromisoformat(str(x["date"])).astimezone(IST)
        except Exception:
            continue
        out.append({"time": t.isoformat(timespec="minutes"), "country": x.get("country"), "title": x.get("title"),
                    "impact": x.get("impact"), "forecast": x.get("forecast") or "", "previous": x.get("previous") or ""})
    return sorted(out, key=lambda e: e["time"])


# ------------------------------------------------------------------ NSE
def fii_dii(rows) -> dict:
    """NSE /api/fiidiiTradeReact: provisional cash-market buy / sell / net (₹ crore) for FII / FPI and DII."""
    out = {}
    for r in rows if isinstance(rows, list) else []:
        cat = str(r.get("category", ""))
        who = "FII" if ("FII" in cat or "FPI" in cat) else "DII" if "DII" in cat else None
        if not who:
            continue
        out[who] = {"date": r.get("date"), "buy": num(r.get("buyValue")), "sell": num(r.get("sellValue")),
                    "net": num(r.get("netValue"))}
    return out


def participant_csv(text: str) -> dict:
    """NSE's daily participant-wise open interest CSV -> {Client Type: {column: contracts}}."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    start = next((i for i, ln in enumerate(lines) if ln.startswith("Client Type")), None)
    if start is None:
        return {}
    out = {}
    for row in csv.DictReader(io.StringIO("\n".join(lines[start:]))):
        row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        who = row.pop("Client Type", None)
        if who:
            out[who] = {k: num(v) or 0.0 for k, v in row.items()}
    return out


def participant_oi(today: dict, prev: dict | None, day: str) -> dict:
    """Index futures and index options positioning per participant (FII, DII, Pro, Client), with the day's change."""
    def net(x, a, b):
        return (x.get(a) or 0) - (x.get(b) or 0)
    rows = []
    for who in ("FII", "DII", "Pro", "Client"):
        x = today.get(who)
        if not x:
            continue
        fl, fs = x.get("Future Index Long") or 0, x.get("Future Index Short") or 0
        r = {"who": who, "fut_long": fl, "fut_short": fs, "fut_net": fl - fs,
             "fut_long_pct": round(fl / (fl + fs) * 100, 1) if fl + fs else None,
             "call_net": net(x, "Option Index Call Long", "Option Index Call Short"),
             "put_net": net(x, "Option Index Put Long", "Option Index Put Short"),
             "stock_fut_net": net(x, "Future Stock Long", "Future Stock Short")}
        y = (prev or {}).get(who)
        if y:
            r["fut_net_chg"] = r["fut_net"] - net(y, "Future Index Long", "Future Index Short")
            r["call_net_chg"] = r["call_net"] - net(y, "Option Index Call Long", "Option Index Call Short")
            r["put_net_chg"] = r["put_net"] - net(y, "Option Index Put Long", "Option Index Put Short")
        rows.append(r)
    return {"date": day, "rows": rows}


def mwpl_zip(content: bytes) -> dict:
    """NSE's combined open interest file (zip with one CSV): each F&O stock's open interest as % of its market-wide
    position limit, and whether it is in the ban (no fresh positions next day)."""
    if content[:2] != b"PK":
        return {}
    z = zipfile.ZipFile(io.BytesIO(content))
    name = next((n for n in z.namelist() if n.lower().endswith(".csv")), None)
    if not name or z.getinfo(name).file_size > 20_000_000:
        return {}
    out = {}
    for row in csv.DictReader(io.StringIO(z.read(name).decode("utf-8", "ignore"))):
        row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        sym = row.get("NSE Symbol")
        mw = num(row.get("MWPL"))
        oi = num(row.get("Future Equivalent Open Interest")) or num(row.get("Open Interest")) or 0
        if sym and mw:
            out[sym] = {"pct": round(oi / mw * 100, 1), "ban": "no fresh" in (row.get("Limit for Next Day") or "").lower()}
    return out


def deals_csv(text: str, kind: str) -> list[dict]:
    """NSE bulk.csv / block.csv -> [{type, date, sym, client, side, qty, price, value_cr}]."""
    out = []
    for row in csv.DictReader(io.StringIO(text or "")):
        row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        qty, px = num(row.get("Quantity Traded")), num(row.get("Trade Price / Wght. Avg. Price"))
        if not row.get("Symbol") or qty is None or px is None:
            continue
        out.append({"type": kind, "date": row.get("Date"), "sym": row.get("Symbol"), "client": row.get("Client Name"),
                    "side": row.get("Buy/Sell"), "qty": qty, "price": px, "value_cr": round(qty * px / 1e7, 2)})
    return out


def events(data) -> list[dict]:
    """NSE event calendar / board meetings JSON -> [{sym, company, date, purpose, results}]."""
    if isinstance(data, dict):
        data = data.get("data") or data.get("rows") or []
    out = []
    for r in data if isinstance(data, list) else []:
        if not isinstance(r, dict):
            continue
        sym = r.get("symbol") or r.get("bm_symbol")
        d = _date(str(r.get("date") or r.get("bm_date") or "")[:11])
        purpose = str(r.get("purpose") or r.get("bm_purpose") or r.get("bm_desc") or "").strip()
        if sym and d:
            out.append({"sym": str(sym).strip(), "company": r.get("company") or r.get("sm_name"),
                        "date": d.date().isoformat(), "purpose": purpose[:80],
                        "results": bool(re.search(r"financial result|results", purpose.lower()))})
    return sorted(out, key=lambda e: (e["date"], e["sym"]))


def classify_filing(desc: str, text: str, rules: list, skip: str):
    """(label, severity, tone) for a corporate filing, or None when it is routine."""
    if skip and re.search(skip, desc or "", re.I):
        return None
    blob = f"{desc} {text}".lower()
    for rx, label, sev, tone in rules:
        if re.search(rx, blob):
            if label in ("M&A", "Resignation") and re.search(r"subsidiar|independent director|company secretary", blob):
                sev = 1
            return label, sev, tone
    return None


def filings(rows, rules: list, skip: str) -> list[dict]:
    """NSE corporate announcements -> classified filings (routine ones dropped)."""
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or not r.get("seq_id"):
            continue
        cls = classify_filing(r.get("desc", ""), r.get("attchmntText", ""), rules, skip)
        if not cls:
            continue
        out.append({"seq": str(r.get("seq_id")), "sym": r.get("symbol"), "name": r.get("sm_name"),
                    "desc": r.get("desc"), "text": (r.get("attchmntText") or "")[:300], "time": r.get("an_dt"),
                    "link": r.get("attchmntFile"), "label": cls[0], "severity": cls[1], "tone": cls[2]})
    return out


def _frac_pct(v) -> float | None:
    x = num(v)
    return None if x is None else round(x * 100, 4) if x <= 1 else x


def insider_xbrl(text: str, meta: dict) -> list[dict]:
    """Rows from one NSE insider-trading XBRL filing (the format from May 2026; one filing can hold several trades)."""
    main, per = {}, {}
    for tag, ctx, val in re.findall(r'<in-bse-co:([A-Za-z]+)\s+contextRef="([^"]+)"[^>]*>([^<]*)<', text or ""):
        (main if ctx == "MainI" else per.setdefault(ctx, {}))[tag] = html.unescape(val.strip())
    filed = _date(meta.get("broadcastDateTime")) or _date(meta.get("exchdisstime"))
    rows = []
    for ctx, f in per.items():
        typ = f.get("SecuritiesAcquiredOrDisposedTransactionType", "")
        cat, mode = f.get("CategoryOfPerson", ""), f.get("ModeOfAcquisitionOrDisposal", "")
        t1 = (f.get("DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyToDate")
              or f.get("DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyFromDate"))
        try:
            traded = dt.date.fromisoformat(t1) if t1 else None
        except ValueError:
            traded = None
        rows.append({"pid": f"g{meta.get('appId')}:{ctx}", "sym": meta.get("symbol") or main.get("Symbol"),
                     "company": meta.get("companyName") or main.get("NameOfTheCompany"),
                     "person": f.get("NameOfThePerson", ""), "category": cat,
                     "side": "Buy" if typ.lower() == "buy" else "Sell" if typ.lower() == "sell" else typ, "mode": mode,
                     "qty": num(f.get("SecuritiesAcquiredOrDisposedNumberOfSecurity")) or 0.0,
                     "value": num(f.get("SecuritiesAcquiredOrDisposedValueOfSecurity")) or 0.0,
                     "before_pct": _frac_pct(f.get("SecuritiesHeldPriorToAcquisitionOrDisposalPercentageOfShareholding")),
                     "after_pct": _frac_pct(f.get("SecuritiesHeldPostAcquistionOrDisposalPercentageOfShareholding")),
                     "traded": traded.isoformat() if traded else None,
                     "filed": filed.isoformat(timespec="minutes") if filed else None,
                     "days_to_file": (filed.date() - traded).days if filed and traded else None,
                     "key": any(k.lower() in cat.lower() for k in KEY_PEOPLE), "market": mode.lower().startswith("market")})
    return rows


def meaningful_insider(x: dict, min_rs: float = 5e6) -> bool:
    """Promoters, directors and key managers buying or selling in the market, at least ₹50 lakh."""
    return bool(x.get("key") and x.get("market") and (x.get("value") or 0) >= min_rs and x.get("side") in ("Buy", "Sell"))


def gift_nifty(d: dict) -> dict | None:
    """Nearest-expiry NIFTY future on NSE IX (GIFT City) from its derivatives-watch JSON."""
    rows = [r for r in ((d or {}).get("data") or []) if str(r.get("SYMBOL")).upper() == "NIFTY"
            and "FUT" in str(r.get("INSTRUMENT", "")).upper() and num(r.get("LASTPRICE"))]
    if not rows:
        return None
    r = min(rows, key=lambda r: _date(r.get("EXPIRYDATE")) or dt.datetime.max)
    last, chg, pct = num(r.get("LASTPRICE")), num(r.get("CHANGE")), num(r.get("PERCHANGE"))
    if pct is None and chg is not None and last - chg:
        pct = chg / (last - chg) * 100
    return {"last": last, "chg": chg, "chg_pct": None if pct is None else round(pct, 2), "expiry": r.get("EXPIRYDATE"),
            "day_h": num(r.get("HIGH")) or num(r.get("HIGHPRICE")), "day_l": num(r.get("LOW")) or num(r.get("LOWPRICE")),
            "at": r.get("TIMESTMP")}


# ------------------------------------------------------------------ news
def rss(content: bytes, source: str) -> list[dict]:
    """RSS 2.0 items -> [{title, link, source, ts}] (Atom entries too)."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []
    out = []
    items = list(root.iter("item")) or list(root.iter("{http://www.w3.org/2005/Atom}entry"))
    for it in items:
        title = html.unescape((it.findtext("title") or it.findtext("{http://www.w3.org/2005/Atom}title") or "").strip())
        link = (it.findtext("link") or "").strip()
        if not link:
            el = it.find("{http://www.w3.org/2005/Atom}link")
            link = el.get("href", "") if el is not None else ""
        pub = (it.findtext("pubDate") or it.findtext("{http://www.w3.org/2005/Atom}updated") or "").strip()
        ts = None
        try:
            ts = parsedate_to_datetime(pub).timestamp()
        except Exception:
            try:
                ts = dt.datetime.fromisoformat(pub.replace("Z", "+00:00")).timestamp()
            except Exception:
                try:
                    ts = dt.datetime.strptime(pub[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()
                except Exception:
                    ts = None
        if title and link.startswith(("http://", "https://")):
            out.append({"title": re.sub(r"\s+", " ", title)[:300], "link": link[:500], "source": source, "ts": ts})
    return out


def tag_headline(title: str, rules: dict) -> dict:
    """Keyword tone (-1 / 0 / +1) and impact (high / medium / low) of a headline: a hint, not a call."""
    t = " " + title.lower() + " "
    p = sum(w in t for w in rules.get("positive", []))
    n = sum(w in t for w in rules.get("negative", []))
    high = bool(re.search(rules.get("high_impact", r"$^"), t, re.I))
    india = bool(re.search(rules.get("india_words", r"$^"), t, re.I))
    return {"tone": (p > n) - (n > p), "impact": "high" if high and india else "medium" if high or india else "low",
            "india": india}
