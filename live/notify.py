"""Telegram alerts, AI explanations / market pulse, and market news from free RSS feeds."""
from __future__ import annotations

import collections
import datetime as dt
import html
import queue
import re
import threading
import time
import xml.etree.ElementTree as ET

import requests

from .clock import IST

RSS = {
    "ET Markets": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "Business Standard": "https://www.business-standard.com/rss/markets-106.rss",
    "Mint": "https://www.livemint.com/rss/markets",
    "Moneycontrol": "https://www.moneycontrol.com/rss/marketreports.xml",
    "RBI": "https://www.rbi.org.in/pressreleases_rss.xml",
}

POS = ("surge", "jump", "rally", "soar", "gain", "rise", "up ", "beat", "record high", "upgrade", "order win", "bags",
       "approval", "buyback", "profit rises", "strong", "outperform", "bullish", "wins", "acquire")
NEG = ("fall", "drop", "slump", "plunge", "crash", "decline", "down ", "miss", "downgrade", "loss", "probe", "penalty",
       "raid", "fraud", "resign", "weak", "bearish", "cut", "selloff", "sell-off", "tumble", "ban", "default", "sebi order")


# ---------------------------------------------------------------- Telegram
class Telegram:
    def __init__(self, get_cfg, log=print):
        self.get_cfg = get_cfg
        self.log = log
        self.q = queue.Queue(maxsize=200)
        self.sent = collections.deque(maxlen=30)
        threading.Thread(target=self._run, daemon=True, name="telegram").start()

    def enabled(self):
        tg = self.get_cfg().get("telegram", {})
        return bool(tg.get("bot_token") and tg.get("chat_id"))

    def send(self, text):
        if self.enabled():
            try:
                self.q.put_nowait(text)
            except queue.Full:
                pass

    def _run(self):
        while True:
            text = self.q.get()
            now = time.time()
            while len(self.sent) >= 18 and now - self.sent[-18] < 60:      # stay well under Telegram limits
                time.sleep(2)
                now = time.time()
            tg = self.get_cfg().get("telegram", {})
            try:
                r = requests.post(f"https://api.telegram.org/bot{tg['bot_token']}/sendMessage",
                                  data={"chat_id": tg["chat_id"], "text": text, "parse_mode": "HTML",
                                        "disable_web_page_preview": "true"}, timeout=20)
                if r.status_code != 200:
                    self.log(f"Telegram failed: {r.text[:120]}")
            except Exception as e:
                self.log(f"Telegram failed: {e}")
            self.sent.append(time.time())


def telegram_text(a: dict) -> str:
    icon = {"up": "🟢", "down": "🔴"}.get(a["direction"], "🟡")
    sev = "‼️ " if a["severity"] >= 3 else ""
    t = f"{icon} {sev}<b>{html.escape(a['title'])}</b>\n{html.escape(a['detail'])}"
    o = a.get("option")
    if o:
        t += f"\nIdea: {html.escape(o['label'])} @ ₹{o['ltp']:.2f} (lot {o['lot']})"
    t += f"\n<i>{dt.datetime.fromtimestamp(a['ts'], IST):%H:%M:%S}</i>"
    return t


