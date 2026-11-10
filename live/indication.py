"""India market indication (gun, 2.40): one word for where the Indian market is headed, on the Live page.

Mandeep: in the empty space of the Live page's market strip, a simple read of the whole Indian market (Nifty, Bank Nifty,
Fin Nifty, Midcap Select and Sensex together, not only Nifty): Gap up / Gap down before the open, Positive / Negative /
Neutral after it, with one or two short lines, marked as an AI suggestion. Claude writes it every 15 minutes from 8:30
to 4:00 PM (8:30, 8:45, 9:00, 9:15 ...) from what drives the market: GIFT Nifty's move, global indices and their
pressure on Nifty, US futures, crude, USD/INR, the VIXes, FII / DII, today's events and the latest headlines (and,
after the open, the indices' moves, breadth and the day type). After 4:00 PM the last reading stays until the morning.

Slots, thresholds and the AI allowance are bullets in rules/indication.json. Without AI (or past its allowance, or on a
bad answer) the same word comes from rules, and the tile says so. Every reading is saved; after the close the last
pre-open reading is graded (did the market open with that gap, did it close that way), so the tile can say how often
it was right. Information only: never a signal, never a trade.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
from pathlib import Path

from .clock import IST

FILE = Path(__file__).resolve().parents[1] / "rules" / "indication.json"
DEFAULTS = {"start": "08:30", "end": "16:00", "every_min": 15, "ai": True, "max_ai_per_day": 32, "late_ok_min": 10,
            "gap_pct": 0.35, "lean_pct": 0.12, "session_pct": 0.4, "headlines": 8, "insider_days": 5}
INDICES = [("Nifty", "NIFTY", "^NSEI"), ("Bank Nifty", "BANKNIFTY", "^NSEBANK"), ("Fin Nifty", "FINNIFTY", "NIFTY_FIN_SERVICE.NS"),
           ("Midcap Select", "MIDCPNIFTY", "NIFTY_MID_SELECT.NS"), ("Sensex", "SENSEX", "^BSESN")]
GLOBAL = [("S&P 500 fut", "ES=F"), ("Nasdaq fut", "NQ=F"), ("Dow fut", "YM=F"), ("Nikkei", "^N225"), ("Hang Seng", "^HSI"),
          ("Shanghai", "000001.SS"), ("Kospi", "^KS11"), ("Taiwan", "^TWII"), ("ASX 200", "^AXJO"), ("FTSE", "^FTSE"),
          ("DAX", "^GDAXI"), ("Brent crude", "BZ=F"), ("Gold", "GC=F"), ("USD/INR", "INR=X"), ("Dollar index", "DX-Y.NYB"),
          ("US 10Y yield", "^TNX"), ("US VIX", "^VIX"), ("India VIX", "^INDIAVIX"), ("Bitcoin", "BTC-USD")]
WORDS = ("Gap up", "Gap down", "Positive", "Negative", "Neutral")
OPEN_T, CLOSE_T = dt.time(9, 15), dt.time(15, 30)
_C: dict = {}


def cfg() -> dict:
    try:
        mt = FILE.stat().st_mtime
        if _C.get("mt") != mt:
            raw = json.loads(FILE.read_text())
            _C.update(mt=mt, cfg={**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}, error=None)
    except Exception as e:
        _C.update(cfg=_C.get("cfg") or dict(DEFAULTS), error=str(e)[:160])
    return _C["cfg"]


def _t(hhmm: str) -> dt.time:
    h, m = str(hhmm).split(":")
    return dt.time(int(h), int(m))


def slots(day: dt.date, c: dict | None = None) -> list:
    """The day's reading times (IST): start, start + every_min, ... end."""
    c = c or cfg()
    t = dt.datetime.combine(day, _t(c["start"]), IST)
    end = dt.datetime.combine(day, _t(c["end"]), IST)
    out = []
    while t <= end:
        out.append(t)
        t += dt.timedelta(minutes=max(5, int(c["every_min"])))
    return out


