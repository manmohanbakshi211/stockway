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

## Live events (0.9.0)
| Component | Events |
|---|---|
| supervisor | `web_started`, `view_only` (with the gate reasons), `supervisor_state` (idle / login / live / eod), `cutover_switch`, `backfilled_morning`, `feed_restarted_with_new_login`, `feed_restarted_after_pause`, `instruments_download_failed`, `eod_done`, `eod_bars_failed` |
| session | `session_ready` (indices, traded, stocks, options, setups, notes), `session_started` (subscribed per mode), `feed_silent` (WARNING), `session_stopped` |
| live | `subscriptions_changed`, `live_day_done` (ticks, messages, minutes, late ticks, bars) |
| universe | `universe_trimmed` (WARNING: keys dropped to stay inside Upstox limits) |
| upstox_auth | `upstox_app_saved`, `upstox_logged_in` (expiry only, never the token) |
| telegram / alerts | `telegram_saved`, `telegram_failed`, `telegram_queue_full`, `alert_failed` |
| rehearsal | `rehearsal_done` (day, passed, trades) |
The live status for the app is `data/tanu/live/status.json` (rewritten every minute).
