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
| Another web page (or a DNS trick) uses Tanu's setup routes | routes answer only to Host 127.0.0.1 / localhost on Tanu's port, refuse a foreign Origin and need the X-Tanu header (a cross-site page cannot send it without a preflight Tanu never approves) |
| A forged Upstox callback | the login `state` is random per login and checked; the code is exchanged only for the app saved on this Mac |
| Two live feeds / two token keepers | gates: legacy port 8765 must be silent, one live lock, switch on (ADR-0009) |
| The AI reads files, runs commands or sees a secret | Claude Code runs with every tool off in an empty folder, no session, no MCP; the prompt is built from Tanu's data only and secret-like keys are dropped (tested); the API key is a secret, never shown (ADR-0013) |
| A public website sends something harmful | Market page sources are parsed as data only (no code runs), size-capped, every URL through the guard; news links are shown only if http(s) and open in a new tab without the opener; all text is escaped (ADR-0012) |

## Secrets
- Never in code, settings files, logs, git, issues or chat: Upstox / Kite / Dhan tokens, API secrets, PINs, TOTP keys,
  Telegram bot token.
- Files (gitignored, never read or copied by Claude): `data/upstox_token.json`, `data/kite_token.json`, `data/dhan_*`,
  `user_settings.json`.
- Kite / Dhan keys are entered by Mandeep on the Mac only.
- Tanu's own secrets (ADR-0009): typed by Mandeep into the System page on the Mac; saved in `data/tanu/secrets/`
  (folder 0700, files 0600: `upstox_app`, `upstox_token`, `telegram`, `ai`); the app only says "saved", never shows them;
  Tanu never reads the legacy app's token or settings files.

## Reporting
Anything that looks like an order call, a token in a log, or an unexpected network destination: stop, log it as
ERROR, tell Mandeep.
