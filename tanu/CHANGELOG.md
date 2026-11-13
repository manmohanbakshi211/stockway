# Changelog

All notable changes to Tanu. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions:
[Semantic Versioning](https://semver.org/) (0.x while the core is being built).

## [0.10.0] - 2026-11-13
### Added
- Mandeep's 13 Nov chart idea ("9:20 par call"), research 0003:
  - Gun: morning features in the engine: `gap`, `first5` (first 5-minute candle close vs previous close),
    `first5_body`, `vs_first5`, and `long` (d_long > 0 = calls only). Known only from 09:19.
  - Bullets (shadow, calls only, Nifty and Bank Nifty): `gap_down_call_920` (the plain 09:20 call, expected to
    fail, on record) and `gap_down_reversal` (back above the first candle's close, 09:20-10:00).
  - `setups/pretests.json`: versions looked at on index data count as ideas tried in the search's pass bar;
    the Setups page counts them too.
- Index pre-test 2022-2026: plain 09:20 call after a gap-down wins 35% (Nifty) / 50% (Bank Nifty) of such days;
  only Bank Nifty's "back above the first candle's close" looked interesting (11 of 16). Not proven.
### Checked
- Golden: all 6,498 trades of the existing setups identical before and after (full history, synthetic options).
- Tests: 84.

## [0.9.0] - 2026-11-13
### Added
- Cutover pieces (ADR-0009), all off until the live switch (Mandeep's go):
  - `tanu/live/`: universe planner (bullet `universe.json`: 5 indices, all F&O stocks, nearest-expiry options around
    the money, inside Upstox's per-connection limits, re-centred after a 2-strike move); streaming minute bars (same
    as the tape rebuild); live option book with OptionHistory's interface; the live loop (one engine step per closed
    minute, exchange-time bars, 2 s grace, carry-forward like replay, chain snapshots for measured costs, status file);
    day context and carried ATR exactly as replay; morning backfill from broker candles for a late start; session;
    supervisor `python -m tanu run` (gates, daily cycle, feed restart on a new login, end-of-day jobs).
  - Upstox token keeper (`brokers/upstox/auth.py`): app key / secret typed into the System page, daily login through
    Upstox's page, token until 03:30; secrets in `data/tanu/secrets/` (0700 / 0600), never shown back.
  - Telegram alerts (`notify/`): entry / exit cards for trial / live setups only; system warnings; day summary.
  - Rehearsal `python -m tanu rehearse <day>`: a past day through fake broker websocket -> feed process -> tape ->
    bars -> engine -> accounts, checked against replay. Real Mac data: 23 Oct, 27 Oct (expiry), 6 Nov: same trades,
    tape rebuilds the index bars, 0 late ticks.
  - `python -m tanu cutover [--undo]`; settings `live` (default false) and `legacy_port` (8765).
  - App: Today shows the live day (minute, feed, index prices, open paper trades, signals with status); System has
    the switch-over setup (Upstox key, Telegram, switch, daily login). Setup routes refuse other web pages.
  - "Start Tanu.command": its own environment (.venv-tanu, `tanu/requirements.txt`), never the old app's .venv.
- Engine listeners may implement only the events they need; new `on_signal` event (alerts at the signal, before the
  fill). Trades unchanged (golden tests).
- Instrument list: BSE file too (SENSEX options).
- Tests: 80 (live bars = tape rebuild, universe limits, live loop = replay, late start, full rehearsal over a
  websocket, token keeper, alerts, Telegram, setup route guards, gates and switch).

## [0.8.0] - 2026-11-13
### Added
- T5 light app (`tanu/ui/`, `python -m tanu web`, http://127.0.0.1:8790; the legacy app keeps 8765): four screens.
  - Today: one-line answer, paper accounts, setups by status, cheapest options to buy, questions from the reviews.
  - Setups: each setup's evidence (tuning days vs unseen days, best of N versions in plain words, why it has not
    passed), ideas tried so far.
  - Journal: trades by day and account in a ruled ledger; tap a trade for its footprint and charts of the
    underlying that day and the option around the trade.
  - System: machine checks, data sizes, import totals, latest log lines.
  Read-only API, binds to this machine only, no outside links (works offline), light / dark, bottom tabs on phones.
- Setting `web_port` (default 8790, never the legacy 8765).
- Headless check (Playwright, real Mac data copy): 4 screens at 390 px and 1280 px, no console errors, no sideways
  scroll. Tests: 70.
### Found
- SQLite cannot run on the VM's shared folder mount (disk I/O error); on the Mac itself it is fine. Rule added to
  DATA: the journal stays on a local disk.

## [0.7.0] - 2026-11-13
### Added
- T4 paper side:
  - Paper accounts (`tanu/paper/accounts.py`, `accounts.json` bullet): listeners on the engine; each decides at the
    fill whether it takes the trade. `shadow` takes every signal (grading); `sniper` takes only setups on trial / live,
    index only, 1 lot, ₹1L, max 1 open, 3 trades a day, stop at -3% of capital or after 2 losses in a row, premium
    <= 30% of capital, one position per direction in the index group, no entry after 14:30. Every refusal is journaled
    with its reason. Paper only (ADR-0001).
  - Journal (`tanu/journal/store.py`, SQLite `data/tanu/store/journal.db`): every trade with its footprint (state at
    entry, best / worst option price, minutes, costs, exit reason, underlying in / out); idempotent on replays.
  - Evening review (`tanu/journal/review.py`, `python -m tanu review <day>`): per account P&L, by setup / day type /
    exit, best and worst trade, "went green then closed red" and "never moved" losers, refusals; questions to
    `research/hypotheses.jsonl` (never straight to live). Deterministic; an AI write-up can sit on top later.
  - Engine hooks `on_fill` / `on_close` for listeners; a failing listener is recorded on the position, never stops
    the engine; best and worst option price tracked per trade. `tanu research` now fills the journal.
- Tests: 67.

## [0.6.1] - 2026-11-13
### Found
- Research 0002 (`docs/research/0002-exit-search.md`), run on the Mac: 56 combinations (4 entries x 7 exits x 2
  filters), 36,736 trades on 523 days of real option prices; k = 42, bar t >= 3.24. Nothing passes. Best: VWAP
  pullback + wide chandelier +5.36% learn (t 1.05), -0.17% holdout. Exit type barely matters (-1.3% to -2.2% learn
  per type). Skipping expiry day helped in 36 of 56 comparisons (median +0.5 points): kept as a shadow filter.
  Next edge sources: option-side flow, order flow, in-play stocks, breadth (from the tick tape).

## [0.6.0] - 2026-11-13
### Added
- Exit variants (`tanu/setups/registry.py` Exit, engine `_exit_reason`): besides the chandelier stop on the
  underlying, a premium trail (armed after a gain, exits a set % below the option's best), a time stop, a "no move"
  exit (out when the option has not gained X% within N minutes); premium-stop fills now respect a gap through the
  level. New feature `dte` (days to the contract's expiry) for filters.
- `tanu/setups/exits.json` (bullet): 7 exit variants x 2 filters (all days, no expiry day).
- Controlled search (`tanu/evaluate/search.py`, `python -m tanu search`): every entry x exit x filter replayed once,
  each judged against the yardstick with the same exit and filter; every idea ever tried goes to
  `data/tanu/research/ledger.jsonl` and the pass bar uses the ledger's total, so more tries make passing harder.
- Tests: 62 (exit rules and fill levels, variants file, same-exit yardstick, search + ledger raising the bar).
### Pending
- The real-data run needs the Mac (the legacy option files live there): 4 entries x 7 exits x 2 filters = 56
  combinations, k = 42 ideas, pass bar t >= 3.2.

## [0.5.0] - 2026-11-13
### Added
- T3 core:
  - Day state (`tanu/state/day.py`): normal day range and the share reached by each minute, from earlier days only;
    quiet / range / trend, with / against.
  - Setup bullets (`tanu/setups/registry.py`, `library/*.json`): validated one by one (a bad file is skipped and
    reported); conditions with direction-adjusted and previous-minute features; window; chandelier exit; status
    shadow by default. First bullets: sharp_move_trend, vwap_pullback_trend, orb15_break, random_baseline.
  - Engine (`tanu/engine/core.py`): one code path for replay and live; incremental features; signal at a minute's
    close, fill at the next minute's option open with half the spread + 1 tick; chandelier / premium stop / flat-by
    exits; charges; deterministic.
  - Historical option prices from the legacy expired-option files, read in place (`tanu/engine/options.py`); replay
    driver (`tanu/engine/replay.py`).
  - Evaluator (`tanu/evaluate/report.py`): learn / holdout, Bonferroni bar, must beat the baseline, reasons kept;
    `python -m tanu research`.
- Tests: 58 (registry isolation, point-in-time state, engine fills / exits / determinism, option history, evaluator
  known answers).
### Found
- Research 0001 (`docs/research/0001-first-engine-replay.md`): on 523 days of real 1-minute option prices none of the
  three candidates passes (sharp move -3.1% learn / +9.9% holdout; VWAP pullback +4.5% (t 0.94) / -1.0%; ORB -4.1% /
  +1.9%; baseline -3.3% / -0.3%). All stay in shadow. 93% of exits were the chandelier stop: exits are the next lever.

## [0.4.0] - 2026-11-13
### Added
- T2 costs (`tanu/costs/`, ADR-0008): round trip of an ATM option buy = measured spread + 1 slippage tick + charges
  (dated rates) + theta for the holding time (square-root rule); daily tradeable list with reasons (round trip > 6%,
  spread > 2%, too few quotes, unknown lot). On trial: shown, not enforced. Thresholds in `tanu/costs/rules.json`.
- Instrument list (`tanu/data/instruments.py`): Upstox's public NSE file -> lot, tick and expiries per underlying.
- CLI: `python -m tanu costs <day>`.
- Tests: 52.
### Found (Mac data, 7 days 4-12 Nov, 224 underlyings)
- Measured round trip (60 min hold): Bank Nifty 1.7%, Midcap Select 1.9%, BSE / MCX / HAL / Apollo / ICICI / TCS /
  M&M / Trent ~2.0-2.2%, Nifty 3.2% (weekly theta; expiry day 19%), SBIN 2.3%, Reliance 2.4%, Tata Steel 3.5%, ITC 3.8%.
  168 of 224 tradeable on at least half the days; illiquid ones (Nifty Next 50, Kaynes, Idea, Yes Bank) excluded.
- The legacy Lab overstated stock option costs (assumed ₹0.25 / 1% slippage per side): e.g. Tata Steel -15.3% there vs
  3.5% measured. Rank agreement 0.60. Earlier advice that stock option buying costs 2-19% a trade is withdrawn.

## [0.3.0] - 2026-11-13
### Added
- T1 data layer, part 2:
  - Feed process (`tanu/data/feed.py`): Upstox V3 websocket in its own process; every message to the tape byte for
    byte (plus connect / disconnect / subscribe events); decoded ticks to the app; reconnect with backoff and
    resubscribe; restarts a dead process; URL checked by the safety guard. Tested on a local fake broker only (no
    broker connection before cutover, ADR-0007).
  - Upstox adapter (`tanu/brokers/upstox/`): the V3 message format vendored in a private protobuf pool (cannot clash
    with the legacy copy), a decoder for every feed shape, a market-data REST client (rate limits, retries, login
    errors, guard).
  - 1-minute bars from the tape (`tanu/data/bars.py`): exchange trade time, per-minute volume from the cumulative
    day volume, last OI / bid / ask, session minutes only; a rebuild gives identical bars.
  - Gap check and fill (`tanu/data/gaps.py`): missing session minutes per instrument, filled from the broker's 1-minute
    candles and marked `src="broker"`.
  - Dated facts (`tanu/facts/`, `tanu/data/facts.py`): charges, index lot sizes, sessions / holidays / Muhurat, copied
    from the legacy rules; option charges per trade.
  - Legacy import (`tanu/data/legacy.py`, `python -m tanu import-legacy`): history (1-minute index, daily stocks),
    recordings (bars, minute snapshots, chains), live.db (alerts, outcomes, paper, plan outcomes, trials, mind reads);
    Lab tables and 1.8 GB of expired-option / end-of-day files listed and read in place. Legacy files only read.
  - CLI: `import-legacy`, `tape-summary <day>`, `bars <day>`.
- Run on the Mac (13 Nov, 04:0x IST, Tanu code run from a scratch copy, legacy app untouched): 908,876 history rows,
  11,971,932 recording rows from 29 files, 151,902 live.db rows, Lab tables 3,807,977 rows listed; 16 s, 160 MB.
- Tests: 46 (fake-broker feed with a dropped connection, decoder shapes, REST, bars, gaps, facts, import).

## [0.2.0] - 2026-11-13
### Added
- T1 step 1, tick tape (`tanu/data/tape.py`, ADR-0006): append-only writer on its own thread (callers never wait for
  the disk), one file per IST hour, restarts open a new file (never overwrite), zstd per block so a crash loses at
  most the last second, pauses instead of filling the disk; reader, day reader and day summary (silences, control
  events). Not connected to any broker yet (ADR-0007).
- Tests: exact round trip of 5,000 binary messages, hour files and restarts, crash recovery, 100,000 writes in under
  2 s, low-disk pause, day summary.

## [0.1.0] - 2026-11-13
### Added
- T0 foundation: `tanu/` package beside the legacy app, with no imports either way.
- Settings: one typed object (`tanu/core/config.py`); load order defaults -> `tanu/settings.json` ->
  `data/tanu/local.json` -> `TANU_*` environment; unknown keys rejected.
- Structured JSON-line logs with run id, component and event; secrets masked (`tanu/core/log.py`).
- Market-data-only guard: trading and account URLs raise `BlockedCall` (`tanu/core/safety.py`).
- `python -m tanu doctor` machine check; `python -m tanu version`.
- Tests: boundaries (no Manu <-> Tanu imports), safety, settings, logs, doctor, command line.
- Docs: architecture, ADRs 0001-0007, roadmap, runbook, observability, data, testing, contributing, security, UX,
  glossary, rules for Claude.

### Decided
- Tanu replaces the legacy app; cutover only after Tanu runs a full market day cleanly (ADR-0002).
- No tick recorder attached to the legacy feed: Tanu's tape starts with Tanu's own feed (ADR-0007).
