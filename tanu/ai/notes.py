"""The morning note (before the open) and the evening note (after the close), written by the AI from the same
snapshot as Ask Tanu and saved in data/tanu/ai/notes/<day>_<kind>.md (with a .json of who wrote it and when)."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .client import AI
from .context import RULES, snapshot

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

ASK = {
    "morning": ("Write Mandeep's morning note for {day}, before the market opens. Up to 10 short bullets in simple English "
                "(a few Hinglish words are fine): 1) world cues and GIFT Nifty, and what they suggest for the open; "
                "2) FII / DII cash and who holds index futures; 3) events, results and the F&O ban list today; "
                "4) what Tanu's paper accounts and research honestly say (say plainly if nothing has passed the tests); "
                "5) what to watch today, with levels only if the data has them. Start with a one-line headline."),
    "evening": ("Write Mandeep's evening review of {day}. Up to 12 short bullets: what the market did (from the live day "
                "and the alerts), each paper account's result in rupees and % per trade, which setups fired and how they "
                "did, the biggest alerts, FII / DII and insider flows, and end with up to 3 questions worth testing next "
                "(as hypotheses with how Tanu would test them, never as trades). Start with a one-line headline."),
}


def folder(base: Path | str) -> Path:
    return Path(base) / "ai" / "notes"


def write(kind: str, base: Path | str, ai: AI, collector, book=None, day: str | None = None, user: bool = True) -> dict:
    if kind not in ASK:
        raise ValueError(f"no note called {kind}")
    day = day or dt.datetime.fromtimestamp(ai.now(), IST).date().isoformat()
    snap = snapshot(base, collector, "", book)
    prompt = RULES + "\n\nDATA (JSON):\n" + json.dumps(snap, default=str, separators=(",", ":")) + "\n\nTASK:\n" + ASK[kind].format(day=day)
    r = ai.ask(prompt, job=kind, user=user)
    f = folder(base)
    f.mkdir(parents=True, exist_ok=True)
    (f / f"{day}_{kind}.md").write_text(r["text"] + "\n")
    meta = {"day": day, "kind": kind, "model": r["model"], "secs": r["secs"],
            "at": dt.datetime.fromtimestamp(ai.now(), IST).isoformat(timespec="minutes")}
    (f / f"{day}_{kind}.json").write_text(json.dumps(meta))
    return {**meta, "text": r["text"]}


def latest(base: Path | str, n: int = 2) -> list[dict]:
    """The newest notes first (by the time they were written)."""
    f = folder(base)
    out = []
    for p in sorted(f.glob("*.md"), reverse=True)[:20] if f.exists() else []:
        try:
            meta = json.loads(p.with_suffix(".json").read_text())
        except Exception:
            meta = {"day": p.stem.split("_")[0], "kind": p.stem.split("_")[-1], "at": p.stem.split("_")[0]}
        out.append({**meta, "text": p.read_text()[:6000]})
    return sorted(out, key=lambda x: x.get("at") or "", reverse=True)[:n]


def due(kind: str, base: Path | str, now: dt.datetime, at: str, trading_day: bool) -> bool:
    """A scheduled note is due once a trading day, from its time, if not written yet."""
    if not trading_day or now.strftime("%H:%M") < at:
        return False
    return not (folder(base) / f"{now.date().isoformat()}_{kind}.md").exists()