# ---------------------------------------------------------------- AI
class AI:
    """Gemini (free tier) or Claude. Only market data and public headlines are ever sent."""

    def __init__(self, get_cfg, log=print):
        self.get_cfg = get_cfg
        self.log = log
        self.day = None
        self.used = 0
        self.last_error = None

    def cfg(self):
        return self.get_cfg().get("ai", {}) or {}

    def enabled(self):
        c = self.cfg()
        return bool(c.get("api_key")) and c.get("provider") in ("gemini", "claude")

    def ask(self, prompt: str, max_tokens=400) -> str | None:
        if not self.enabled():
            return None
        c = self.cfg()
        today = dt.date.today()
        if self.day != today:
            self.day, self.used = today, 0
        if self.used >= int(c.get("daily_limit") or 200):
            self.last_error = "Daily AI limit reached"
            return None
        self.used += 1
        try:
            if c["provider"] == "gemini":
                model = c.get("model") or "gemini-flash-lite-latest"
                r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                  params={"key": c["api_key"]},
                                  json={"contents": [{"parts": [{"text": prompt}]}],
                                        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.3}},
                                  timeout=40)
                r.raise_for_status()
                parts = r.json()["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in parts)
            else:
                model = c.get("model") or "claude-haiku-5-5"
                r = requests.post("https://api.anthropic.com/v1/messages",
                                  headers={"x-api-key": c["api_key"], "anthropic-version": "2023-06-01",
                                           "content-type": "application/json"},
                                  json={"model": model, "max_tokens": max_tokens,
                                        "messages": [{"role": "user", "content": prompt}]}, timeout=40)
                r.raise_for_status()
                text = "".join(b.get("text", "") for b in r.json()["content"])
            self.last_error = None
            return text.strip()
        except Exception as e:
            self.last_error = str(e)[:200]
            self.log(f"AI request failed: {self.last_error}")
            return None


SYSTEM_STYLE = ("You are a sharp Indian F&O market desk analyst. Write in simple English a retail trader understands. "
                "Be specific with levels and numbers from the data. No disclaimers, no hype. ")


def pulse_prompt(snapshot: dict) -> str:
    return (SYSTEM_STYLE + "Here is the live market snapshot as JSON. Write exactly 4 short bullet points: "
            "1) overall mood and why, 2) Nifty view with key levels, 3) Bank Nifty view with key levels, "
            "4) the single most interesting stock or option activity right now and what it suggests. "
            "Each bullet under 30 words.\n\n" + _compact_json(snapshot))


def alert_prompt(alert: dict, metrics: dict | None, news: list) -> str:
    ctx = {"alert": {k: alert.get(k) for k in ("sym", "kind", "direction", "title", "detail", "price", "data")},
           "metrics": _slim_metrics(metrics) if metrics else None,
           "related_news": [n["title"] for n in news[:5]]}
    return (SYSTEM_STYLE + "Explain this live alert in 3 short bullet points: what happened, what is likely driving it "
            "(use the metrics and news), and what would confirm or cancel the move (exact levels). Under 80 words total.\n\n"
            + _compact_json(ctx))


def _slim_metrics(m):
    if not m:
        return None
    keep = ("sym", "spot", "chg_pct", "vwap", "rel_vol", "ret_5m", "ret_15m", "or_high", "or_low", "day_h", "day_l",
            "score", "reasons")
    out = {k: m.get(k) for k in keep}
    f = m.get("fut") or {}
    out["fut"] = {k: f.get(k) for k in ("oi_chg_pct", "buildup_label")}
    o = m.get("opt") or {}
    out["opt"] = {k: o.get(k) for k in ("atm", "atm_iv", "pcr", "max_pain", "resistance", "support", "exp_move_pct",
                                        "iv_chg_15m", "ce_writing", "pe_writing")}
    return out


def _compact_json(x):
    import json

    def r(v):
        if isinstance(v, float):
            return round(v, 2)
        if isinstance(v, dict):
            return {k: r(w) for k, w in v.items() if w is not None}
        if isinstance(v, list):
            return [r(w) for w in v]
        return v
    return json.dumps(r(x), default=str, separators=(",", ":"))


def rule_pulse(indices: list, alerts: list, movers: dict, vix: dict | None) -> list:
    """Plain-language market pulse without AI (always available)."""
    out = []
    ups = sum(1 for m in indices if (m.get("chg_pct") or 0) > 0)
    mood = "positive" if ups > len(indices) / 2 else "negative" if ups < len(indices) / 2 else "mixed"
    v = f" India VIX {vix['ltp']:.2f} ({vix['chg_pct']:+.1f}%)." if vix and vix.get("ltp") and vix.get("chg_pct") is not None else ""
    out.append(f"Mood is {mood}: {ups} of {len(indices)} indices up.{v}")
    for m in indices[:2]:
        o = m.get("opt") or {}
        parts = [f"{m['name']} {m['spot']:,.0f} ({m.get('chg_pct') or 0:+.2f}%)"]
        if m.get("vwap"):
            parts.append("above VWAP" if m["spot"] > m["vwap"] else "below VWAP")
        if o.get("support") and o.get("resistance"):
            parts.append(f"range {o['support']:,.0f}–{o['resistance']:,.0f} by option OI")
        if o.get("pcr") is not None:
            parts.append(f"PCR {o['pcr']:.2f}")
        if m.get("reasons"):
            parts.append(", ".join(m["reasons"][:2]))
        out.append(", ".join(parts) + ".")
    if movers.get("hot"):
        h = movers["hot"][0]
        rv = f" at {h['rel_vol']:.1f}× normal volume" if (h.get("rel_vol") or 0) >= 0.1 else ""
        out.append(f"Most active: {h['sym']} {h.get('chg_pct') or 0:+.2f}%{rv}.")
    if alerts:
        out.append(f"Latest: {alerts[0]['title']}.")
    return out


# ---------------------------------------------------------------- news
class News:
    def __init__(self, log=print, feeds=None):
        self.log = log
        self.feeds = feeds or RSS
        self.items = []          # newest first
        self.seen = set()
        self.last_fetch = 0.0
        self.errors = {}

    def fetch(self, symbols: dict):
        """symbols: SYMBOL -> list of name words to match in headlines."""
        new = []
        for src, url in self.feeds.items():
            try:
                r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (Macintosh) Stockway"})
                r.raise_for_status()
                root = ET.fromstring(r.content)
                for it in root.iter("item"):
                    title = html.unescape((it.findtext("title") or "").strip())
                    link = (it.findtext("link") or "").strip()
                    if not title or title in self.seen:
                        continue
                    self.seen.add(title)
                    pub = it.findtext("pubDate") or ""
                    try:
                        from email.utils import parsedate_to_datetime
                        ts = parsedate_to_datetime(pub).timestamp()
                    except Exception:
                        try:      # e.g. "2026-10-28 22:54:37" (UTC)
                            ts = dt.datetime.strptime(pub.strip()[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()
                        except Exception:
                            ts = time.time()
                    new.append({"title": title, "link": link, "source": src, "ts": ts,
                                "syms": match_symbols(title, symbols), "tone": tone(title)})
                self.errors.pop(src, None)
            except Exception as e:
                self.errors[src] = str(e)[:120]
        self.items = sorted(new + self.items, key=lambda x: -x["ts"])[:300]
        self.last_fetch = time.time()
        return new


def tone(title: str) -> int:
    t = " " + title.lower() + " "
    p = sum(w in t for w in POS)
    n = sum(w in t for w in NEG)
    return (p > n) - (n > p)


def match_symbols(title: str, symbols: dict) -> list:
    t = title.lower()
    out = []
    for sym, words in symbols.items():
        for w in words:
            if w and re.search(r"(?<![a-z])" + re.escape(w.lower()) + r"(?![a-z])", t):
                out.append(sym)
                break
    return out[:5]


GENERIC = {"bharat", "state", "power", "national", "indian", "hindustan", "united", "general", "punjab", "union",
           "central", "finance", "motors", "steel", "capital", "energy", "infra", "global", "life", "tata", "bajaj",
           "adani", "mahindra", "reliance", "godrej", "aditya", "jindal", "oil", "bank", "housing", "torrent"}


def symbol_words(universe) -> dict:
    extra = {"NIFTY": ["nifty", "sensex", "market", "stock market", "dalal street"],
             "BANKNIFTY": ["bank nifty", "banking stocks", "psu banks", "rbi"],
             "FINNIFTY": ["fin nifty", "financial stocks"], "MIDCPNIFTY": ["midcap"]}
    out = {}
    for sym, u in universe.underlyings.items():
        if sym in extra:
            out[sym] = extra[sym]
            continue
        words = {sym.lower()}
        name = (u.name or "").lower()
        name = re.sub(r"\b(ltd|limited|india|industries|corporation|corp|co|company|of|the|and|&)\b\.?", " ", name)
        name = " ".join(name.split())
        if len(name) >= 4:
            words.add(name)
            first = name.split()[0]
            if len(first) >= 6 and first not in GENERIC:
                words.add(first)
        out[sym] = sorted(words)
    return out
