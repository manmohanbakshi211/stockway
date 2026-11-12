"""Command line: python -m tanu <command>

  doctor    check this machine (Python, packages, settings, data folder, disk)
  version   print Tanu's version
"""
from __future__ import annotations

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
    print(__doc__)
    return 0 if cmd in ("help", "-h", "--help") else 2


if __name__ == "__main__":
    raise SystemExit(main())
