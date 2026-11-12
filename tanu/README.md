# Tanu — Stockway core

Tanu is the clean rebuild of Stockway: an F&O **alerts + paper trading** system for the Indian market, built the way a
professional option trader and a senior engineer would build it. It replaces the legacy app (live/, lab/, webapp/,
"Manu") from 13 Nov 2026. **Market data only — no real orders, ever.**

| | |
|---|---|
| Owner | Mandeep (product decisions, defaults, go-live) |
| Status | T0 foundation done; T1 data in progress (v0.2.0: tick tape). Legacy app still runs until cutover (see [ROADMAP](docs/ROADMAP.md)). |
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
```

## Layout
```
tanu/
  core/          settings, paths, structured logs, safety guard (shared by every layer)
  doctor.py      machine check
  __main__.py    command line
  settings.json  committed defaults (machine overrides: data/tanu/local.json)
  tests/         Tanu's tests (boundaries, safety, core)
  docs/          architecture, decisions (ADR), runbook, data, testing, contributing, security, roadmap
```
Planned layers (T1+): `data/` (recorder, tape, bars, import), `costs/`, `state/`, `setups/`, `engine/`, `evaluate/`,
`risk/`, `journal/`, `ui/`. See [ARCHITECTURE](docs/ARCHITECTURE.md).

## Documents
- [Architecture](docs/ARCHITECTURE.md) · [Decisions (ADR)](docs/adr/README.md) · [Roadmap](docs/ROADMAP.md)
- [Runbook](docs/RUNBOOK.md) · [Observability](docs/OBSERVABILITY.md) · [Data](docs/DATA.md)
- [Testing](docs/TESTING.md) · [Contributing](docs/CONTRIBUTING.md) · [Security](docs/SECURITY.md)
- [UX](docs/UX.md) · [Glossary](docs/GLOSSARY.md) · Rules for Claude: [CLAUDE.md](CLAUDE.md)
