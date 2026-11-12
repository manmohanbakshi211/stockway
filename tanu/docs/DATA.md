# Data

Everything Tanu writes is under `data/tanu/` (setting `data_dir`; not in git). Legacy data under `data/` is read-only
to Tanu.

## Layout
| Folder | Holds | Format | Phase |
|---|---|---|---|
| `logs/` | structured logs | JSON lines, daily files | T0 ✅ |
| `tape/<source>/<YYYY-MM-DD>/HH.tape.zst` | every broker message as received (ADR-0006) | binary records, zstd | T1 |
| `bars/<YYYY-MM-DD>.parquet` | 1-min OHLCV + OI per instrument, from the tape; filled gaps marked | Parquet zstd | T1 |
| `chains/<YYYY-MM-DD>.parquet` | option chain snapshots | Parquet zstd | T1 |
| `facts/` | dated facts: lot sizes, expiries, charges, holidays, events (`valid_from`) | JSON / CSV | T1 |
| `store/` | small transactional state: alerts, paper trades, journal | SQLite | T4 |
| `imports/` | copies of legacy data in Tanu's format + an import report | Parquet | T1 |
| `local.json` | this machine's setting overrides | JSON | T0 ✅ |

## Tape record format (T1, ADR-0006)
File header `TANUTAPE1\n` + JSON line (source, day, broker format version). Then records:
`<f64 receive_time><u8 kind><u32 length><bytes>`; kind 0 = broker market message (raw), 1 = control event (JSON:
connect, disconnect, subscribe, unsubscribe with instrument keys and mode). Little-endian.

## Legacy sources to import (T1)
| Legacy file | Contents | Notes |
|---|---|---|
| `data/lab5y/lab/factory_bars.parquet` | 2021-2026, 51 symbols, bar features + real option P&L per exit | index every 5 min, stocks every 15 min |
| `data/lab/factory_bars.parquet` | last ~1-2 years, 32 symbols, 224 columns incl. strike choices | |
| `data/history/<SYM>/minutes.parquet` | 1-min index / stock history since 2022 | |
| `data/recordings/upstox_<day>_*.parquet` | per-minute snapshots, chains, metrics, candles, bars, news since 28 Oct 2026 | 12 Nov 09:07-10:02 missing; bars before 14:10 lost |
| `data/live.db` | alerts, outcomes, paper trades, plan outcomes, mind reads | read with `?mode=ro` |

## Rules
- Point-in-time: a value is stored with the time it became known; nothing uses later data.
- Facts that change are dated; old dates keep old values.
- Derived data (bars, features) can always be rebuilt from the tape / history; never edit raw data.
- Retention: logs `log_keep_days`; tape by free disk (`min_free_disk_gb`); everything else kept.
