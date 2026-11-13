"""Research in steps: the same replay as `python -m tanu research`, cut into chunks of days so it can run where a
command may only last a few minutes (the Mac's VM shell). The engine's carried state (ATR, timeframe EMAs) is saved
between chunks, so the trades are exactly those of one long run.

  python -m tanu research-steps plan [days_per_step]   # what to run, from the option files
  python -m tanu research-steps step                   # the next chunk (repeat until it says done)
  python -m tanu research-steps finish                 # report (learn / holdout) from all chunks
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import pandas as pd

from ..core import log
from ..engine import replay
from ..engine.core import Engine
from ..engine.options import OptionHistory
from ..journal.store import Journal
from ..paper.accounts import Accounts
from ..setups import registry
from . import report
from .run import SPREAD_PCT

_log = log.get("research_steps")


def _dir(base: Path) -> Path:
    return Path(base) / "research" / "_steps"


def plan(base: Path | str, legacy_history: Path | str, syms=("NIFTY", "BANKNIFTY"), days_per_step: int = 60) -> dict:
    base, oh = Path(base), OptionHistory(legacy_history)
    chunks = []
    for s in syms:
        if not (base / "history" / f"{s}.parquet").exists():
            continue
        days = sorted(oh.days(s))
        for i in range(0, len(days), days_per_step):
            chunks.append({"sym": s, "days": days[i:i + days_per_step], "done": False})
    d = _dir(base)
    d.mkdir(parents=True, exist_ok=True)
    for f in d.glob("part_*.parquet"):
        f.unlink()
    p = {"legacy_history": str(legacy_history), "made": dt.datetime.now().isoformat(timespec="seconds"),
         "chunks": chunks, "carry": {}}
    (d / "plan.json").write_text(json.dumps(p))
    return {"chunks": len(chunks), "days": sum(len(c["days"]) for c in chunks)}


def step(base: Path | str, journal_path: Path | str | None = None) -> dict:
    base, d = Path(base), _dir(Path(base))
    p = json.loads((d / "plan.json").read_text())
    todo = [i for i, c in enumerate(p["chunks"]) if not c["done"]]
    if not todo:
        return {"done": True}
    i, t0 = todo[0], time.time()
    c = p["chunks"][i]
    good, _bad = registry.load()
    eng = Engine(good, OptionHistory(p["legacy_history"]), spread_pct=SPREAD_PCT)
    carry = p["carry"].get(c["sym"], {})
    if carry.get("atr") is not None:
        eng.carry_atr[c["sym"]] = carry["atr"]
    for k, v in carry.get("ema", {}).items():
        tf, n = map(int, k.split(":"))
        eng.carry_ema[(c["sym"], tf, n)] = v
    eng.listeners.append(Accounts(Journal(journal_path or base / "store" / "journal.db")))
    replay.run(eng, c["sym"], replay.load_minutes(base / "history" / f"{c['sym']}.parquet"), only_days=set(c["days"]))
    tr = pd.DataFrame(eng.trades())
    if len(tr):
        tr.to_parquet(d / f"part_{i:04d}.parquet", index=False)
    p["carry"][c["sym"]] = {"atr": eng.carry_atr.get(c["sym"]),
                            "ema": {f"{tf}:{n}": v for (s, tf, n), v in eng.carry_ema.items() if s == c["sym"]}}
    c["done"] = True
    (d / "plan.json").write_text(json.dumps(p))
    left = sum(not x["done"] for x in p["chunks"])
    _log.info("research_step", chunk=i, sym=c["sym"], days=len(c["days"]), trades=len(tr), left=left,
              seconds=round(time.time() - t0, 1))
    return {"chunk": i, "sym": c["sym"], "days": len(c["days"]), "trades": len(tr), "left": left,
            "seconds": round(time.time() - t0, 1)}


def finish(base: Path | str) -> dict:
    base, d = Path(base), _dir(Path(base))
    parts = sorted(d.glob("part_*.parquet"))
    trades = pd.concat([pd.read_parquet(f) for f in parts], ignore_index=True) if parts else pd.DataFrame()
    rep = report.evaluate(trades) if len(trades) else {"days": 0, "setups": []}
    rep.update(steps=len(parts), trades=len(trades))
    out = base / "research" / dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    out.mkdir(parents=True, exist_ok=True)
    trades.to_parquet(out / "trades.parquet", index=False)
    (out / "report.json").write_text(json.dumps(rep, indent=1, default=str))
    (out / "report.md").write_text(report.markdown(rep))
    _log.info("research_done", out=str(out), trades=len(trades))
    rep["out"] = str(out)
    return rep
