# Security

## Threats we design against
| Threat | Control |
|---|---|
| A bug or a bad bullet places a real order | No order code exists (ADR-0001); `safety.check_url` blocks trading / account URLs; tests scan the source |
| Tokens leak (git, logs, chat, screenshots) | token files are gitignored and read only by the feed component; log masking; never pasted in chat |
| Personal / account data read | no account endpoints (funds, positions, holdings, portfolio) are ever called |
| Malicious downloaded file runs code | downloads (videos, books, data) are data only: parsed, never executed; kept in their own folder |
| A third-party package misbehaves | dependencies pinned in requirements.txt; new ones need an ADR |
| Data loss | tape is append-only; raw data never edited; derived data rebuildable |

## Secrets
- Never in code, settings files, logs, git, issues or chat: Upstox / Kite / Dhan tokens, API secrets, PINs, TOTP keys,
  Telegram bot token.
- Files (gitignored, never read or copied by Claude): `data/upstox_token.json`, `data/kite_token.json`, `data/dhan_*`,
  `user_settings.json`.
- Kite / Dhan keys are entered by Mandeep on the Mac only.

## Reporting
Anything that looks like an order call, a token in a log, or an unexpected network destination: stop, log it as
ERROR, tell Mandeep.
