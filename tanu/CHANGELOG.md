# Changelog

All notable changes to Tanu. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions:
[Semantic Versioning](https://semver.org/) (0.x while the core is being built).

## [0.2.0] - 2026-11-13
### Added
- T1 step 1, tick tape (`tanu/data/tape.py`, ADR-0006): append-only writer on its own thread (callers never wait for
  the disk), one file per IST hour, restarts open a new file (never overwrite), zstd per block so a crash loses at
  most the last second, pauses instead of filling the disk; reader, day reader and day summary (silences, control
  events). Not connected to any broker yet (ADR-0007).
- Tests: exact round trip of 5,000 binary messages, hour files and restarts, crash recovery, 100,000 writes in under
  2 s, low-disk pause, day summary.

## [0.1.0] - 2026-11-13
### Added
- T0 foundation: `tanu/` package beside the legacy app, with no imports either way.
- Settings: one typed object (`tanu/core/config.py`); load order defaults -> `tanu/settings.json` ->
  `data/tanu/local.json` -> `TANU_*` environment; unknown keys rejected.
- Structured JSON-line logs with run id, component and event; secrets masked (`tanu/core/log.py`).
- Market-data-only guard: trading and account URLs raise `BlockedCall` (`tanu/core/safety.py`).
- `python -m tanu doctor` machine check; `python -m tanu version`.
- Tests: boundaries (no Manu <-> Tanu imports), safety, settings, logs, doctor, command line.
- Docs: architecture, ADRs 0001-0007, roadmap, runbook, observability, data, testing, contributing, security, UX,
  glossary, rules for Claude.

### Decided
- Tanu replaces the legacy app; cutover only after Tanu runs a full market day cleanly (ADR-0002).
- No tick recorder attached to the legacy feed: Tanu's tape starts with Tanu's own feed (ADR-0007).
