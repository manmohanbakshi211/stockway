# Observability

## Logs (built, `tanu/core/log.py`)
One JSON object per line in `data/tanu/logs/tanu-YYYY-MM-DD.jsonl`, kept `log_keep_days` days.

| Field | Meaning |
|---|---|
| `ts` | time, IST, milliseconds |
| `level` | DEBUG / INFO / WARNING / ERROR |
| `run` | 8-character id of this process start (group lines of one run) |
| `comp` | component: `feed`, `tape`, `store`, `costs`, `state`, `engine`, `paper`, `journal`, `review`, `web`, `doctor` |
| `event` | snake_case name of what happened (`connected`, `tape_rotated`, `bullet_failed`, `gap_filled`) |
| other | event fields (numbers, names); keys that look like secrets are written as `***` |

Use: `grep '"event": "bullet_failed"' data/tanu/logs/*.jsonl`, or load a day with pandas
(`pd.read_json(path, lines=True)`).

Rules: log events, not chatter (no line per tick); WARNING = needs a look today; ERROR = something did not happen.

## Health (planned, T1+)
Each component publishes a small health record (ok / warn / fail + numbers) that the System page shows and
`doctor` can read:
- feed: connected, messages / min, delay to the broker, reconnects today
- tape: bytes today, files, disk free
- store: missing minutes today per symbol
- engine: pass time p50 / p95, slowest part
- paper: open trades, last fill time
Thresholds are settings; breaches log a WARNING and send one Telegram message (not one per minute).

## Daily gap report (planned, T1)
After the close: minutes missing per symbol, feed down time, slow passes, instruments without updates.
