# Data

Everything Tanu writes is under `data/tanu/` (setting `data_dir`; not in git). Legacy data under `data/` is read-only
to Tanu.

## Layout
| Folder | Holds | Format | Phase |
|---|---|---|---|
| `logs/` | structured logs | JSON lines, daily files | T0 ✅ |
| `tape/<source>/<YYYY-MM-DD>/HH[-n].tape` | every broker message as received (ADR-0006) | blocks of records, zstd per block | T1 ✅ writer + reader |
| `bars/<source>/<YYYY-MM-DD>.parquet` | 1-min bars per instrument from the tape (`src="tape"`), gaps filled from broker candles (`src="broker"`) | Parquet zstd | T1 ✅ |
| `chains/<YYYY-MM-DD>.parquet` | option chain snapshots | Parquet zstd | T1 |
| `history/<SYM>.parquet`, `history/daily/<SYM>.parquet` | imported 1-minute index history, daily stock history | Parquet zstd | T1 ✅ |
| `bars/legacy/<day>.parquet` | imported legacy 1-minute bars (key = symbol, `src="legacy"`) | Parquet zstd | T1 ✅ |
| `imports/legacy/{minutes,chains,live}/` + `report.json` | imported legacy minute snapshots, chains, live.db tables; the import report | Parquet zstd | T1 ✅ |
| `costs/tradeable_<day>.json`, `costs/all_days.parquet` | round-trip cost per underlying and the tradeable list (on trial) | JSON / Parquet | T2 ✅ |
| `store/journal.db` | paper trades with footprints (per account) and refused signals with reasons | SQLite | T4 ✅ |
| `journal/review_<day>.{md,json}`, `research/hypotheses.jsonl` | evening reviews and the questions they raise | Markdown / JSON | T4 ✅ |
| `local.json` | this machine's setting overrides | JSON | T0 ✅ |

## Tape format (built: `tanu/data/tape.py`, ADR-0006)
- File: header `TANUTAPE2\n` + one JSON line (`source`, `day`, `created`, `format`), then blocks.
- Block: `b"B" <u32 raw_len> <u32 zlen> <zstd bytes>`; each block is compressed on its own (written about once a
  second), so a crash loses only the block being written.
- Record inside a block: `<f64 receive_time><u8 kind><u32 length><payload>`, little-endian. Kind 0 = broker market
  message (raw bytes as received), 1 = control event (JSON: connect, disconnect, subscribe, unsubscribe ...).
- One file per IST hour; a restart in the same hour opens `HH-1.tape`, `HH-2.tape` (never overwrites).
- Reading: `tape.read_day(tape_dir, source, day)` yields `(time, kind, payload)` in order; `tape.summary(...)` gives
  counts, bytes, first / last time, silences and control events.

## Legacy sources to import (T1)
| Legacy file | Contents | Notes |
|---|---|---|
| `data/lab5y/lab/factory_bars.parquet` | 2021-2026, 51 symbols, bar features + real option P&L per exit | index every 5 min, stocks every 15 min |
| `data/lab/factory_bars.parquet` | last ~1-2 years, 32 symbols, 224 columns incl. strike choices | |
| `data/history/<SYM>/minutes.parquet` | 1-min index / stock history since 2022 | |
| `data/recordings/upstox_<day>_*.parquet` | per-minute snapshots, chains, metrics, candles, bars, news since 28 Oct 2026 | 12 Nov 09:07-10:02 missing; bars before 14:10 lost |
| `data/live.db` | alerts, outcomes, paper trades, plan outcomes, mind reads | read with `?mode=ro` |

## Dated facts (in git: `tanu/facts/`)
`charges.json` (option charges), `lots.json` (index lots), `calendar.json` (sessions, holidays, special sessions).
Each entry has `valid_from`; `tanu.data.facts.Facts` returns the value valid on a given day.

## Rules
- Point-in-time: a value is stored with the time it became known; nothing uses later data.
- Facts that change are dated; old dates keep old values.
- Derived data (bars, features) can always be rebuilt from the tape / history; never edit raw data.
- Retention: logs `log_keep_days`; tape by free disk (`min_free_disk_gb`); everything else kept.
