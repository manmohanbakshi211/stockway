"""Strategies from your trading ebooks, tested like everything else.

Put PDF, EPUB or text files in data/ebooks (or upload them on the Strategy Lab page). For each book the app finds the
passages that describe concrete rules (entries, stops, targets, indicators), asks Claude (your Pro plan, through
Claude Code on this Mac) to turn each rule into the Strategy Lab's rule format, and queues those rules for the next
research run, where they face the same train / validation / holdout test as any other idea. Rules that need facts the
app doesn't have yet are listed as "can't test yet", with the reason.

The book's text stays on your Mac and goes only to Claude for this; nothing is published. Claude is told not to
quote the book, only to describe the rule.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import zipfile
from pathlib import Path

from .factory import EXIT_WORDS, FEATURES, LAST_ENTRY_TOD, add_ideas

KEYWORDS = ("entry", "enter", "buy", "sell", "short", "stop", "stop-loss", "stop loss", "target", "exit", "breakout",
            "breakdown", "moving average", "ema", "sma", "rsi", "macd", "vwap", "support", "resistance", "gap",
            "opening range", "candle", "volume", "trend", "pullback", "retrace", "high", "low", "range", "momentum",
            "oversold", "overbought", "divergence", "volatility", "vix", "option", "premium", "intraday", "minute")
CHUNK = 12000
MAX_CHUNKS = 10


def book_dir(data_dir) -> Path:
    d = Path(data_dir) / "ebooks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_file(data_dir) -> Path:
    return Path(data_dir) / "lab" / "ebooks.json"


def load_state(data_dir) -> dict:
    f = state_file(data_dir)
    try:
        return json.loads(f.read_text()) if f.exists() else {}
    except Exception:
        return {}


def books(data_dir) -> list:
    st = load_state(data_dir)
    out = []
    for p in sorted(book_dir(data_dir).iterdir()):
        if p.suffix.lower() in (".pdf", ".epub", ".txt", ".md"):
            s = st.get(p.name, {})
            out.append({"name": p.name, "mb": round(p.stat().st_size / 1e6, 1), "done": bool(s.get("at")), "at": s.get("at"),
                        "rules": len(s.get("strategies", [])), "cannot": s.get("cannot_test", [])[:12], "error": s.get("error"),
                        "chunks": s.get("chunks")})
    return out


def text_of(path: Path) -> str:
    suf = path.suffix.lower()
    if suf == ".pdf":
        from pypdf import PdfReader
        r = PdfReader(str(path))
        return "\n".join(f"[page {i + 1}] " + (pg.extract_text() or "") for i, pg in enumerate(r.pages))
    if suf == ".epub":
        out = []
        with zipfile.ZipFile(path) as z:
            for n in sorted(z.namelist()):
                if n.lower().endswith((".xhtml", ".html", ".htm")):
                    h = z.read(n).decode("utf-8", "ignore")
                    out.append(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h)))
        return "\n".join(out)
    return path.read_text(errors="ignore")


def pick_chunks(text: str, size=CHUNK, k=MAX_CHUNKS) -> list:
    """The k passages densest in rule words (entries, stops, indicators), in book order."""
    parts = [text[i:i + size] for i in range(0, len(text), size)]
    def score(p):
        t = p.lower()
        return sum(t.count(w) for w in KEYWORDS) / max(1, len(t) / 1000)
    ranked = sorted(range(len(parts)), key=lambda i: -score(parts[i]))[:k]
    return [parts[i] for i in sorted(ranked)]


def prompt(book: str, chunk: str) -> str:
    feats = "; ".join(f"{k} = {v[0]} ({v[1] or 'number'})" for k, v in FEATURES.items())
    exits = "; ".join(f"{k} = {v}" for k, v in EXIT_WORDS.items())
    return (
        "You turn trading-book rules into testable rules for an Indian index-options backtester (Nifty, Bank Nifty; "
        "intraday; the trade is always buying the at-the-money call for a long or put for a short; 5-minute bars). "
        "Read the passage and extract ONLY concrete, testable intraday rules. Map each onto these facts, known at the "
        f"close of a 5-minute bar: {feats}. Time is minutes since 09:15 (15 = 09:30, {LAST_ENTRY_TOD} = 14:45). "
        f"Exit rules available: {exits}. Pick the closest exit. Thresholds must be numbers in the units given. "
        "If a rule needs facts not in the list (volume profile, order book, specific patterns not expressible, "
        "multi-day holds, stocks), put it in cannot_test with a short reason. Do not quote the book; describe in your "
        "own words. Do not invent rules the passage doesn't state.\n"
        'Reply with JSON only: {"strategies": [{"dir": 1 or -1, "conds": [["feature", ">" or "<", number], ...], '
        '"win": [from_min, to_min], "exit": "std", "max_day": 1, "syms": ["NIFTY", "BANKNIFTY"], '
        '"note": "one line: the idea in your words + where in the book (page/chapter)"}], '
        '"cannot_test": [{"idea": "short", "needs": "what fact is missing"}]}\n\n'
        f"BOOK: {book}\nPASSAGE:\n{chunk}")


def _json(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def extract(data_dir, name: str, ai, log=print, should_stop=lambda: False) -> dict:
    """Read one book, ask Claude chunk by chunk, queue the rules for the next Strategy Lab run."""
    p = book_dir(data_dir) / name
    if not p.exists():
        raise FileNotFoundError(name)
    if not ai or not ai.enabled():
        raise PermissionError("Connect Claude (your Pro plan) in Live setup → AI first.")
    st = load_state(data_dir)
    rec = {"strategies": [], "cannot_test": [], "error": None}
    try:
        text = text_of(p)
    except Exception as e:
        rec["error"] = f"Could not read the file: {e}"
        st[name] = rec
        state_file(data_dir).write_text(json.dumps(st))
        raise
    if len(text.strip()) < 500:
        rec["error"] = "Almost no text found (a scanned book needs OCR first)."
    chunks = pick_chunks(text)
    rec["chunks"] = len(chunks)
    for i, ch in enumerate(chunks):
        if should_stop():
            break
        log(f"{name}: passage {i + 1} of {len(chunks)} → Claude")
        out = _json(ai.ask(prompt(name, ch), 1800, auto=False))
        rec["strategies"] += out.get("strategies") or []
        rec["cannot_test"] += out.get("cannot_test") or []
    q = add_ideas(data_dir, rec["strategies"], source=f"ebook:{name}"[:80])
    rec.update(at=dt.datetime.now().isoformat(timespec="seconds"), queued=q["queued"], rejected=q["rejected"])
    st[name] = rec
    state_file(data_dir).parent.mkdir(parents=True, exist_ok=True)
    state_file(data_dir).write_text(json.dumps(st))
    log(f"{name}: {q['queued']} testable rules queued, {q['rejected']} not usable, {len(rec['cannot_test'])} need facts we don't have")
    return rec