def phase(now: dt.datetime) -> str:
    t = now.astimezone(IST).time()
    return "pre-open" if t < OPEN_T else "session" if t < CLOSE_T else "closed"


def _num(x, nd=2):
    try:
        return None if x is None else round(float(x), nd)
    except Exception:
        return None


def rules_word(b: dict, c: dict | None = None) -> dict:
    """The word from rules alone (also the fallback when AI is off or wrong)."""
    c = c or cfg()
    ph = b.get("phase")
    gap = (b.get("gift") or {}).get("chg_pct")
    glob = (b.get("pressure") or {}).get("implied_pct")
    if ph == "pre-open":
        lean = 0.6 * (gap or 0) + 0.4 * (glob or 0)
        if gap is not None and gap >= c["gap_pct"]:
            w = "Gap up"
        elif gap is not None and gap <= -c["gap_pct"]:
            w = "Gap down"
        else:
            w = "Positive" if lean >= c["lean_pct"] else "Negative" if lean <= -c["lean_pct"] else "Neutral"
        parts = []
        if gap is not None:
            parts.append(f"GIFT Nifty {gap:+.2f}%")
        if glob is not None:
            parts.append(f"global pressure {glob:+.2f}%")
        return {"word": w, "line": (", ".join(parts) + ".") if parts else "Too little data yet.", "lean": round(lean, 2)}
    avg = b.get("avg_chg")
    if avg is None:
        return {"word": "Neutral", "line": "No index prices yet.", "lean": None}
    w = "Positive" if avg >= c["session_pct"] else "Negative" if avg <= -c["session_pct"] else "Neutral"
    br = b.get("breadth") or {}
    more = f"; {br['adv']} up / {br['dec']} down" if br.get("adv") is not None else ""
    lead = "Closed" if ph == "closed" else "Indices"
    return {"word": w, "line": f"{lead} {avg:+.2f}% on average{more}.", "lean": avg}


def parse_ai(text: str | None, ph: str) -> dict | None:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None
    w = str(d.get("word") or "").strip().lower()
    word = next((x for x in WORDS if x.lower() == w), None)
    line = re.sub(r"\s+", " ", str(d.get("line") or "")).strip()
    if not word or not line:
        return None
    if ph != "pre-open" and word.startswith("Gap"):
        word = "Positive" if word == "Gap up" else "Negative"          # after the open there is no gap to call
    words = line.split()
    if len(words) > 32:
        line = " ".join(words[:32]) + "…"
    return {"word": word, "line": line}


