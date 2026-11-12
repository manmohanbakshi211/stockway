# ADR-0005: Evidence before trust (promotion protocol)

- Status: Accepted
- Date: 2026-11-13

## Context
5-year research: a random index option buy loses 1.9-2.8% a trade after costs; stock options 2-19%. Many ideas look
good on the data they were tuned on and fail later. Thousands of ideas will be tried ("Dr. Strange" search), so luck
must be controlled for.

## Decision
A setup reaches the paper account only after, in order:
1. **Costs included** (spread, slippage, charges, theta) at the instrument's real cost drag.
2. **Walk-forward** on history, then a **holdout** period never used for any choice.
3. **Multiple-testing control**: results judged against how many variants were tried (deflated Sharpe / false
   discovery rate), plus a minimum number of trades.
4. **Shadow live** for a set number of days (graded, not traded).
5. **Champion vs challenger**: replaces the current setup only if better on the same unseen data; the margin is stated
   in the CHANGELOG.
Automatic retirement when live results fall below the holdout band. Failed ideas stay recorded with why they failed.

## Consequences
- The evaluator is a first-class component (T3) with its own tests on synthetic data with known answers.
- Daily recordings add depth (OI, bid / ask, chains), but a rule is trusted only after enough days and trades.
