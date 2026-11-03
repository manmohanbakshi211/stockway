"""Data vault (gun): what is being recorded for future backtests, and how complete each day is.

Settings are bullets in rules/vault.json. Recording itself lives in live/record.py (minutes, metrics, and since 2.31
full option chains, headlines and market context); the engine's background sweep fetches the stock chains with spare
Upstox calls only. Everything stays on this computer in data/recordings.
"""
from __future__ import annotations

import json
from pathlib import Path

FILE = Path(__file__).resolve().parents[1] / "rules" / "vault.json"
DEFAULTS = {"stock_chains_every_min": 30, "start": "09:20", "end": "15:20", "max_upstox_share": 0.7, "context_every_min": 5}
PARTS = ("minutes", "metrics", "chains", "news", "context")
_C: dict = {}


def cfg() -> dict:
    try:
        mt = FILE.stat().st_mtime
        if _C.get("mt") != mt:
            raw = json.loads(FILE.read_text())
            _C.update(mt=mt, cfg={**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}})
        return _C["cfg"]
    except Exception:
        return dict(DEFAULTS)


def days(data_dir, source: str = "upstox", limit: int = 30) -> list:
    """Past recorded days, newest first: [{day, parts: {minutes: MB, ...}, mb}]."""
    d = Path(data_dir) / "recordings"
    if not d.exists():
        return []
    out = {}
    for f in d.glob(f"{source}_*"):
        bits = f.stem.split("_")
        if len(bits) < 2:
            continue
        day = bits[1]
        part = bits[2] if len(bits) > 2 else ("live" if f.suffix == ".sqlite" else "?")
        x = out.setdefault(day, {"day": day, "parts": {}, "mb": 0.0})
        mb = f.stat().st_size / 1e6
        x["parts"][part] = round(x["parts"].get(part, 0) + mb, 1)
        x["mb"] = round(x["mb"] + mb, 1)
    return sorted(out.values(), key=lambda x: x["day"], reverse=True)[:limit]


def view(engine) -> dict:
    rec = getattr(engine, "recorder", None)
    today = None
    if rec is not None and engine.running:
        try:
            today = {**rec.health(), "counts": dict(getattr(rec, "counts", {}))}
        except Exception:
            today = None
    every = days(engine.data_dir, engine.source or "upstox", limit=100000)
    return {"cfg": cfg(), "today": today, "days": every[:30], "n_days": len(every),
            "total_mb": round(sum(x["mb"] for x in every), 1),
            "chain_sweep": getattr(engine, "vault_last", None), "parts": PARTS,
            "note": "Recorded on this computer for backtests: every streamed price each minute, full option chains, "
                    "headlines and the market around them. Kept for the days set in Data & settings (365 by default)."}
