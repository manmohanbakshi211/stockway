# Runbook

How to run, check and fix Tanu. Until cutover the legacy app is what runs on market days (start it with
"Start Radar.command"); Tanu runs only its checks and tests.

## Daily (after cutover)
| When | Do | Check |
|---|---|---|
| Before 09:00 | Start the app; log in to Upstox when asked | System page: feed connected, disk green |
| 09:15-15:30 | Nothing; alerts and paper run by themselves | Today page; Telegram for warnings |
| After 15:35 | Nothing; gap check + fill and the evening review run by themselves | Journal page; review note |

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

## Never
Do not edit token files, do not paste tokens anywhere, do not place orders. See [SECURITY](SECURITY.md).
