# ADR-0014: The Dhan 5-year download moves into Tanu, after the switch-over and outside market hours

- Status: Accepted
- Date: 2026-11-15
- Decided by: Mandeep ("add that in tanu", 14 Nov 2026, 23:51)

## Context
The legacy app downloads Dhan's expired-options archive (1-minute, 2021 onwards) for Nifty, Bank Nifty and every
F&O stock (lab/dhan_hist.py, data/lab5y). On 14 Nov it was a quarter done: the indices complete, 38 of 218 stocks
complete, 32 partly, 148 not started; days of Dhan calls remain. At the switch-over the legacy app closes, so the
download would stop for good. Five years of real option prices are what Tanu needs to test stock options and the
Bank Nifty iron condor with its old weekly expiries.

## Decision
1. **Gun** (`tanu/brokers/dhan/`, `tanu/data/dhan5y.py`):
   - Token keeper with Dhan's documented automatic login (client ID + PIN + current TOTP code to
     auth.dhan.co/app/generateAccessToken). The PIN and TOTP key live in the macOS Keychain (`keyring`; elsewhere
     Tanu's 0600 secrets folder); never logged, shown or sent anywhere but auth.dhan.co. A refusal stops automatic
     login until they are saved again (no retry loop that could lock the account); at most one login in 5 minutes;
     a new token when the old one is 20 hours old or refused. A pasted web.dhan.co token also works.
   - A data-only client (rolling expired options, daily candles) through `safety.check_url`, with one speed limit
     (4.6 calls a second) and a daily count in `data/tanu/dhan/calls.json`.
   - The downloader, ported from the legacy one: trading days from the calendar caches or Dhan's Nifty candles, a
     manifest per symbol-month, a day saved only when every strike came back, pauses on network trouble and retries
     the same day, the daily cap, the disk guard.
2. **Continues the legacy progress, read-only.** A day the legacy manifest or month file holds (or that Dhan finally
   had no data for) is never fetched again. The legacy folder is never written; Tanu's days go to
   `data/tanu/dhan5y/history/<SYM>/raw_YYYY-MM.parquet` (same columns) with Tanu's own manifest.
3. **Bullet** `tanu/data/dhan5y.json`: start date, order (indices, the 30 most liquid, the rest by turnover), strikes
   (indices ATM±10, first 80 stocks ATM±3, the rest ATM±1), daily cap 92,000, speed, disk guard, run times.
4. **When:** only in live mode (after the switch-over, when the legacy app and its own Dhan login are closed: one
   token keeper at a time), and never in market hours: on trading days after 16:15 or before 08:30 IST, all day on
   weekends and NSE holidays. The System page can pause it.

## Consequences
- Nothing already downloaded is fetched twice; the two folders together are the 5-year archive. Reading it in
  Tanu's research (alongside the Upstox per-expiry files) is the next step.
- A rollback to the legacy app would re-fetch Tanu's days there (it does not know them): wasted calls, no damage.
- Dhan's daily limit is shared with nothing else in Tanu today; the cap leaves 8,000 calls a day spare.
