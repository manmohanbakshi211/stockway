# ADR-0004: One engine for history replay and live paper

- Status: Accepted
- Date: 2026-11-13

## Context
The legacy app had separate logic for the Lab, the bench, live alerts and several paper accounts, so a rule could
behave differently in testing and live.

## Decision
- One event-driven engine consumes a stream of market events. The source is either the live feed or a recorded tape /
  bar history; the engine cannot tell them apart.
- Same inputs give the same outputs (deterministic): a replay of a recorded day reproduces that day's signals and
  paper trades exactly. A golden replay test guards it.
- Time comes from the events, never from the wall clock, inside the engine.

## Consequences
- Fast research needs a vectorised path too; it must be checked against the engine on samples (same trades).
- Live-only concerns (reconnects, delays) stay in the feed layer.
