# Changelog

All notable changes to Tanu. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions:
[Semantic Versioning](https://semver.org/) (0.x while the core is being built).

## [0.3.0] - 2026-11-13
### Added
- T1 data layer, part 2:
  - Feed process (`tanu/data/feed.py`): Upstox V3 websocket in its own process; every message to the tape byte for
    byte (plus connect / disconnect / subscribe events); decoded ticks to the app; reconnect with backoff and
    resubscribe; restarts a dead process; URL checked by the safety guard. Tested on a local fake broker only (no
    broker connection before cutover, ADR-0007).
  - Upstox adapter (`tanu/brokers/upstox/`): the V3 message format vendored in a private protobuf pool (cannot clash
    with the legacy copy), a decoder for every feed shape, a market-data REST client (rate limits, retries, login
    errors, guard).
  - 1-minute bars from the tape (`tanu/data/bars.py`): exchange trade time, per-minute volume from the cumulative
    day volume, last OI / bid / ask, session minutes only; a rebuild gives identical bars.
  - Gap check and fill (`tanu/data/gaps.py`): missing session minutes per instrument, filled from the broker's 1-minute
    candles and marked `src="broker"`.
  - Dated facts (`tanu/facts/`, `tanu/data/facts.py`): charges, index lot sizes, sessions / holidays / Muhurat, copied
    from the legacy rules; option charges per trade.
  - Legacy import (`tanu/data/legacy.py`, `python -m tanu import-legacy`): history (1-minute index, daily stocks),
    recordings (bars, minute snapshots, chains), live.db (alerts, outcomes, paper, plan outcomes, trials, mind reads);
    Lab tables and 1.8 GB of expired-option / end-of-day files listed and read in place. Legacy files only read.
  - CLI: `import-legacy`, `tape-summary <day>`, `bars <day>`.
- Run on the Mac (13 Nov, 04:0x IST, Tanu code run from a scratch copy, legacy app untouched): 908,876 history rows,
  11,971,932 recording rows from 29 files, 151,902 live.db rows, Lab tables 3,807,977 rows listed; 16 s, 160 MB.
- Tests: 46 (fake-broker feed with a dropped connection, decoder shapes, REST, bars, gaps, facts, import).

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
