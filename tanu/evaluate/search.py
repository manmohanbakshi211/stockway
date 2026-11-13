"""Controlled search ("Dr. Strange done right", ADR-0005): every entry setup x every exit variant x every filter in
tanu/setups/exits.json, replayed once through the engine and judged against the yardstick with the same exit and
filter. Every idea ever tried is written to a ledger (<base>/research/ledger.jsonl); the pass bar uses the ledger's
total count, so trying more ideas makes passing harder, never easier."""
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
from ..setups import registry
from ..setups.registry import Exit, Setup
from . import report
from .run import SPREAD_PCT

VARIANTS = Path(__file__).resolve().parents[1] / "setups" / "exits.json"
PRETESTS = Path(__file__).resolve().parents[1] / "setups" / "pretests.json"
BASELINE = "random_baseline"
_log = log.get("search")


def combos(entries: list[Setup], variants: dict) -> list[Setup]:
    out = []
    for e in entries:
        if e.status == "retired":
            continue
        for x in variants["exits"]:
            for f in variants["filters"]:
                d = e.model_dump()
                d.update(id=f"{e.id}__{x['name']}__{f['name']}", when=list(e.when) + [tuple(c) for c in f["when"]],
                         exit=Exit(**x))
                out.append(Setup(**d))
    return out


def ledger_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {json.loads(line)["id"] for line in path.read_text().splitlines() if line.strip()}


def pretest_ids(path: Path = PRETESTS) -> set[str]:
    """Ideas looked at on index data before becoming (or instead of) setups: they count as tried."""
    try:
        return {i["id"] for i in json.loads(Path(path).read_text())["ideas"]}
    except FileNotFoundError:
        return set()


def search(base: Path | str, legacy_history: Path | str, syms=("NIFTY", "BANKNIFTY"), entries=None,
           variants_path: Path = VARIANTS, options=None) -> dict:
    base, t0 = Path(base), time.time()
    entries = entries if entries is not None else registry.load()[0]
    variants = json.loads(Path(variants_path).read_text())
    setups = combos(entries, variants)
    oh = options or OptionHistory(legacy_history)
    eng = Engine(setups, oh, spread_pct=SPREAD_PCT)
    for sym in syms:
        hist = base / "history" / f"{sym}.parquet"
        if hist.exists():
            only = oh.days(sym) if hasattr(oh, "days") else None
            replay.run(eng, sym, replay.load_minutes(hist), only_days=only)
    trades = pd.DataFrame(eng.trades())
    research_dir = base / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    led = research_dir / "ledger.jsonl"
    tried = ledger_ids(led) | {s.id for s in setups} | {s.id + "@sell" for s in setups if s.sell.on} | pretest_ids()
    k = len([i for i in tried if not (i.startswith(BASELINE + "__") or i == BASELINE + "@sell")])
    rep = report.evaluate(trades, baseline=BASELINE, k=k) if len(trades) else {"days": 0, "setups": [], "k": k}
    rep.update(combos=len(setups), seconds=round(time.time() - t0))
    run_id = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    out = research_dir / f"search_{run_id}"
    out.mkdir(parents=True, exist_ok=True)
    trades.to_parquet(out / "trades.parquet", index=False)
    (out / "report.json").write_text(json.dumps(rep, indent=1, default=str))
    (out / "report.md").write_text(report.markdown(rep))
    with open(led, "a") as f:
        for r in rep["setups"]:
            f.write(json.dumps({"id": r["setup"], "run": run_id, "learn": r["learn"], "hold": r["hold"],
                                "passes": r.get("passes")}, default=str) + "\n")
    _log.info("search_done", combos=len(setups), k=k, passes=sum(bool(r.get("passes")) for r in rep["setups"]),
              seconds=rep["seconds"])
    rep["out"] = str(out)
    return rep
