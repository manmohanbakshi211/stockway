"""Research run: replay every setup in the library over the history that has option prices, then evaluate.
Outputs <base>/research/<YYYY-MM-DD_HHMM>/{trades.parquet, signals.parquet, report.json, report.md}."""
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

SPREAD_PCT = {"NIFTY": 0.27, "BANKNIFTY": 0.33}     # measured median ATM spread (Tanu 0.4.0, 4-12 Nov 2026)
_log = log.get("research")


def research(base: Path | str, legacy_history: Path | str, syms=("NIFTY", "BANKNIFTY"), setups=None) -> dict:
    base, t0 = Path(base), time.time()
    good, bad = registry.load() if setups is None else (setups, [])
    oh = OptionHistory(legacy_history)
    eng = Engine(good, oh, spread_pct=SPREAD_PCT)
    journal = Journal(base / "store" / "journal.db")          # every replayed trade, per paper account, with footprints
    eng.listeners.append(Accounts(journal))
    for sym in syms:
        hist = base / "history" / f"{sym}.parquet"
        if not hist.exists():
            continue
        replay.run(eng, sym, replay.load_minutes(hist), only_days=oh.days(sym))
    trades, signals = pd.DataFrame(eng.trades()), pd.DataFrame(eng.signals)
    rep = report.evaluate(trades) if len(trades) else {"days": 0, "setups": []}
    rep.update(bad_setups=bad, signals=len(signals), skipped=int(signals.get("skipped", pd.Series()).notna().sum()) if len(signals) else 0,
               unfilled=int((trades["reason"] == "no_exit_price").sum()) if len(trades) else 0, seconds=round(time.time() - t0))
    out = base / "research" / dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    out.mkdir(parents=True, exist_ok=True)
    trades.to_parquet(out / "trades.parquet", index=False)
    signals.to_parquet(out / "signals.parquet", index=False)
    (out / "report.json").write_text(json.dumps(rep, indent=1, default=str))
    (out / "report.md").write_text(report.markdown(rep))
    _log.info("research_done", out=str(out), trades=len(trades), seconds=rep["seconds"])
    rep["out"] = str(out)
    return rep
