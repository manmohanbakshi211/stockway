# ADR-0013: Tanu's AI is read-only: Ask Tanu and the daily notes, from Tanu's own data

- Status: Accepted
- Date: 2026-11-13
- Decided by: Mandeep ("ai wala system toh chahiye hi hai")

## Context
The legacy app had "Ask Stockway", AI notes and an AI router (fast / balanced / deep models). Tanu is built on
evidence: an AI answer is information, not a trade signal, and it must never become a way to reach a broker, a file
or a secret.

## Decision
1. **Two ways in, chosen on the System page:** Claude Code on the Mac (Mandeep's Claude plan login) or a Claude API key
   (saved as the secret `ai`, never shown back). Default: Claude Code if it is installed, else off.
2. **Read-only by construction.** Claude Code runs as plain question-and-answer: every tool off (`--tools ""`, older
   versions `--disallowedTools ...`), no session saved, no MCP servers, no slash commands, in an empty folder
   (`data/tanu/ai/sandbox`), prompt on stdin, any API key removed from its environment. The API path sends only the
   prompt to `api.anthropic.com/v1/messages` (through the URL guard).
3. **What the AI sees:** a compact snapshot built from Tanu's data folder only (live status, paper accounts, research
   verdicts, market sources, alerts, the symbols asked about). The secrets folder is never opened; keys named like
   secrets are dropped as a second guard (tested). The rules tell it to answer only from that data, say when data is
   missing, never suggest real-money trades, and phrase ideas as tests.
4. **Jobs and models (bullet `ai/models.json`):** ask = balanced, morning note = balanced, evening note = deep;
   fallback opus -> sonnet -> haiku when the plan refuses a model (remembered for the day); daily budgets for automatic
   jobs, never for a question Mandeep asks.
5. **Notes:** after the switch-over the supervisor writes the morning note at 08:45 and the evening note at 16:05 on
   trading days and sends them to Telegram; before it, the Today page has "Write ... now" buttons. Saved in
   `data/tanu/ai/notes/`.

## Consequences
- An idea from the AI goes to research like any other (ADR-0005); it never changes a setup, an account or an alert.
- Claude Code and the legacy app share the same plan allowance on the Mac; budgets keep automatic use small.
- Reading headlines with the AI (the legacy "news radar", graded against keyword tone) is later work.
