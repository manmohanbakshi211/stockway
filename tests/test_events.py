"""2.35: event engine: official feeds parsed, tagged and matched; event risk from every source; Telegram only for
new high-impact items; never a block."""
import datetime as dt
import types

from live import events as EV
from live.clock import IST

NOW = dt.datetime(2026, 11, 4, 11, 0, tzinfo=IST)

RBI = ("﻿<?xml version=\"1.0\" encoding=\"utf-8\"?><rss version=\"2.0\"><channel><title>PRESS RELEASES FROM RBI</title>"
       "<item><title><![CDATA[RBI imposes monetary penalty on HDFC Bank Ltd.]]></title><link>https://rbi.org.in/x1</link>"
       "<pubDate>Wed, 04 Nov 2026 10:40:00 +0530</pubDate></item>"
       "<item><title><![CDATA[RBI to conduct Overnight Variable Rate Reverse Repo auction]]></title><link>https://rbi.org.in/x2</link>"
       "<pubDate>Wed, 04 Nov 2026 09:30:00 +0530</pubDate></item>"
       "<item><title><![CDATA[Recruitment of Officers in Grade B]]></title><link>https://rbi.org.in/x3</link></item>"
       "</channel></rss>").encode("utf-8")
SEBI = ("<?xml version=\"1.0\"?><rss><channel><item><title>Interim Order in the matter of Reliance Industries Limited</title>"
        "<link>https://www.sebi.gov.in/https://www.sebi.gov.in/a.pdf</link><pubDate>04 Nov 2026 10:50:00 +0530</pubDate></item>"
        "</channel></rss>").encode()
PIB = ("<?xml version=\"1.0\"?><rss><channel><item><title>PM greets citizens on a festival</title><link>https://pib/1</link></item>"
       "<item><title>Cabinet approves PLI scheme for semiconductor units</title><link>https://pib/2</link></item></channel></rss>").encode()

SOURCES = [{"name": "RBI press releases", "who": "RBI", "url": "rbi"}, {"name": "SEBI", "who": "SEBI", "url": "sebi"},
           {"name": "Government (PIB)", "who": "Government", "url": "pib", "only_if": ["pli", "cabinet approves"]},
           {"name": "Broken", "who": "X", "url": "broken"}]


class _Store(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)

    def put(self, k, v):
        self[k] = v


def _engine():
    sent, recorded = [], []
    u = lambda idx, exp=None: types.SimpleNamespace(is_index=idx, near_expiry=lambda d, exp=exp: exp)
    uni = types.SimpleNamespace(underlyings={"NIFTY": u(True, NOW.date()), "HDFCBANK": u(False), "RELIANCE": u(False),
                                             "INFY": u(False)})
    ctx = types.SimpleNamespace(next_event=lambda s, d: {"date": "2026-11-05", "results": True, "days": 1} if s == "INFY" else None,
                                view=lambda: {"ban": ["RELIANCE"]})
    world = types.SimpleNamespace(calendar=[{"time": (NOW + dt.timedelta(minutes=40)).isoformat(), "country": "USD",
                                             "title": "CPI m/m", "impact": "High"}])
    e = types.SimpleNamespace(store=_Store(), telegram=types.SimpleNamespace(send=sent.append), log=lambda m: None,
                              words={"HDFCBANK": ["hdfc bank"], "RELIANCE": ["reliance industries"], "INFY": ["infosys"],
                                     "NIFTY": ["nifty"]},
                              uni=uni, context=ctx, world=world, running=True,
                              recorder=types.SimpleNamespace(news=lambda items, ts: recorded.extend(items)))
    return e, sent, recorded


def _get(url):
    if url == "broken":
        raise OSError("connection refused")
    return types.SimpleNamespace(content={"rbi": RBI, "sebi": SEBI, "pib": PIB}[url], raise_for_status=lambda: None)


def test_feeds_are_parsed_tagged_matched_and_saved(monkeypatch):
    monkeypatch.setattr(EV, "cfg", lambda: {**EV.DEFAULTS, **{k: v for k, v in EV.json.loads(EV.FILE.read_text()).items() if k in EV.DEFAULTS},
                                            "sources": SOURCES})
    monkeypatch.setattr(EV.time, "time", lambda: NOW.timestamp())
    e, sent, recorded = _engine()
    g = EV.EventEngine(e)
    assert g.fetch_all(get=_get) == 4                    # recruitment and the greeting are noise
    by = lambda start: next(x for x in g.items if x["title"].startswith(start))
    assert by("RBI imposes")["impact"] == "high" and by("RBI imposes")["syms"] == ["HDFCBANK"]
    assert by("Interim Order")["syms"] == ["RELIANCE"] and by("Interim Order")["link"] == "https://www.sebi.gov.in/a.pdf"
    assert by("RBI to conduct")["impact"] == "low" and by("Cabinet approves")["impact"] == "medium"   # routine liquidity op: low
    assert g.status["Broken"]["ok"] is False and g.status["SEBI"]["ok"] is True      # one broken feed, the rest fine
    assert len(sent) == 2 and any("on HDFCBANK" in s for s in sent) and any("on RELIANCE" in s for s in sent)
    assert len(recorded) == 4 and e.store["events:items"]
    assert g.fetch_all(get=_get) == 0 and len(sent) == 2                             # nothing twice


def test_event_risk_from_every_source(monkeypatch):
    monkeypatch.setattr(EV, "cfg", lambda: {**EV.DEFAULTS, **{k: v for k, v in EV.json.loads(EV.FILE.read_text()).items() if k in EV.DEFAULTS},
                                            "sources": SOURCES})
    monkeypatch.setattr(EV.time, "time", lambda: NOW.timestamp())
    import lab.rules as R
    monkeypatch.setattr(R, "events_named", lambda: [(NOW.date().isoformat(), "RBI policy decision")])
    e, _, _ = _engine()
    g = EV.EventEngine(e)
    g.fetch_all(get=_get)
    n = g.risk("NIFTY", NOW)
    whats = " | ".join(i["what"] for i in n["items"])
    assert n["level"] == "high" and "RBI policy decision today" in whats and "USD CPI m/m in 40 min" in whats and "Expiry day" in whats
    assert n["facts"]["ev_india_today"] == 1 and n["facts"]["ev_glob_min"] == 40 and n["facts"]["ev_expiry"] == 1
    assert n["line"].startswith("Event risk: ") and n["warnings"]
    h = g.risk("HDFCBANK", NOW)
    assert any("RBI: RBI imposes monetary penalty" in i["what"] for i in h["items"]) and h["facts"]["ev_off_sym_min"] == 20
    r = g.risk("RELIANCE", NOW)
    assert any(i["what"] == "In the F&O ban today" for i in r["items"]) and r["facts"]["ev_ban"] == 1
    i = g.risk("INFY", NOW)
    assert any(x["what"] == "Results tomorrow" for x in i["items"]) and i["facts"]["ev_results_d"] == 1
    later = NOW + dt.timedelta(hours=5)                  # the official news is no longer fresh
    assert not any("RBI imposes" in x["what"] for x in g.risk("HDFCBANK", later)["items"])
