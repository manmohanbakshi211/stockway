# ADR-0007: No overlap with the legacy app before cutover

- Status: Accepted
- Date: 2026-11-13
- Decided by: Mandeep ("ensure karna kuch bhi clash na ho ... manu ka or tanu ka kahi clash ya overlap na ho")

## Context
Both codebases live in one repo until cutover. The broker allows limited connections; the legacy app already runs one
live feed and one token keeper. Attaching Tanu's tick recorder to the legacy feed was considered and rejected: it would
put Tanu code inside a legacy process.

## Decision
- No imports between `tanu/` and the legacy packages, both ways (boundary tests).
- Tanu writes only under `data/tanu/`; legacy files are read-only to Tanu and copied by the import step.
- Tanu opens no broker connection and keeps no token before cutover. Tick data starts with Tanu's own feed.
- The legacy start script does not start any Tanu process before cutover.

## Consequences
- No tick tape for the days before cutover; those days use the legacy minute recordings (imported) and broker history.
- Tanu's feed is developed and tested against a local fake broker server, then switched on at cutover.
