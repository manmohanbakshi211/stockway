"""Command line: python -m tanu <command>

  doctor                     check this machine (Python, packages, settings, data folder, disk)
  version                    print Tanu's version
  import-legacy              copy the legacy app's data into Tanu (legacy files only read)
  tape-summary <day> [src]   what a recorded day's tape holds (files, messages, silences)
  bars <day> [src]           build that day's 1-minute bars from its tape
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
    if cmd in ("import-legacy", "tape-summary", "bars"):
        from .core import config, log, paths
        s = config.load()
        d = paths.ensure(s)
        log.setup(d["logs"], s.log_level, s.log_keep_days)
        if cmd == "import-legacy":
            from .data import legacy
            rep = legacy.run(paths.LEGACY_DATA, d["base"])
            print(json.dumps(rep["totals"], indent=1))
            return 0
        if len(argv) < 2:
            print(__doc__)
            return 2
        day, src = argv[1], (argv[2] if len(argv) > 2 else "upstox")
        if cmd == "tape-summary":
            from .data import tape
            print(json.dumps(tape.summary(d["tape"], src, day), indent=1, default=str))
            return 0
        from .data import bars
        print(bars.build_day(d["tape"], src, day, d["bars"]))
        return 0
    print(__doc__)
    return 0 if cmd in ("help", "-h", "--help") else 2


if __name__ == "__main__":
    raise SystemExit(main())
