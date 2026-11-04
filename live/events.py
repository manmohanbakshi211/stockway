"""Event engine (gun): official feeds (RBI, SEBI, Government, NSE, BSE) and the event risk of every alert.

Sources, impact words and windows are bullets in rules/event_sources.json. A background thread reads the feeds (every
2 minutes in market hours), keeps the items (saved across restarts and with the day's recording), matches them to F&O
symbols by name and sends a new high-impact one to Telegram. risk(sym) puts everything that can make a move jumpy in
one place: India's scheduled days (RBI policy, Budget), high-impact world events about to come out, the stock's
results, expiry day, the F&O ban list, a holiday tomorrow and fresh official news. It is information and warnings
only (shown on alerts, recorded as facts for the models, warnings in the risk check): it never blocks an alert or a
trade. Read-only: nothing here talks to a broker.
"""
from __future__ import annotations

import collections
import datetime as dt
import hashlib
import html
import json
import re
import threading
import time
from pathlib import Path

from .clock import IST

FILE = Path(__file__).resolve().parents[1] / "rules" / "event_sources.json"
DEFAULTS = {"sources": [], "impact": {"high": [], "medium": []}, "skip": [], "fresh_min_symbol": 240, "fresh_min_market": 90,
            "global_window_min": 120, "telegram_high": True, "telegram_per_hour": 6, "poll_market_s": 120, "poll_other_s": 900}
LEVEL = {None: 0, "low": 1, "medium": 2, "high": 3}
WORD = {0: None, 1: "low", 2: "medium", 3: "high"}
_C: dict = {}


def cfg() -> dict:
    try:
        mt = FILE.stat().st_mtime
        if _C.get("mt") != mt:
            raw = json.loads(FILE.read_text())
            _C.update(mt=mt, cfg={**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}, error=None)
        return _C["cfg"]
    except Exception as e:
        _C.update(cfg=dict(DEFAULTS), error=str(e)[:160])
        return _C["cfg"]


