# Runbook

How to run, check and fix Tanu. Until cutover the legacy app is what runs on market days (start it with
"Start Radar.command"); "Start Tanu.command" then only opens Tanu's app (view-only, port 8790).

## Switch-over (ADR-0009, only on Mandeep's go)
1. Any time (the old app may run): double-click **Start Tanu.command** -> System page -> save the Upstox API key and
   secret (Upstox developer page; the app's redirect URL stays `http://127.0.0.1:8765/upstox/callback`) and the
   Telegram bot token + chat id (a test message arrives).
2. Optional proof on this Mac: `python -m tanu rehearse 2026-11-06` -> `"passed": true`.
3. After 15:30: close the old app's window. Run `python -m tanu cutover` (refuses while port 8765 answers).
4. Close and double-click Start Tanu again. App: http://127.0.0.1:8765. Next morning before 09:00: System page ->
   **Log in with Upstox**. Tanu starts the feed by itself at 08:50 once logged in.
5. Rollback any time: close Tanu, `python -m tanu cutover --undo`, double-click Start Radar.

## Daily (after cutover)
| When | Do | Check |
|---|---|---|
| Before 09:00 | Double-click Start Tanu (or leave it running); System page -> Log in with Upstox (Telegram reminds) | Today: Live block, feed On |
| 09:15-15:30 | Nothing; paper trades and alerts run by themselves | Today page; Telegram for cards and warnings |
| After 15:35 | Nothing; bars, gap fill, costs and the evening review run by themselves; summary on Telegram | Journal page; review note |

## Commands
```bash
python -m tanu doctor            # machine check (exit code 1 on any FAIL)
python -m tanu version
python -m pytest tanu/tests -q   # Tanu's tests
python -m tanu import-legacy     # copy the legacy app's data into data/tanu (legacy files only read; re-run safe)
python -m tanu tape-summary 2026-11-13   # what a day's tape holds (after cutover)
python -m tanu bars 2026-11-13           # build that day's 1-minute bars from its tape
python -m tanu costs 2026-11-12          # that day's measured option costs and tradeable list
python -m tanu research                  # replay all setups over history (fills the journal), learn / holdout report
python -m tanu search                    # setups x exits x filters, ledger-controlled
python -m tanu review 2026-11-13         # evening review of the paper accounts
python -m tanu web                       # the app on this machine only: http://127.0.0.1:8790
python -m tanu run                       # what Start Tanu runs: app + (after cutover) the live market day
python -m tanu rehearse 2026-11-06       # a past day through the whole live path vs replay ("passed": true)
python -m tanu cutover                   # live switch ON (Mandeep's go; the old app must be closed)
python -m tanu cutover --undo            # live switch OFF (rollback)
```

## Where to look
| What | Where |
|---|---|
| Logs | `data/tanu/logs/tanu-YYYY-MM-DD.jsonl` (one JSON per line; see [OBSERVABILITY](OBSERVABILITY.md)) |
| Settings | `tanu/settings.json` (defaults), `data/tanu/local.json` (this machine) |
| Data | `data/tanu/` (see [DATA](DATA.md)) |

## Incidents
| Symptom | Likely cause | Action |
|---|---|---|
| `doctor` FAIL packages | new dependency not installed | restart via the start script (installs requirements) |
| `doctor` WARN disk | tape growing | free disk; the tape stops by itself before the disk fills |
| Feed not connected | Upstox login expired / internet | log in again on the System page; check internet |
| Prices behind (warning) | network or broker delay | System page shows delay; backup feed (when on) takes over |
| A setup marked failed | bullet error | log line `event=bullet_failed` names it; the rest keeps running |
| `BlockedCall` in logs | code tried a trading / account URL | stop and report: this is a bug (ADR-0001) |
| App says "view-only" after cutover | a gate failed: old app still open on 8765, another Tanu live, switch off | read the reason on the System page / window; close the other app; start Tanu again |
| Telegram "log in with Upstox" | no valid token (they end 03:30) | System page -> Log in with Upstox |
| Telegram "no prices for N s" | feed down / internet | Today: feed Off? check internet; Tanu reconnects by itself; restarted with a new login if Upstox rejected it |
| Started late (after 09:15) | - | normal: the morning comes from Upstox 1-minute candles (`backfilled_morning` in logs) |

## Never
Do not edit token files, do not paste tokens anywhere, do not place orders. See [SECURITY](SECURITY.md).
