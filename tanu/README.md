# Tanu — Stockway core

Tanu is the clean rebuild of Stockway: an F&O **alerts + paper trading** system for the Indian market, built the way a
professional option trader and a senior engineer would build it. It replaces the legacy app (live/, lab/, webapp/,
"Manu") from 13 Nov 2026. **Market data only — no real orders, ever.**

| | |
|---|---|
| Owner | Mandeep (product decisions, defaults, go-live) |
| Status | T0 done; T1 data mostly done; T2 costs done; T3 engine + evaluator + controlled search done (research 0001 / 0002: no setup passes yet); T4 paper accounts + risk + journal + review done; T5 light app done; cutover pieces built and rehearsed on real days (v0.9.0, switch off until Mandeep's go, [ADR-0009](docs/adr/0009-cutover.md)). Legacy app still runs until then (see [ROADMAP](docs/ROADMAP.md)). |
| Version | [`VERSION`](VERSION) · changes in [`CHANGELOG.md`](CHANGELOG.md) |

## What it will do
1. Record the market (ticks, 1-min bars, option chains) for indices and F&O stocks.
2. Know the cost of trading each instrument, every day, and only buy where it is cheap.
3. Read the market state (trend / range / quiet day, with / against the move, volatility, expiry).
4. Run setups (from research, books, videos) through one engine — the same code for history replay and live paper.
5. Promote a setup only when it wins on data it never saw; journal every paper trade; review every evening.
6. Later: swing, positional, investing, ETFs, mutual funds — as plugins on the same core.

## Quick start
```bash
python -m tanu doctor        # is this machine ready? (Python, packages, settings, data folder, disk)
python -m tanu version
python -m pytest tanu/tests  # Tanu's tests (the legacy suite is tests/)
python -m tanu run           # the app (view-only until cutover); "Start Tanu.command" does this with its own environment
python -m tanu rehearse 2026-11-06   # a past day through the whole live path, checked against replay
```

## Layout
```
tanu/
  core/          settings, paths, structured logs, safety guard (shared by every layer)
  doctor.py      machine check
  __main__.py    command line
  settings.json  committed defaults (machine overrides: data/tanu/local.json)
  data/          tape, feed process, bars, gaps, dated facts, instrument list, legacy import
  brokers/       Upstox: feed protobuf, decode, market-data REST, token keeper
  costs/ state/ setups/ engine/ evaluate/ paper/ journal/   the research and paper core (one engine, ADR-0004)
  live/          universe, live bars, live option book, live loop, session, supervisor, fake broker, rehearsal
  notify/        Telegram sender and alert cards
  ui/            the app (Today, Setups, Journal, System)
  tests/         Tanu's tests
  docs/          architecture, decisions (ADR), runbook, data, testing, contributing, security, roadmap
```
See [ARCHITECTURE](docs/ARCHITECTURE.md).

## Documents
- [Architecture](docs/ARCHITECTURE.md) · [Decisions (ADR)](docs/adr/README.md) · [Roadmap](docs/ROADMAP.md)
- [Runbook](docs/RUNBOOK.md) · [Observability](docs/OBSERVABILITY.md) · [Data](docs/DATA.md)
- [Testing](docs/TESTING.md) · [Contributing](docs/CONTRIBUTING.md) · [Security](docs/SECURITY.md)
- [UX](docs/UX.md) · [Glossary](docs/GLOSSARY.md) · Rules for Claude: [CLAUDE.md](CLAUDE.md)
