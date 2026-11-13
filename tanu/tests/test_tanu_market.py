"""Market page (0.14.0, ADR-0012): public-source parsers on saved samples, the collector (isolation, cache, alerts,
URL guard), the alert book, the display-only detectors on the live day, and the API. No network: every fetch goes
through a fake transport."""
import datetime as dt
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from tanu.detect.book import AlertBook
from tanu.detect.runner import Detectors
from tanu.intel import parsers as P
from tanu.intel import views
from tanu.intel.collect import Collector, alerts_for, yahoo_ticker
from tanu.intel.http import Http
from tanu.live import universe as U
from tanu.ui.api import create_app

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NOW = dt.datetime(2026, 11, 13, 16, 0, tzinfo=IST).timestamp()


# ------------------------------------------------------------------ samples (shapes as the sites send them)
def yahoo(last, prev, closes=(1, 2, 3)):
    return {"chart": {"result": [{"meta": {"regularMarketPrice": last, "chartPreviousClose": prev, "regularMarketTime": NOW,
                                           "fiftyTwoWeekHigh": last * 1.2, "fiftyTwoWeekLow": last * 0.8, "currency": "INR"},
                                  "timestamp": [NOW - 240, NOW - 120, NOW],
                                  "indicators": {"quote": [{"close": list(closes), "high": [4, 5, 6], "low": [0.5, 1, 2]}]}}]}}


FII = [{"category": "DII **", "date": "13-Nov-2026", "buyValue": "15000.5", "sellValue": "12000.25", "netValue": "3000.25"},
       {"category": "FII/FPI *", "date": "13-Nov-2026", "buyValue": "11000", "sellValue": "13500", "netValue": "-2500"}]

POI = ('"Participant wise Open Interest (no. of contracts) in Equity Derivatives as on Nov 13, 2026"\n'
       "Client Type,Future Index Long,Future Index Short,Option Index Call Long,Option Index Put Long,"
       "Option Index Call Short,Option Index Put Short,Future Stock Long,Future Stock Short\n"
       "Client,100,50,500,400,450,300,10,5\nDII,10,90,0,0,0,0,30,1\nFII,40,160,200,300,100,250,50,60\n"
       "Pro,30,20,100,100,50,60,5,5\nTOTAL,180,320,800,800,600,610,95,71\n")


def mwpl():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("combineoi_13112026.csv", "Date,ISIN,Scrip Name,NSE Symbol,MWPL,Open Interest,Limit for Next Day,"
                   "Future Equivalent Open Interest\n13-Nov-2026,X,ABC,ABC,1000,980,No Fresh Positions,970\n"
                   "13-Nov-2026,Y,DEF,DEF,1000,850,500,850\n13-Nov-2026,Z,GHI,GHI,1000,100,900,100\n")
    return b.getvalue()


BULK = ("Date,Symbol,Security Name,Client Name,Buy/Sell,Quantity Traded,Trade Price / Wght. Avg. Price,Remarks\n"
        "13-Nov-2026,ABC,ABC Ltd,BIG FUND,BUY,1000000,250.5,-\n13-Nov-2026,DEF,DEF Ltd,SOMEONE,SELL,10,x,-\n")
EVENTS = [{"symbol": "ABC", "company": "ABC Ltd", "purpose": "Financial Results", "date": "17-Nov-2026"},
          {"symbol": "GHI", "company": "GHI Ltd", "purpose": "Dividend", "date": "18-Nov-2026"}, {"bad": 1}]
ANN = [{"seq_id": "1", "symbol": "ABC", "sm_name": "ABC Ltd", "desc": "Outcome of Board Meeting",
        "attchmntText": "Approved buyback of shares", "an_dt": "13-Nov-2026 15:10:00", "attchmntFile": "https://nsearchives.nseindia.com/x.pdf"},
       {"seq_id": "2", "symbol": "DEF", "desc": "Trading Window", "attchmntText": "closure of trading window"},
       {"seq_id": "3", "symbol": "GHI", "desc": "Updates", "attchmntText": "SEBI order and penalty imposed"}]
