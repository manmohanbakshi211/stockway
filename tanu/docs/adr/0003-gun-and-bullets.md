# ADR-0003: Gun (engine) and bullets (dated data)

- Status: Accepted
- Date: 2026-11-13 (carries over Mandeep's rule of 31 Oct 2026)
- Decided by: Mandeep

## Context
"Gun aur bullet wala scene": the technical core is stable; strategies, styles, indicators, models, calendars, lot
sizes, cost rates, universes and thresholds change all the time.

## Decision
- The gun (feed, store, costs, state, engine, evaluator, paper, journal, UI) contains no strategy, date, lot size or
  threshold.
- Bullets live in registries / data files with a `valid_from` date; old dates keep old values.
- The gun loads each bullet in isolation: a bad bullet is skipped, logged and marked failed; the rest keeps running.
- A new bullet starts in shadow (computed and recorded, not shown or traded) until it passes ADR-0005.
- Adding a bullet must need no gun edit; if it does, the registry is fixed first.

## Consequences
- Bullets need a schema (pydantic) and a loader with per-bullet error isolation (T3).
- Every new input (book, video, idea) is first split into gun pieces and bullets.
