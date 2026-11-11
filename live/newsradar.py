"""News radar (2.64): the fast AI reads every new headline and says what it means, next to the keyword tone.

Mandeep, 11 Nov 2026: "kya kya cheej bhot jaldi pakad sakta hai". Until now a headline's direction came only from
keywords (live/notify.py tone: "rise" = good, "fall" = bad), and its stocks only from name matching.

Gun: new headlines (RSS, Dhan, official feeds the app already reads) are batched every couple of minutes and sent to
the fast AI tier (rules/ai_models.json "news": Haiku) with the app's stock list. For each it returns the impact (0-3),
the direction for the market or the named stocks (-1 / 0 / +1), the NSE symbols it is clearly about, and a few words
why. Shadow first: nothing changes the news alerts (still keyword based). Each call is graded 30 minutes later on the
price of its stock (or Nifty): was the AI's direction right, was the keyword tone right? The page shows both scores;
the AI takes over the news alerts only when it is clearly better, on Mandeep's say-so.
Only public headlines and symbol names are sent. Paper and information only.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

GRADE_AFTER_S = 1800          # judge a call on the price 30 minutes later
MOVE_PCT = 0.25               # a move smaller than this is "no move" (neither right nor wrong)
BATCH = 25
EVERY_S = 120


def prompt(items: list, symbols: list) -> str:
    lines = "\n".join(f"{i}. {it['title'][:220]}" for i, it in enumerate(items))
    return ("You classify Indian stock-market headlines for an F&O trader, fast and strictly. For each numbered headline "
            "return one JSON object: {\"i\": number, \"impact\": 0-3 (0 = no market effect, 1 small, 2 clear for the named "
            "stock or sector, 3 market-moving), \"dir\": -1, 0 or 1 (price direction it implies for the stocks it names, "
            "or for the Indian market if it names none), \"syms\": [NSE symbols from the list below that it is clearly "
            "about, at most 3], \"why\": \"at most 8 words\"}. Reply with only a JSON list, nothing else.\n"
            f"NSE symbols: {', '.join(symbols)}\n\nHeadlines:\n{lines}")


def parse(text: str | None, n: int, symbols: set) -> dict:
    """{index: {"impact", "dir", "syms", "why"}} from the AI's reply; anything malformed is dropped."""
    if not text:
        return {}
    m = re.search(r"\[.*\]", text, re.S)
    try:
        arr = json.loads(m.group(0) if m else text)
    except Exception:
        return {}
    out = {}
    for x in arr if isinstance(arr, list) else []:
        try:
            i = int(x.get("i"))
            if not 0 <= i < n:
                continue
            imp = max(0, min(3, int(x.get("impact") or 0)))
            d = int(x.get("dir") or 0)
            d = 1 if d > 0 else -1 if d < 0 else 0
            syms = [str(s).upper() for s in (x.get("syms") or []) if str(s).upper() in symbols][:3]
            out[i] = {"impact": imp, "dir": d, "syms": syms, "why": str(x.get("why") or "")[:80]}
        except Exception:
            continue
    return out


class NewsRadar:
    def __init__(self, ai, data_dir, log=print):
        self.ai, self.log = ai, log
        self.file = Path(data_dir) / "news_radar.json"
        self.pending: list = []
        self.calls: list = []            # graded or waiting: {ts, title, sym, ai_dir, tone, impact, p0, ...}
        self.last_run = 0.0
        self.runs = 0
        self.errors = 0
        self.lock = threading.Lock()
        self._load()

    def _load(self):
        try:
            d = json.loads(self.file.read_text())
            self.calls = d.get("calls", [])[-2000:]
        except Exception:
            self.calls = []

    def _save(self):
        try:
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"calls": self.calls[-2000:]}))
            tmp.replace(self.file)
        except Exception:
            pass

    def add(self, items: list):
        now = time.time()
        with self.lock:
            for it in items:
                if now - float(it.get("ts") or now) <= 1800:
                    self.pending.append(it)
            self.pending = self.pending[-200:]

    def run(self, metrics: dict, symbols: list, now: float | None = None, force: bool = False) -> int:
        """Classify the waiting headlines (one batch) if it is time; returns how many were classified."""
        now = now or time.time()
        if not self.pending or (not force and now - self.last_run < EVERY_S) or not self.ai or not self.ai.enabled():
            return 0
        with self.lock:
            batch, self.pending = self.pending[:BATCH], self.pending[BATCH:]
        self.last_run = now
        text = self.ai.ask(prompt(batch, symbols), 1200, auto=True, task="news")
        res = parse(text, len(batch), set(symbols))
        self.runs += 1
        if not res:
            self.errors += 1
            return 0
        for i, r in res.items():
            it = batch[i]
            it["ai"] = r                                   # shown with the headline; the alert itself is unchanged
            sym = (r["syms"] or it.get("syms") or ["NIFTY"])[0]
            p0 = (metrics.get(sym) or {}).get("spot")
            if p0 and (r["dir"] or it.get("tone")):
                self.calls.append({"ts": now, "title": it["title"][:160], "sym": sym, "ai_dir": r["dir"], "impact": r["impact"],
                                   "tone": int(it.get("tone") or 0), "p0": float(p0), "kw_syms": it.get("syms") or [],
                                   "ai_syms": r["syms"], "why": r["why"]})
        self._save()
        return len(res)

    def grade(self, metrics: dict, now: float | None = None) -> int:
        now = now or time.time()
        n = 0
        for c in self.calls:
            if "move" in c or now - c["ts"] < GRADE_AFTER_S:
                continue
            p = (metrics.get(c["sym"]) or {}).get("spot")
            if not p:
                continue
            mv = (float(p) / c["p0"] - 1) * 100
            c["move"] = round(mv, 3)
            s = 1 if mv > MOVE_PCT else -1 if mv < -MOVE_PCT else 0
            c["ai_right"] = None if not c["ai_dir"] or not s else c["ai_dir"] == s
            c["tone_right"] = None if not c["tone"] or not s else c["tone"] == s
            n += 1
        if n:
            self._save()
        return n

    def view(self) -> dict:
        g = [c for c in self.calls if "move" in c]

        def score(key, rows):
            dec = [c[key] for c in rows if c.get(key) is not None]
            return {"decided": len(dec), "right_pct": round(100 * sum(dec) / len(dec), 1) if dec else None}
        big = [c for c in g if c.get("impact", 0) >= 2]
        return {"calls": len(self.calls), "graded": len(g), "runs": self.runs, "errors": self.errors,
                "ai": score("ai_right", g), "keywords": score("tone_right", g), "ai_big": score("ai_right", big),
                "extra_syms": sum(1 for c in self.calls if set(c.get("ai_syms") or []) - set(c.get("kw_syms") or [])),
                "latest": [{k: c.get(k) for k in ("ts", "title", "sym", "ai_dir", "impact", "tone", "why", "move", "ai_right", "tone_right")}
                           for c in self.calls[-12:][::-1]]}
