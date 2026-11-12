# Rules for anyone (or any AI) changing Tanu

Tanu replaces the legacy app ("Manu": live/, lab/, webapp/, radar/) from 13 Nov 2026 (Mandeep: "aaj se samajh lo manu
khatam tanu shuru"). All new work happens here. These rules override the legacy CLAUDE.md for anything under tanu/.

## Never
- **No real orders.** No order, modify or cancel call to Upstox, Dhan, Kite or any broker; no funds, positions,
  holdings, margins or portfolio; no algo trading. Every network URL goes through `tanu.core.safety.check_url`.
- **No secrets in code, logs, git or chat.** Tokens, PINs, TOTP keys stay in their token files on the Mac. Never read,
  copy or stage: data/upstox_token.json, data/dhan_*, data/kite_token.json, user_settings.json.
- **No overlap with the legacy app.** Tanu never imports live/, lab/, webapp/, radar/; the legacy app never imports
  tanu. Tanu writes only under data/tanu/. Legacy data is read-only and copied in by the import step.
  Until cutover, Tanu opens **no broker connection** (one live feed and one token keeper at a time).

## How we build
- **Gun vs bullet.** The engine (gun) knows nothing about any one strategy, date or lot size. Strategies, thresholds,
  calendars, costs and universes are bullets: data files with a `valid_from` date, loaded in isolation (a broken
  bullet is skipped and logged, never stops the gun). Adding a bullet needs no engine edit.
- **Every new input gets the split first** (book, video, idea, screenshot): say what is gun, what is bullet, then build.
- **Evidence before trust.** A setup or model goes live in paper only after: costs included, walk-forward, a holdout it
  never saw, correction for the number of ideas tried, then shadow. Failed ideas stay recorded as failed.
- **Every engine must earn its place**: it must improve results on unseen data (measured), or it is not built / removed.
- **Defaults change only on Mandeep's say-so**, with a CHANGELOG line.

## Definition of done (every change)
1. Code + tests (unit; replay / golden test when behaviour on market data changes).
2. `python -m pytest tanu/tests` green, and the legacy suite `python -m pytest tests` still green until cutover.
3. Docs updated: CHANGELOG always; ARCHITECTURE / DATA / RUNBOOK / an ADR when the design, data or operations change.
4. Structured logs for new components (`tanu.core.log.get("<component>")`, snake_case events, no secrets).
5. UI pages opened in a headless browser: no console errors, no sideways scroll at phone width.
6. Release: branch `tanu-x.y.z` -> tests -> squash-merge to main -> push. Commit message says what and why.

## Talking to Mandeep
Hinglish, short, action-first bullets. Results in ₹ and % per trade after costs, on unseen days. Never suggest rest.