PIT_LIST = {"data": [{"appId": "77", "symbol": "ABC", "companyName": "ABC Ltd", "broadcastDateTime": "13-Nov-2026 18:01:00",
                      "xmlFileName": "https://nsearchives.nseindia.com/corporate/xbrl/PIT_77.xml"}]}
PIT_XML = """<xbrl><in-bse-co:Symbol contextRef="MainI">ABC</in-bse-co:Symbol>
<in-bse-co:NameOfThePerson contextRef="D1">A &amp; B Promoter</in-bse-co:NameOfThePerson>
<in-bse-co:CategoryOfPerson contextRef="D1">Promoter Group</in-bse-co:CategoryOfPerson>
<in-bse-co:SecuritiesAcquiredOrDisposedTransactionType contextRef="D1">Buy</in-bse-co:SecuritiesAcquiredOrDisposedTransactionType>
<in-bse-co:ModeOfAcquisitionOrDisposal contextRef="D1">Market Purchase</in-bse-co:ModeOfAcquisitionOrDisposal>
<in-bse-co:SecuritiesAcquiredOrDisposedNumberOfSecurity contextRef="D1">50000</in-bse-co:SecuritiesAcquiredOrDisposedNumberOfSecurity>
<in-bse-co:SecuritiesAcquiredOrDisposedValueOfSecurity contextRef="D1">125000000</in-bse-co:SecuritiesAcquiredOrDisposedValueOfSecurity>
<in-bse-co:SecuritiesHeldPostAcquistionOrDisposalPercentageOfShareholding contextRef="D1">0.5123</in-bse-co:SecuritiesHeldPostAcquistionOrDisposalPercentageOfShareholding>
<in-bse-co:DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyToDate contextRef="D1">2026-11-11</in-bse-co:DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyToDate>
</xbrl>"""
GIFT = {"data": [{"SYMBOL": "NIFTY", "INSTRUMENT": "FUTIDX", "EXPIRYDATE": "25-Dec-2026", "LASTPRICE": "26100"},
                 {"SYMBOL": "NIFTY", "INSTRUMENT": "FUTIDX", "EXPIRYDATE": "27-Nov-2026", "LASTPRICE": "26050",
                  "CHANGE": "-50", "HIGH": "26200", "LOW": "26000"},
                 {"SYMBOL": "BANKNIFTY", "INSTRUMENT": "FUTIDX", "EXPIRYDATE": "27-Nov-2026", "LASTPRICE": "59000"}]}
FF = [{"title": "CPI m/m", "country": "USD", "date": "2026-11-13T08:30:00-05:00", "impact": "High", "forecast": "0.3%", "previous": "0.4%"},
      {"title": "Bank holiday", "country": "JPY", "date": "2026-11-13T00:00:00-05:00", "impact": "Holiday"}]
RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Sensex, Nifty crash as RBI hikes repo rate</title><link>https://example.com/a</link><pubDate>Fri, 13 Nov 2026 10:00:00 +0530</pubDate></item>
<item><title>ABC bags big order &amp; shares rally</title><link>https://example.com/b</link><pubDate>Fri, 13 Nov 2026 11:00:00 +0530</pubDate></item>
<item><title>Bad link</title><link>javascript:alert(1)</link></item>
</channel></rss>"""


class Fake:
    """A transport that answers by URL; 'fail' lists URL parts that answer 503."""

    def __init__(self, fail=()):
        self.fail, self.urls = set(fail), []

    def __call__(self, url, headers, timeout, opener):
        self.urls.append(url)
        if any(f in url for f in self.fail):
            return 503, b"down"
        j = lambda x: (200, json.dumps(x).encode())
        if "finance.yahoo.com" in url:
            t = url.split("/chart/")[1].split("?")[0]
            return j(yahoo(2.0, 1.0) if t in ("ES%3DF", "BZ%3DF") else yahoo(100.1, 100.0))
        if url.rstrip("/") == "https://www.nseindia.com":
            return 200, b"<html></html>"
        if "fiidiiTradeReact" in url:
            return j(FII)
        if "fao_participant_oi" in url:
            return (200, POI.encode()) if "13112026" in url or "12112026" in url else (404, b"")
        if "combineoi" in url:
            return (200, mwpl()) if "13112026" in url else (404, b"")
        if "bulk.csv" in url or "block.csv" in url:
            return 200, BULK.encode()
        if "event-calendar" in url:
            return j(EVENTS)
        if "corporate-announcements" in url:
            return j(ANN)
        if "corporates-pit-gg" in url:
            return j(PIT_LIST)
        if "PIT_77.xml" in url:
            return 200, PIT_XML.encode()
        if "nseix.com" in url:
            return j(GIFT)
        if "faireconomy" in url:
            return j(FF)
        if "rss" in url or "feeds" in url or url.endswith(".xml") or url.endswith(".cms"):
            return 200, RSS
        return 404, b""


def collector(tmp_path, fail=(), alerts=None):
    return Collector(tmp_path, Http(transport=Fake(fail), sleep=lambda s: None), now=lambda: NOW, on_alert=alerts)


# ------------------------------------------------------------------ parsers
def test_parsers_read_the_saved_shapes():
    y = P.yahoo_chart(yahoo(101.0, 100.0))
    assert y["last"] == 101.0 and y["chg_pct"] == 1.0 and y["day_h"] == 6 and y["line"][-1] == 101.0
    assert P.yahoo_chart({"chart": {"result": []}}) is None
    f = P.fii_dii(FII)
    assert f["FII"]["net"] == -2500 and f["DII"]["net"] == 3000.25
    poi = P.participant_oi(P.participant_csv(POI), None, "2026-11-13")
    fii = next(r for r in poi["rows"] if r["who"] == "FII")
    assert fii["fut_net"] == -120 and fii["fut_long_pct"] == 20.0 and fii["call_net"] == 100 and "fut_net_chg" not in fii
    m = P.mwpl_zip(mwpl())
    assert m["ABC"] == {"pct": 97.0, "ban": True} and m["DEF"]["pct"] == 85.0 and P.mwpl_zip(b"<html>") == {}
    d = P.deals_csv(BULK, "Bulk")
    assert len(d) == 1 and d[0]["value_cr"] == 25.05
    ev = P.events(EVENTS)
    assert [e["sym"] for e in ev] == ["ABC", "GHI"] and ev[0]["results"] and ev[0]["date"] == "2026-11-17"
    feeds = json.load(open(P.__file__.replace("parsers.py", "feeds.json")))
    fl = P.filings(ANN, feeds["filing_rules"], feeds["filing_skip"])
    assert [(x["sym"], x["label"]) for x in fl] == [("ABC", "Buyback"), ("GHI", "Red flag")]
    (x,) = P.insider_xbrl(PIT_XML, PIT_LIST["data"][0])
    assert x["person"] == "A & B Promoter" and x["side"] == "Buy" and x["value"] == 1.25e8 and x["after_pct"] == 51.23
    assert x["days_to_file"] == 2 and P.meaningful_insider(x)
    g = P.gift_nifty(GIFT)
    assert g["last"] == 26050 and g["expiry"] == "27-Nov-2026" and round(g["chg_pct"], 2) == -0.19
    cal = P.ff_calendar(FF)
    assert len(cal) == 1 and cal[0]["time"] == "2026-11-13T19:00+05:30"
    items = P.rss(RSS, "ET")
    assert [i["title"] for i in items] == ["Sensex, Nifty crash as RBI hikes repo rate", "ABC bags big order & shares rally"]
    assert P.rss(b"not xml", "x") == []
    t = P.tag_headline(items[0]["title"], feeds)
    assert t == {"tone": -1, "impact": "high", "india": True}
    assert P.tag_headline(items[1]["title"], feeds)["tone"] == 1


def test_yahoo_tickers_for_indices_and_stocks():
    assert yahoo_ticker("nifty") == "^NSEI" and yahoo_ticker("M&M") == "M&M.NS" and yahoo_ticker("^VIX") == "^VIX"


# ------------------------------------------------------------------ collector
def test_every_source_reads_into_its_cache_and_one_failure_does_not_stop_the_rest(tmp_path):
    c = collector(tmp_path, fail=["fiidiiTradeReact", "nseix.com"])
    ok = c.refresh()
    assert ok["fii_dii"] is False and ok["gift_nifty"] is False
    assert all(v for k, v in ok.items() if k not in ("fii_dii", "gift_nifty")), ok
    f = c.cached("fii_dii")
    assert f["ok"] is False and "503" in f["error"] and f["data"] is None
    w = c.cached("world")["data"]
    assert w["answered"] == w["of"] + 4 and w["adr"] and w["adr"][0]["nse"] == "INFY"     # + the 4 ADR stocks on NSE
    assert c.cached("participant_oi")["data"]["rows"][0]["fut_net_chg"] == 0                 # today vs the day before
    assert c.cached("ban")["data"]["ban"] == ["ABC"] and c.cached("ban")["data"]["near"] == [["DEF", 85.0]]
    assert c.cached("insiders")["data"]["rows"][0]["sym"] == "ABC"
    assert c.refresh() == {}                                         # nothing due again until each source's every_s


def test_a_failed_read_keeps_the_last_good_data(tmp_path):
    c = collector(tmp_path)
    c.refresh(["fii_dii"])
    c.http.transport.fail.add("fiidiiTradeReact")
    assert c.refresh(["fii_dii"], force=True) == {"fii_dii": False}
    x = c.cached("fii_dii")
    assert x["ok"] is False and x["data"]["FII"]["net"] == -2500


def test_alerts_only_for_new_items_after_the_first_read(tmp_path):
    got = []
    c = collector(tmp_path, alerts=got.append)
    c.refresh(["filings", "insiders", "news_india", "fii_dii"])
    assert got == []                                                 # the first read is the starting point
    prev = {"items": [], "new": []}
    data = c.cached("filings")["data"]
    out = alerts_for("filings", prev, data, c.spec["sources"], c.world_spec)
    assert [(a["sym"], a["direction"]) for a in out] == [("ABC", "up"), ("GHI", "down")]
    ins = alerts_for("insiders", {"rows": []}, c.cached("insiders")["data"], c.spec["sources"], c.world_spec)
    assert len(ins) == 1 and ins[0]["direction"] == "up" and "12.5 Cr" in ins[0]["title"]
    news = alerts_for("news_india", {"items": []}, c.cached("news_india")["data"], c.spec["sources"], c.world_spec)
    assert [a["title"] for a in news] == ["Sensex, Nifty crash as RBI hikes repo rate"]
    fd = alerts_for("fii_dii", {"FII": {"date": "12-Nov-2026"}}, c.cached("fii_dii")["data"], c.spec["sources"], c.world_spec)
    assert len(fd) == 1 and fd[0]["direction"] == "up"                # -2500 + 3000 net


def test_world_move_alert_fires_once_per_level(tmp_path):
    c = collector(tmp_path)
    c.refresh(["world"])
    data = c.cached("world")["data"]                                  # ES=F and BZ=F +100% in the fake
    out = alerts_for("world", {"groups": []}, data, c.spec["sources"], c.world_spec)
    assert {a["data"]["ticker"] for a in out} == {"ES=F", "BZ=F"} and all(a["severity"] == 2 for a in out)
    assert alerts_for("world", data, data, c.spec["sources"], c.world_spec) == []


def test_blocked_urls_never_leave_the_machine(tmp_path):
    c = collector(tmp_path)
    c.feeds["india"] = {"bad": "https://example.com/mutual-fund/rss.xml", "ok": "https://example.com/markets.rss"}
    c.refresh(["news_india"])
    d = c.cached("news_india")["data"]
    assert "bad" in d["errors"] and "trading / account" in d["errors"]["bad"] and len(d["items"]) == 2
    assert not any("mutual-fund" in u for u in c.http.transport.urls)


def test_watchlist_is_cleaned_and_saved(tmp_path):
    c = collector(tmp_path)
    assert c.set_watch(["reliance", " M&M ", "RELIANCE", "<script>", "", "x" * 30]) == ["RELIANCE", "M&M"]
    assert c.watch_symbols() == ["RELIANCE", "M&M"]
    c.refresh(["watchlist", "ban", "events", "insiders"])
    v = views.watchlist(c)
    assert [r["sym"] for r in v["rows"]] == ["RELIANCE", "M&M"] and v["rows"][0]["last"] == 100.1


def test_views_compose_each_tab(tmp_path):
    c = collector(tmp_path)
    c.refresh()
    ctx = views.context(c)
    assert ctx["gift_nifty"]["last"] == 26050 and ctx["ban"]["ban"] == ["ABC"] and [e["sym"] for e in ctx["results_week"]] == ["ABC"]
    assert {x["ticker"] for x in ctx["cues"]} >= {"ES=F", "INR=X"}
    ins = views.insiders(c)
    assert ins["insider_net"][0] == {"sym": "ABC", "net_cr": 12.5, "buy_cr": 12.5, "sell_cr": 0.0, "trades": 1}
    t = views.tab(c, "news", refresh=False)
    assert t["sources"]["news_india"]["ok"] is True and len(t["items"]) == 2 and t["reading"] == []


# ------------------------------------------------------------------ alert book
def test_alert_book_cooldown_and_filters(tmp_path):
    b = AlertBook(tmp_path)
    a = {"sym": "NIFTY", "kind": "sharp_move", "direction": "up", "severity": 3, "title": "t", "detail": "d"}
    assert b.add(a, NOW) and not b.add(a, NOW + 60) and b.add(a, NOW + 11 * 60)         # sharp_move cooldown 10 min
    assert b.add({**a, "direction": "down"}, NOW + 60)
    assert b.add({**a, "kind": "candles", "severity": 1, "sym": "ABC"}, NOW + 120)
    v = b.view(min_sev=2)
    assert v["day"] == "2026-11-13" and v["counts"]["all"] == 4 and v["shown"] == 3 and v["alerts"][0]["t"] == NOW + 11 * 60
    assert b.view(min_sev=1, kind="candles")["shown"] == 1 and b.view(min_sev=1, sym="abc")["shown"] == 1


# ------------------------------------------------------------------ detectors
T0 = int(dt.datetime(2026, 11, 13, 9, 15, tzinfo=IST).timestamp())


def _uni():
    ms = int(dt.datetime(2026, 11, 17, 15, 30, tzinfo=IST).timestamp() * 1000)
    rows = []
    for k in range(24500, 25501, 50):
        for side in ("CE", "PE"):
            rows.append({"segment": "NSE_FO", "instrument_type": side, "instrument_key": f"O|{k}{side}", "underlying_symbol": "NIFTY",
                         "underlying_key": "I|NIFTY", "underlying_type": "INDEX", "expiry": ms, "strike_price": k,
                         "lot_size": 65, "tick_size": 5.0})
    for k in (90, 100, 110):
        for side in ("CE", "PE"):
            rows.append({"segment": "NSE_FO", "instrument_type": side, "instrument_key": f"S|{k}{side}", "underlying_symbol": "ABC",
                         "underlying_key": "E|ABC", "underlying_type": "EQUITY", "expiry": ms, "strike_price": k,
                         "lot_size": 500, "tick_size": 5.0})
    return U.Universe(rows, "2026-11-13")


def _bar(key, m, c, o=None, vol=None, oi=None, h=None, l=None):
    o = c if o is None else o
    return {"key": key, "t": T0 + 60 * m, "o": o, "h": max(o, c) if h is None else h, "l": min(o, c) if l is None else l,
            "c": c, "vol": vol, "oi": oi}


def test_sharp_move_and_opening_range_break_on_the_index(tmp_path):
    book = AlertBook(tmp_path)
    d = Detectors(_uni(), T0, book, prev_close={"NIFTY": 25000.0})
    last = {"O|25000CE": {"iv": 12.0}, "O|25000PE": {"iv": 12.0}}
    px = [25000 + (i % 3) for i in range(40)] + [25010, 25040, 25070, 25100, 25130]
    for m, p in enumerate(px):
        rows = [_bar("I|NIFTY", m, p)] + [_bar(f"O|{k}{s}", m, 100.0, oi=1000.0) for k in (24950, 25000, 25050) for s in ("CE", "PE")]
        d.on_minute(T0 + 60 * m, rows, last)
    kinds = {(a["kind"], a["direction"]) for a in book.read("2026-11-13")}
    assert ("sharp_move", "up") in kinds and ("orb", "up") in kinds and ("day_extreme", "up") in kinds
    sm = next(a for a in book.read("2026-11-13") if a["kind"] == "sharp_move")
    assert sm["severity"] == 2 and 4 <= sm["data"]["z"] < 6.5           # +0.39% in 5 min vs ~0.087% priced at IV 12
    assert sm["at"].endswith("09:59:00+05:30")                        # minute 43 closes at 09:59; then 10 min quiet
    assert d.failed == {}


def test_unusual_option_buying_and_a_broken_detector_is_switched_off_alone(tmp_path):
    book = AlertBook(tmp_path)
    d = Detectors(_uni(), T0, book)
    d.rules["candles"] = lambda *a: 1 / 0                                 # a broken bullet
    for m in range(30):
        big = m >= 25
        rows = [_bar("I|NIFTY", m, 25000.0),
                _bar("O|25100CE", m, 50.0 + (5 * (m - 24) if big else 0), vol=(400000 if big else 20000), oi=1e6)]
        d.on_minute(T0 + 60 * m, rows, {})
    al = [a for a in book.read("2026-11-13") if a["kind"] == "unusual_options"]
    assert al and al[0]["direction"] == "up" and "buying" in al[0]["title"] and "25100 CE" in al[0]["title"]
    assert "candles" in d.failed and "sharp_move" not in d.failed


def test_live_loop_survives_a_detector_that_raises(tmp_path):
    from tanu.live.loop import LiveLoop

    class Boom:
        def on_minute(self, *a):
            raise RuntimeError("boom")

    class Eng:
        setups, signals, open, closed, opt = [], [], [], [], type("O", (), {"add_bar": lambda *a: False})()

        def start_day(self, *a):
            pass

        def on_bar(self, *a):
            pass

        def end_day(self, *a):
            pass

    lp = LiveLoop(Eng(), _uni(), {}, "2026-11-13", out_dir=tmp_path, detect=Boom(), clock="exchange")
    lp.bars.add([{"key": "I|NIFTY", "ltp": 25000.0, "ltt": (T0 + 5) * 1000}], T0 + 5)
    lp._close_minute(0)
    assert lp.counts["detect_errors"] == 1 and lp.next_m == 1
    st = json.loads((tmp_path / "status.json").read_text())
    assert st["minute"] == 1


# ------------------------------------------------------------------ API
def test_market_api_tabs_alerts_and_watchlist_guard(tmp_path):
    c = collector(tmp_path)
    c.refresh()
    AlertBook(tmp_path / "live" / "alerts").add({"sym": "NIFTY", "kind": "orb", "direction": "up", "severity": 2,
                                                 "title": "NIFTY broke above", "detail": "x"}, NOW)
    api = TestClient(create_app(tmp_path, collector=c))
    for tab in ("context", "world", "insiders", "watchlist", "news"):
        r = api.get(f"/api/market?tab={tab}")
        assert r.status_code == 200 and r.json()["tab"] == tab and r.json()["sources"]
    a = api.get("/api/market?tab=alerts").json()
    assert a["counts"]["all"] == 1 and a["alerts"][0]["title"] == "NIFTY broke above"
    assert api.get("/api/market?tab=nope").status_code == 404
    hdr = {"host": "127.0.0.1:8790"}
    assert api.post("/api/market/watchlist", json={"symbols": ["ABC"]}, headers=hdr).status_code == 403   # no X-Tanu
    r = api.post("/api/market/watchlist", json={"symbols": ["abc", "def"]}, headers={**hdr, "X-Tanu": "1"})
    assert r.status_code == 200 and r.json()["symbols"] == ["ABC", "DEF"]