class Indication:
    def __init__(self, engine):
        self.e = engine
        self.lock = threading.Lock()
        self.busy = False
        self.ai_used: dict = {}
        self.last_error = None
        try:
            self.latest = engine.store.get("indication:latest") or None
        except Exception:
            self.latest = None

    # ---------------- schedule
    def _trading_day(self, d: dt.date) -> bool:
        try:
            from .health import is_trading_day
            return is_trading_day(d)
        except Exception:
            return d.weekday() < 5

    def due(self, now: dt.datetime) -> dt.datetime | None:
        """The slot to run now, if any: the latest one that has passed and is not done (late by up to late_ok_min,
        or any lateness when nothing has been read today yet)."""
        c = cfg()
        d = now.date()
        if not self._trading_day(d):
            return None
        done = set(self._done(d.isoformat()))
        past = [s for s in slots(d, c) if s <= now]
        if not past:
            return None
        s = past[-1]
        if s.strftime("%H:%M") in done:
            return None
        if (now - s).total_seconds() <= int(c["late_ok_min"]) * 60 or not done:
            return s
        return None

    def _done(self, day: str) -> list:
        try:
            return [x.get("at") for x in (self.e.store.get(f"indication:{day}") or [])]
        except Exception:
            return []

    def tick(self, now: dt.datetime | None = None):
        """Called by the app's watcher every ~20 s: starts a reading in its own thread when a slot is due."""
        now = now or dt.datetime.now(IST)
        s = self.due(now)
        if s is None:
            return None
        with self.lock:
            if self.busy:
                return None
            self.busy = True
        threading.Thread(target=self._run_safe, args=(s,), daemon=True, name="india-indication").start()
        return s

    def _run_safe(self, slot):
        try:
            self.run(slot)
        except Exception as ex:
            self.last_error = str(ex)[:200]
            try:
                self.e.log(f"Market indication failed: {ex}")
            except Exception:
                pass
        finally:
            with self.lock:
                self.busy = False

    # ---------------- what moves the market now
    def bundle(self, now: dt.datetime) -> dict:
        e, c = self.e, cfg()
        ph = phase(now)
        world = getattr(e, "world", None)
        rows = (getattr(world, "rows", {}) or {}) if world else {}
        live = (getattr(e, "metrics", None) or {}) if getattr(e, "running", False) else {}
        idx = []
        for name, sym, tick in INDICES:
            m = live.get(sym) or {}
            r = rows.get(tick) or {}
            chg = m.get("chg_pct") if m.get("spot") else None
            src = "live"
            if chg is None:
                chg, src = r.get("chg_pct"), ("previous session" if ph == "pre-open" else "Yahoo")
            if chg is None and sym == "SENSEX":
                try:
                    sx = next((x for x in e.extra_indices() if x.get("sym") == "SENSEX"), None) or {}
                    chg, src = sx.get("chg_pct"), "live"
                except Exception:
                    pass
            last = m.get("spot") or r.get("last")
            if chg is not None or last:
                idx.append({"name": name, "last": _num(last), "chg_pct": _num(chg), "from": src})
        session_idx = [x["chg_pct"] for x in idx if x["chg_pct"] is not None and x["from"] != "previous session"]
        g = {}
        try:
            g = dict(e.gift.get() or {}) if getattr(e, "gift", None) is not None else {}
        except Exception:
            g = {}
        glob = []
        for name, tick in GLOBAL:
            r = rows.get(tick) or {}
            if r.get("chg_pct") is not None:
                glob.append({"name": name, "chg_pct": _num(r.get("chg_pct")), "last": _num(r.get("last"))})
        try:
            pr = world.pressure() if world else {}
        except Exception:
            pr = {}
        ctx = {}
        try:
            cv = e.context.view()
            fd = cv.get("fii_dii") or {}
            ctx["fii_dii_cr"] = {k: {"net": _num((fd.get(k) or {}).get("net"), 0), "date": (fd.get(k) or {}).get("date")} for k in ("FII", "DII") if fd.get(k)}
            fii = next((r for r in ((cv.get("participants") or {}).get("rows") or []) if r.get("who") == "FII"), None)
            if fii:
                ctx["fii_index_futures_long_pct"] = fii.get("fut_long_pct")
        except Exception:
            pass
        ev = []
        try:
            if getattr(e, "events", None) is not None:
                ev = [i.get("what") for i in (e.events.risk("NIFTY", now).get("items") or [])][:6]
        except Exception:
            ev = []
        heads = []
        try:
            cut = time.time() - 6 * 3600
            for n in (getattr(getattr(e, "news", None), "items", None) or []):
                if (n.get("ts") or 0) >= cut and n.get("title"):
                    heads.append(f"{n.get('source') or ''}: {n['title']}"[:160])
                if len(heads) >= int(c["headlines"]):
                    break
            if getattr(e, "events", None) is not None:
                for x in (getattr(e.events, "items", None) or [])[:20]:
                    if x.get("impact") in ("high", "medium") and (x.get("ts") or 0) >= cut:
                        heads.append(f"{x.get('who')}: {x.get('title')}"[:160])
                heads = heads[:int(c["headlines"]) + 4]
        except Exception:
            pass
        ins = {}
        try:                                         # 2.50 (Mandeep #2): insider / promoter trades, slow background
            if getattr(e, "insiders", None) is not None:
                ins = e.insiders.tilt(int(c.get("insider_days") or 5), weights=getattr(getattr(e, "context", None), "weights", None))
        except Exception:
            ins = {}
        mk = (getattr(e, "market", None) or {}) if getattr(e, "running", False) else {}
        br = mk.get("breadth") or {}
        return {"time": now.strftime("%H:%M"), "day": now.date().isoformat(), "phase": ph, "indices": idx,
                "avg_chg": round(sum(session_idx) / len(session_idx), 2) if session_idx else None,
                "gift": {"last": _num(g.get("spot")), "chg_pct": _num(g.get("chg_pct")), "vs_nifty_pts": _num(g.get("vs_nifty"), 1),
                         "when": g.get("vs_nifty_when")} if g.get("spot") else {},
                "pressure": {"implied_pct": pr.get("implied_pct"), "label": pr.get("label"),
                             "drivers": [f"{d['name']} {d['move']:+.1f}%" for d in (pr.get("drivers") or [])[:4]]} if pr else {},
                "global": glob, "context": ctx, "events": ev, "headlines": heads, "insiders": ins,
                "breadth": {"adv": br.get("adv"), "dec": br.get("dec")} if br else {},
                "day_type": (mk.get("regime") or {}).get("label")}

    def _ask_ai(self, b: dict, rc: dict) -> dict | None:
        c = cfg()
        ai = getattr(self.e, "ai", None)
        if not c.get("ai") or ai is None or not ai.enabled():
            return None
        day = b["day"]
        if self.ai_used.get(day, 0) >= int(c["max_ai_per_day"]):
            self.last_error = "AI allowance for the market indication is used up today"
            return None
        lang = ((self.e.cfg().get("ai") or {}).get("language") or "English") if hasattr(self.e, "cfg") else "English"
        allowed = "Gap up, Gap down, Positive, Negative or Neutral" if b["phase"] == "pre-open" else "Positive, Negative or Neutral"
        prompt = ("You are an Indian equity market analyst. From ONLY the data below, say where the Indian stock market as a "
                  "whole (Nifty, Bank Nifty, Fin Nifty, Midcap, Sensex together) is likely headed "
                  + ("at the open today" if b["phase"] == "pre-open" else "for the rest of today's session" if b["phase"] == "session"
                     else "into tomorrow (the market has closed)")
                  + f". Reply ONLY with JSON {{\"word\": one of {allowed}, \"line\": one or two short sentences, at most 25 words, "
                  f"plain {lang}, the main reasons with numbers}}. Gap up / Gap down only if a clear gap is likely. Be balanced; "
                  "if signals conflict say Neutral. 'insiders' = promoter / director market trades disclosed in the last days: "
                  "slow background for single stocks, never a reason on its own for the whole market's next move."
                  "\n\nData: " + json.dumps(b, default=str))
        try:
            text = ai.ask(prompt, 250, auto=True, budget=("indication", int(c["max_ai_per_day"])))
        except TypeError:                             # an AI client without separate allowances
            text = ai.ask(prompt, 250, auto=True)
        self.ai_used[day] = self.ai_used.get(day, 0) + 1
        out = parse_ai(text, b["phase"])
        if out is None:
            self.last_error = getattr(ai, "last_error", None) or "the AI answer could not be read"
        return out

    # ---------------- one reading
    def run(self, slot: dt.datetime, now: dt.datetime | None = None) -> dict:
        now = now or dt.datetime.now(IST)
        b = self.bundle(now)
        rc = rules_word(b)
        ai = self._ask_ai(b, rc)
        rec = {"at": slot.strftime("%H:%M"), "day": slot.date().isoformat(), "ts": time.time(), "phase": b["phase"],
               "word": (ai or rc)["word"], "line": (ai or rc)["line"], "by": "AI" if ai else "rules",
               "rules": rc, "avg_chg": b.get("avg_chg"), "gift_pct": (b.get("gift") or {}).get("chg_pct"),
               "global_pct": (b.get("pressure") or {}).get("implied_pct"), "error": None if ai else self.last_error,
               "inputs": {k: b.get(k) for k in ("indices", "gift", "pressure", "events", "breadth", "day_type")},
               "insiders": b.get("insiders") or None}         # 2.50: recorded with every reading (graded later)
        day = rec["day"]
        try:
            hist = [x for x in (self.e.store.get(f"indication:{day}") or []) if x.get("at") != rec["at"]] + [rec]
            self.e.store.put(f"indication:{day}", hist[-60:])
            self.e.store.put("indication:latest", rec)
        except Exception:
            pass
        self.latest = rec
        if slot.time() >= _t(cfg()["end"]):
            try:
                self.grade(day, b)
            except Exception as ex:
                self.last_error = f"grading: {ex}"[:200]
        return rec

    def grade(self, day: str, b: dict | None = None) -> dict | None:
        """After the close: was the last pre-open reading right about the open and about the close?"""
        hist = self.e.store.get(f"indication:{day}") or []
        pre = [x for x in hist if x.get("phase") == "pre-open"]
        if not pre:
            return None
        p = pre[-1]
        e = self.e
        nifty_open = prev = None
        try:
            bars = e.charts.today_bars("NIFTY") if getattr(e, "charts", None) is not None else []
            if bars and dt.datetime.fromtimestamp(bars[0][0], IST).date().isoformat() == day:
                nifty_open = float(bars[0][1])
            prev = ((getattr(e, "daily", {}) or {}).get("NIFTY") or {}).get("prev")
        except Exception:
            pass
        c = cfg()
        res = {"day": day, "said": p["word"], "by": p.get("by")}
        if nifty_open and prev:
            g = (nifty_open / float(prev) - 1) * 100
            actual = "Gap up" if g >= c["gap_pct"] else "Gap down" if g <= -c["gap_pct"] else "flat"
            res.update(open_gap_pct=round(g, 2), open=actual,
                       open_right=(p["word"] == actual) if p["word"].startswith("Gap") else (actual == "flat"))
        avg = (b or {}).get("avg_chg")
        if avg is not None:
            want = 1 if p["word"] in ("Gap up", "Positive") else -1 if p["word"] in ("Gap down", "Negative") else 0
            got = 1 if avg >= c["session_pct"] else -1 if avg <= -c["session_pct"] else 0
            res.update(close_avg_pct=avg, close_right=want == got)
        rec = e.store.get("indication:record") or {"days": []}
        rec["days"] = [x for x in rec.get("days", []) if x.get("day") != day] + [res]
        rec["days"] = rec["days"][-250:]
        e.store.put("indication:record", rec)
        return res

    def record(self) -> dict:
        try:
            days = (self.e.store.get("indication:record") or {}).get("days") or []
        except Exception:
            days = []
        o = [d["open_right"] for d in days if d.get("open_right") is not None]
        cl = [d["close_right"] for d in days if d.get("close_right") is not None]
        return {"open_n": len(o), "open_right": sum(o), "close_n": len(cl), "close_right": sum(cl)}

    def view(self) -> dict:
        now = dt.datetime.now(IST)
        nxt = next((s for s in slots(now.date()) if s > now), None) if self._trading_day(now.date()) else None
        if nxt is None:
            d = now.date() + dt.timedelta(days=1)
            while not self._trading_day(d):
                d += dt.timedelta(days=1)
            nxt = slots(d)[0] if slots(d) else None
        return {"latest": self.latest, "next": nxt.isoformat() if nxt else None, "busy": self.busy, "record": self.record(),
                "ai_used_today": self.ai_used.get(now.date().isoformat(), 0), "max_ai_per_day": cfg()["max_ai_per_day"]}
