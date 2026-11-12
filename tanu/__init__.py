"""Tanu: the clean Stockway core (from 13 Nov 2026).

Market data only. Alerts and paper trades, never real orders. See tanu/README.md and tanu/docs/ARCHITECTURE.md.
Tanu never imports the legacy app (live/, lab/, webapp/, radar/); tanu/tests/test_tanu_boundaries.py enforces it.
"""
from pathlib import Path

__version__ = (Path(__file__).with_name("VERSION").read_text().strip())