def _ts(pub: str | None, fallback: float) -> float:
    if not pub:
        return fallback
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(pub).timestamp()
    except Exception:
        pass
    for fmt in ("%d %b %Y %H:%M:%S %z", "%d %b, %Y %z", "%d %b %Y", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            d = dt.datetime.strptime(pub.strip(), fmt)
            return (d if d.tzinfo else d.replace(tzinfo=IST)).timestamp()
        except Exception:
            continue
    return fallback


def parse_rss(content: bytes, src: dict, now: float) -> list:
    """RSS bytes -> [{"id", "who", "source", "title", "link", "ts"}]"""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(content.lstrip(b"\xef\xbb\xbf").strip())
    out = []
    for it in root.iter("item"):
        title = html.unescape((it.findtext("title") or "").strip())
        if not title:
            continue
        link = (it.findtext("link") or "").strip()
        link = re.sub(r"^(https?://[^/]+/)(https?://)", r"\2", link)        # SEBI's doubled prefix
        out.append({"id": hashlib.sha1((link or title).encode()).hexdigest()[:16], "who": src.get("who") or src["name"],
                    "source": src["name"], "title": title[:300], "link": link[:400], "ts": _ts(it.findtext("pubDate"), now)})
    return out


def tag(item: dict, src: dict, c: dict) -> dict | None:
    """Impact (high / medium / low) from the words, or None when the item is noise or off-topic for this source."""
    t = " " + item["title"].lower() + " "
    if any(w in t for w in c.get("skip") or []):
        return None
    only = src.get("only_if")
    if only and not any(w in t for w in only):
        return None
    imp = c.get("impact") or {}
    level = "high" if any(w in t for w in imp.get("high") or []) else "medium" if any(w in t for w in imp.get("medium") or []) else "low"
    return {**item, "impact": level}


class EventEngine:
    def __init__(self, engine):
        self.e = engine
        self.items: list = []                    # newest first
        self.ids: set = set()
        self.status: dict = {}                   # source name -> {"ok", "at", "n", "error"}
        self.sent = collections.deque(maxlen=50)
        self._cache: dict = {}
        self._ban = (0.0, set())
        self.lock = threading.Lock()
        try:
            saved = engine.store.get("events:items", []) or []
            self.items = saved[:400]
            self.ids = {x["id"] for x in self.items}
        except Exception:
            pass
        self._thread = None

    # ------------------------------------------------------------ the feeds
    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="event-feeds")
            self._thread.start()

    def _loop(self):
        time.sleep(15)
        while True:
            c = cfg()
            try:
                if (self.e.cfg().get("live") or {}).get("news", True):     # the same switch as the news feeds
                    self.fetch_all()
            except Exception as ex:
                self.e.log(f"Event feeds failed: {ex}")
            now = dt.datetime.now(IST)
            busy = now.weekday() < 5 and dt.time(8, 30) <= now.time() <= dt.time(15, 35)
            time.sleep(float(c["poll_market_s"] if busy else c["poll_other_s"]))

    def fetch_all(self, get=None) -> int:
        import requests
        c = cfg()
        get = get or (lambda url: requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (Macintosh) Stockway"}))
        new = []
        now = time.time()
        for src in c.get("sources") or []:
            try:
                r = get(src["url"])
                r.raise_for_status()
                got = parse_rss(r.content, src, now)
                kept = [x for x in (tag(i, src, c) for i in got) if x]
                fresh = [x for x in kept if x["id"] not in self.ids]
                today = dt.datetime.fromtimestamp(now, IST).date()
                for x in fresh:
                    x["seen"] = now
                    x["syms"] = self._match(x["title"])
                    d = dt.datetime.fromtimestamp(x["ts"], IST)
                    if d.time() == dt.time(0, 0) and d.date() == today:
                        x["ts"] = now                     # a date without a time (SEBI, NSE): when we first saw it
                new += fresh
                self.status[src["name"]] = {"ok": True, "at": now, "n": len(kept), "new": len(fresh), "error": None}
            except Exception as ex:                      # one broken feed never stops the others
                self.status[src["name"]] = {"ok": False, "at": now, "n": 0, "new": 0, "error": str(ex)[:160]}
        if new:
            with self.lock:
                for x in new:
                    self.ids.add(x["id"])
                self.items = sorted(new + self.items, key=lambda x: -x["ts"])[:400]
            self._cache.clear()
            try:
                self.e.store.put("events:items", self.items)
            except Exception:
                pass
            rec = getattr(self.e, "recorder", None)
            if rec is not None and getattr(self.e, "running", False):
                try:
                    rec.news([{"title": f"[{x['who']}] {x['title']}", "link": x["link"], "source": x["source"], "ts": x["ts"],
                               "syms": x["syms"], "tone": 0} for x in new], now)
                except Exception:
                    pass
            self._announce(new, now, c)
        self._rematch()
        return len(new)

    def _words(self) -> dict:
        return getattr(self.e, "words", None) or {}

    def _match(self, title) -> list:
        words = self._words()
        if not words:
            return []
        from .notify import match_symbols
        idx = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}
        return [s for s in match_symbols(title, {k: v for k, v in words.items() if k not in idx})][:5]

    def _rematch(self):
        """Items read before the symbol list was known get matched once it is."""
        if not self._words():
            return
        changed = False
        for x in self.items[:200]:
            if "matched" not in x:
                x["syms"], x["matched"] = self._match(x["title"]), True
                changed = True
        if changed:
            self._cache.clear()

    def _announce(self, new, now, c):
        if not c.get("telegram_high"):
            return
        uni = getattr(self.e, "uni", None)
        fo = set(uni.underlyings) if uni else set()
        for x in sorted(new, key=lambda x: x["ts"]):
            if x["impact"] != "high" or now - x["ts"] > 3600:
                continue                                   # old items on the first read are not news
            hit = [s for s in x["syms"] if s in fo]
            if x["syms"] and not hit:
                continue                                   # about a company that isn't an F&O stock
            while self.sent and now - self.sent[0] > 3600:
                self.sent.popleft()
            if len(self.sent) >= int(c.get("telegram_per_hour") or 6):
                return
            self.sent.append(now)
            who = f" on {', '.join(hit)}" if hit else ""
            try:
                self.e.telegram.send(f"🏛️ {x['who']}{who}: {x['title']}" + (f"\n{x['link']}" if x.get("link") else ""))
            except Exception:
                pass

    # ------------------------------------------------------------ event risk
    def _bans(self) -> set:
        t = time.time()
        if t - self._ban[0] > 60:
            try:
                ban = set((self.e.context.view().get("ban") or [])) if getattr(self.e, "context", None) else set()
            except Exception:
                ban = set()
            self._ban = (t, ban)
        return self._ban[1]

    def risk(self, sym: str, now: dt.datetime | None = None) -> dict:
        """{"level": "high"/"medium"/"low"/None, "line", "warnings", "items", "facts"} for a symbol right now."""
        now = now or dt.datetime.now(IST)
        key = (sym, int(now.timestamp() // 60))
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        if len(self._cache) > 3000:
            self._cache.clear()
        c = cfg()
        today = now.date()
        t = now.timestamp()
        items, facts = [], {}
        uni = getattr(self.e, "uni", None)
        u = uni.underlyings.get(sym) if uni else None
        is_index = bool(u and u.is_index)
        try:
            from lab import rules
            banks = set(rules.stock_list("banks"))
        except Exception:
            banks = set()

        def add(level, what, warn=None):
            items.append({"level": level, "what": what})
            if warn:
                items[-1]["warn"] = warn
        # India's scheduled days
        try:
            from lab.rules import events_named
            for d, name in events_named():
                if d == today.isoformat():
                    add("high", f"{name} today", f"{name} today: big swings around the announcement.")
                    facts["ev_india_today"] = 1
        except Exception:
            pass
        # world events about to come out (or just out)
        try:
            w = getattr(self.e, "world", None)
            for ev in (w.calendar if w else []) or []:
                if ev.get("impact") != "High":
                    continue
                mins = (dt.datetime.fromisoformat(ev["time"]).timestamp() - t) / 60
                if -30 <= mins <= float(c["global_window_min"]):
                    when = f"in {int(mins)} min" if mins >= 0 else f"{int(-mins)} min ago"
                    add("high" if -15 <= mins <= 60 else "medium", f"{ev.get('country')} {ev.get('title')} {when}",
                        f"{ev.get('country')} {ev.get('title')} {when}: expect a volatility burst." if -15 <= mins <= 30 else None)
                    if "ev_glob_min" not in facts or abs(mins) < abs(facts["ev_glob_min"]):
                        facts["ev_glob_min"] = round(mins)
        except Exception:
            pass
        # the stock's results / board meeting
        if u and not is_index and getattr(self.e, "context", None) is not None:
            try:
                ev = self.e.context.next_event(sym, today)
                if ev and ev["days"] <= 5:
                    facts["ev_results_d"] = ev["days"] if ev.get("results") else None
                    if ev["days"] <= 1:
                        what = f"{'Results' if ev.get('results') else 'Board meeting'} {'today' if ev['days'] == 0 else 'tomorrow'}"
                        add("high" if ev.get("results") else "medium", what)
            except Exception:
                pass
        # expiry day, ban list, holiday tomorrow
        try:
            if u and u.near_expiry(today) == today:
                add("medium", "Expiry day", "Expiry day: options lose value fast and prices can pin near big strikes.")
                facts["ev_expiry"] = 1
        except Exception:
            pass
        if sym in self._bans():
            add("high", "In the F&O ban today", "In the F&O ban: no new positions in its options or futures today.")
            facts["ev_ban"] = 1
        try:
            from .health import is_trading_day
            nxt = today + dt.timedelta(days=1)
            while nxt.weekday() >= 5:
                nxt += dt.timedelta(days=1)
            if not is_trading_day(nxt):
                add("low", f"Market shut on {nxt:%a %d %b} (holiday)", "Market shut on the next weekday: an overnight option "
                    "loses an extra day of time value.")
                facts["ev_holiday_next"] = 1
        except Exception:
            pass
        # fresh official news
        with self.lock:
            recent = [x for x in self.items if t - x["ts"] <= float(c["fresh_min_symbol"]) * 60 and x["ts"] <= t + 60]
        for x in recent:
            ago = int((t - x["ts"]) / 60)
            if sym in (x.get("syms") or []):
                add(x["impact"] if x["impact"] != "low" else "medium", f"{x['who']}: {x['title'][:90]} ({ago} min ago)",
                    f"Fresh {x['who']} news on {sym} {ago} min ago: {x['title'][:120]}")
                facts["ev_off_sym_min"] = min(ago, facts.get("ev_off_sym_min", 9999))
            elif not x.get("syms") and x["impact"] == "high" and t - x["ts"] <= float(c["fresh_min_market"]) * 60:
                hits_me = is_index or x["who"] != "RBI" or sym in banks
                if hits_me:
                    add("high" if (is_index or sym in banks) else "medium", f"{x['who']}: {x['title'][:90]} ({ago} min ago)",
                        f"Fresh {x['who']} news for the whole market {ago} min ago: {x['title'][:120]}")
                    facts["ev_off_mkt_min"] = min(ago, facts.get("ev_off_mkt_min", 9999))
        lvl = max((LEVEL[i["level"]] for i in items), default=0)
        facts["ev_level"] = lvl
        order = sorted(items, key=lambda i: -LEVEL[i["level"]])
        out = {"level": WORD[lvl], "items": order[:6], "warnings": [i["warn"] for i in order if i.get("warn")][:4],
               "line": ("Event risk: " + " · ".join(i["what"] for i in order[:3])) if order else None, "facts": facts}
        self._cache[key] = out
        return out

    # ------------------------------------------------------------ for the screens
    def view(self, sym: str | None = None) -> dict:
        c = cfg()
        t = time.time()
        with self.lock:
            items = list(self.items[:60])
        mkt = self.risk(sym or "NIFTY")
        return {"sources": [{"name": s["name"], "who": s.get("who"), **(self.status.get(s["name"]) or {"ok": None})}
                            for s in c.get("sources") or []],
                "today": mkt, "items": [{**x, "ago_min": round((t - x["ts"]) / 60)} for x in items[:40]],
                "error": _C.get("error"),
                "note": "Information and warnings only: event risk is shown on alerts and recorded as facts; it never blocks anything."}
