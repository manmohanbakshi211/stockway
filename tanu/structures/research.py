"""`python -m tanu structures`: replay every structure over the real option history, evaluate (learn / holdout, luck
bar for the ideas tried; no random yardstick, the bar is "above zero beyond luck"), write the report and the
journal's multi-leg table (account shadow_multileg)."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd

from ..core import log
from ..engine.options import OptionHistory
from ..evaluate import report
from ..evaluate.run import SPREAD_PCT
from ..journal.store import Journal
from . import registry
from .runner import Runner

_log = log.get("structures")


def run(base: Path | str, legacy_history: Path | str, syms=("NIFTY", "BANKNIFTY"), structures=None,
        journal_path: Path | str | None = None) -> dict:
    base = Path(base)
    good, bad = registry.load() if structures is None else (structures, [])
    mins = {}
    for s in syms:
        p = base / "history" / f"{s}.parquet"
        if p.exists():
            mins[s] = pd.read_parquet(p, columns=["t", "c"])
    rows = Runner(OptionHistory(legacy_history), mins, SPREAD_PCT).run(good, list(mins))
    df = pd.DataFrame(rows)
    done = df[df.get("skipped").isna()] if "skipped" in df else df
    skipped = df["skipped"].value_counts().to_dict() if "skipped" in df else {}
    ev = done.rename(columns={"structure": "setup"}).assign(minutes=lambda x: (x["exit_t"] - x["entry_t"]) / 60,
                                                            entry=lambda x: x["credit"]) if len(done) else done
    rep = report.evaluate(ev, baseline=None, min_trades=20, min_hold=5, k=len(good)) if len(done) else {"setups": []}
    rep.update(structures=[s.id for s in good], bad=bad, trades=len(done), skipped=skipped)
    out = base / "research" / ("structures_" + dt.datetime.now().strftime("%Y-%m-%d_%H%M"))
    out.mkdir(parents=True, exist_ok=True)
    df.to_json(out / "trades.json", orient="records", default_handler=str)
    (out / "report.json").write_text(json.dumps(rep, indent=1, default=str))
    (out / "report.md").write_text(report.markdown(rep) if rep.get("setups") else "No structure trades.")
    j = Journal(journal_path or base / "store" / "journal.db")
    for r in done.to_dict("records"):
        j.structure({**r, "account": "shadow_multileg"})
    _log.info("structures_done", trades=len(done), skipped=sum(skipped.values()), out=str(out))
    rep["out"] = str(out)
    return rep
