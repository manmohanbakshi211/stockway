# ADR-0011: Multi-leg option structures (iron condor, straddle) as their own bullets, replayed on real option prices

- Status: Accepted
- Date: 2026-11-13
- Decided by: Mandeep ("haan kardo add", after the Manu vs Tanu comparison: the legacy app's only evidence was the
  Bank Nifty iron condor held to expiry, one session before expiry, and short straddles)

## Context
Every Tanu setup so far is a signal on the index that buys or sells one option (ADR-0010). The legacy Lab's one result
that held on unseen days was not a signal at all: a calendar trade, a Bank Nifty iron condor entered one session
before expiry and held to expiry (192 trades, about +₹95K a lot). Short straddles had weaker support. These trades
have 2-4 legs, are entered by the calendar (days to expiry, a time), and their risk is set by the wings, not by an
index stop. They do not fit the single-option engine.

## Decision
1. **Gun:** `tanu/structures/` is a small second engine next to the signal engine, knowing nothing about any one
   structure. `runner.py` plans entries from the expiry calendar in the option history, picks each leg's strike
   (nearest to spot x (1 + pct/100), or skips the day when the data has no strike close enough), fills every leg at
   the next minute's open with half the measured spread and a tick (ADR-0008), values the structure every minute at
   what it costs to close, and exits on stop (loss >= n x credit), take (kept >= x% of the credit), a time, or expiry
   (settled at intrinsic value from the index close, no spread). Charges per leg from the dated facts.
2. **Bullets:** one JSON per structure in `structures/library/` (`id`, `why`, `source`, `valid_from`, `status`,
   `symbols`, `entry` {dte, at}, 1-6 `legs` {action, side, pct}, `exit` {at, stop_x_credit, take_pct_credit}, `lots`).
   Loaded in isolation; a broken file is skipped and reported. Every-session entries must have a time exit.
   First five, all shadow: Bank Nifty iron condor 1 day before expiry (held / stop at 2x credit), Nifty iron condor,
   short ATM straddle 09:20-15:15 every day, short straddle on expiry day.
3. **Margin:** defined risk (every sold leg has a bought wing) = widest wing - credit; otherwise notional x the dated
   margin share (`facts/margins.json`). % per trade is on that margin.
4. **Evidence:** there is no random yardstick for a calendar trade, so the bar is "above zero beyond luck": learn 80% /
   holdout 20%, t above the Bonferroni bar for the number of structures tried (ADR-0005). The evaluator takes
   `baseline=None` for this.
5. **Paper:** a fifth account, `shadow_multileg` (₹5,00,000), and a journal table `structures` (one row per trade,
   legs as JSON). The app's Option selling tab and the Setups page show each structure's results and latest trades.
6. **Live:** replay only in this release. Live paper entries for structures come after the real-option result, as a
   listener on the live loop that keeps open structures across days.

## Consequences
- The legacy iron condor result can be checked in Tanu with Tanu's costs and Tanu's holdout before anything relies on it.
- Wing strikes 2% away may be missing from older legacy option files; those days are counted as skipped with the
  reason, not filled at a guessed price.
- Expiry settlement uses the index close at 15:29, not the exchange's official settlement price (close to it, not
  equal).
- Tanu still never places an order (ADR-0001).
