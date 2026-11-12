# ADR-0006: Raw tick tape as the source of truth for replay

- Status: Accepted
- Date: 2026-11-13

## Context
The legacy recorder saved one snapshot per instrument per minute, 1-min bars and periodic chains. On 12 Nov 2026 it
missed ~52 minutes in the morning (feed resets) and lost the morning 1-min bars on a restart. A day could not be
replayed exactly.

## Decision
- The feed process writes every broker message, as received, to an append-only tape: header + records of
  (receive time, kind, length, bytes); kind = market message or control event (connect, disconnect, subscribe).
- Compressed with zstd block by block (about one block a second, so a crash loses at most the last block), one file
  per hour per day under `data/tanu/tape/<source>/<YYYY-MM-DD>/` (format in [DATA](../DATA.md)).
- Written by a writer thread inside the feed process, so disk speed never slows reading.
- Bars, chains and features are derived from the tape and can always be rebuilt.
- End of day: gaps checked against the broker's own 1-min candles; missing minutes filled from them and marked.
- Retention by free disk (warn below `min_free_disk_gb`, stop the tape before the disk fills); size measured on day 1.

## Consequences
- Exact replay of any recorded day at any speed (ADR-0004).
- Needs a tape reader, a decoder per broker message format, and a size / health card on the System page.
