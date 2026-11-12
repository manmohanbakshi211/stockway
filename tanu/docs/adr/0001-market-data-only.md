# ADR-0001: Market data only — no orders, no account access

- Status: Accepted
- Date: 2026-11-13
- Decided by: Mandeep

## Context
Mandeep: "algo trade means real trade nahi lena ... real money traders ya real trading account mein kuch nahi karna";
"ye humare personal data ke liye koi threat na ho". The app's purpose is entry / exit alerts and paper trades.

## Decision
- Tanu never calls broker endpoints for orders, modifications, cancellations, GTT, baskets, funds, margins, positions,
  holdings or portfolio. Broker APIs are used for market data only (quotes, candles, option chains, feeds, instruments).
- Every outgoing URL passes `tanu.core.safety.check_url`, which raises `BlockedCall` for trading / account paths.
- Tests block order calls in Tanu's source and check the guard against real broker paths.
- Tokens are read only by the feed / data component, never logged (log masking), never committed, never sent in chat.

## Consequences
- Paper trading must model fills honestly (bid / ask, delay, charges), since there is no real execution to learn from.
- Any future idea that needs account data is out of scope unless Mandeep writes a new ADR.
