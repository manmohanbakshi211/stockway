"""Command line: python -m tanu <command>

  doctor                     check this machine (Python, packages, settings, data folder, disk)
  version                    print Tanu's version
  import-legacy              copy the legacy app's data into Tanu (legacy files only read)
  tape-summary <day> [src]   what a recorded day's tape holds (files, messages, silences)
  bars <day> [src]           build that day's 1-minute bars from its tape
  costs <day>                the day's tradeable list: round-trip cost of each ATM option (on trial)
  research                   replay every setup over history with option prices and evaluate (learn / holdout)
  search                     every setup x every exit variant x every filter (setups/exits.json), ledger-controlled
  review <day>               the evening review of the paper accounts for that day (from the journal)
  web                        open the app on this machine only (http://127.0.0.1:<web_port>, default 8790)
"""
from __future__ import annotations

import json
import sys


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "help"
    if cmd == "doctor":
        from .doctor import main as doctor
        return doctor()
    if cmd == "version":
        from . import __version__
        print(__version__)
        return 0
    if cmd == "web":
        import uvicorn

        from .core import config, log, paths
        from .ui.api import create_app
        s = config.load()
        d = paths.ensure(s)
        log.setup(d["logs"], s.log_level, s.log_keep_days)
        log.get("web").info("web_started", port=s.web_port)
        uvicorn.run(create_app(d["base"], paths.LEGACY_DATA / "history"), host="127.0.0.1", port=s.web_port,
                    log_level="warning")
        return 0
    if cmd in ("import-legacy", "tape-summary", "bars", "costs", "research", "search", "review"):
        from .core import config, log, paths
        s = config.load()
        d = paths.ensure(s)
        log.setup(d["logs"], s.log_level, s.log_keep_days)
        if cmd == "import-legacy":
            from .data import legacy
            rep = legacy.run(paths.LEGACY_DATA, d["base"])
            print(json.dumps(rep["totals"], indent=1))
            return 0
        if cmd == "search":
            from .evaluate.search import search
            rep = search(d["base"], paths.LEGACY_DATA / "history")
            print(open(rep["out"] + "/report.md").read())
            return 0
        if cmd == "research":
            from .evaluate.run import research
            rep = research(d["base"], paths.LEGACY_DATA / "history")
            print(open(rep["out"] + "/report.md").read())
            return 0
        if len(argv) < 2:
            print(__doc__)
            return 2
        day, src = argv[1], (argv[2] if len(argv) > 2 else "upstox")
        if cmd == "costs":
            return _costs(day, d)
        if cmd == "review":
            from .journal import review
            from .journal.store import Journal
            print(review.write(review.daily(Journal(d["base"] / "store" / "journal.db"), day), d["base"]).read_text())
            return 0
        if cmd == "tape-summary":
            from .data import tape
            print(json.dumps(tape.summary(d["tape"], src, day), indent=1, default=str))
            return 0
        from .data import bars
        print(bars.build_day(d["tape"], src, day, d["bars"]))
        return 0
    print(__doc__)
    return 0 if cmd in ("help", "-h", "--help") else 2


def _costs(day: str, d: dict) -> int:
    import pandas as pd

    from .core import paths
    from .costs import measure, tradeable
    from .data import instruments
    chains = next((p for p in (d["base"] / "chains" / f"{day}.parquet",
                               d["base"] / "imports" / "legacy" / "chains" / f"{day}.parquet") if p.exists()), None)
    if chains is None:
        print(f"No option-chain snapshots for {day}")
        return 1
    inst = sorted((d["base"] / "instruments").glob("*.json.gz")) or sorted((paths.LEGACY_DATA / "instruments").glob("NSE_*.json.gz"))
    lots = {s: u["lot"] for s, u in instruments.underlyings(instruments.load(inst[-1])).items()} if inst else {}
    lab_path = paths.LEGACY_DATA / "lab5y" / "lab" / "factory_bars.parquet"
    lab = measure.lab_drag(lab_path) if lab_path.exists() else None
    items = tradeable.build(day, measure.day_spreads(pd.read_parquet(chains), day), lots, lab=lab)
    out = tradeable.write(day, items, d["base"] / "costs")
    print(f"{sum(i['tradeable'] for i in items)} of {len(items)} tradeable -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
