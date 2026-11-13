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
  run                        what Start Tanu runs: the app, and after cutover the live market day (ADR-0009)
  rehearse <day>             play a past day through the whole live path (fake broker) and check it against replay
  cutover [--undo]           turn the live switch on (Mandeep's go only) or off again
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
        uvicorn.run(create_app(d["base"], paths.LEGACY_DATA / "history", settings=s), host="127.0.0.1", port=s.web_port,
                    log_level="warning")
        return 0
    if cmd == "run":
        from .live.app import Supervisor
        return Supervisor().run()
    if cmd == "cutover":
        from .core import config, log, paths
        from .live.app import cutover
        s = config.load()
        d = paths.ensure(s)
        log.setup(d["logs"], s.log_level, s.log_keep_days)
        print(cutover(d["base"], undo="--undo" in argv[1:], s=s))
        return 0
    if cmd == "rehearse":
        from .core import config, log, paths
        from .live.rehearse import rehearse
        if len(argv) < 2:
            print(__doc__)
            return 2
        s = config.load()
        d = paths.ensure(s)
        log.setup(d["logs"], s.log_level, s.log_keep_days)
        rep = rehearse(argv[1], d["base"], paths.LEGACY_DATA / "history")
        print(json.dumps({k: v for k, v in rep.items() if k != "differences"}, indent=1, default=str))
        if rep["differences"]:
            print("differences:", json.dumps(rep["differences"][:5], indent=1, default=str))
        return 0 if rep["passed"] else 1
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
    from .core import paths
    from .costs import daily
    out, msg = daily.run(day, d["base"], paths.LEGACY_DATA)
    print(msg)
    return 0 if out else 1


if __name__ == "__main__":
    raise SystemExit(main())
