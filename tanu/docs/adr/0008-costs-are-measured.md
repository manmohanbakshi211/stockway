# ADR-0008: Option costs are measured from live quotes, not assumed

- Status: Accepted
- Date: 2026-11-13

## Context
The legacy Lab priced stock options with an assumed slippage of at least ₹0.25 or 1% of the price per side
(`lab/engine.py slip`). For low-priced options that alone is 10%+ a round trip: the Lab showed a random buy losing
15% on Tata Steel, ITC and ONGC. Tanu measured the real spread from the recorded option chains (7 days, 4-12 Nov 2026,
224 underlyings, ATM, nearest expiry): Tata Steel's full round trip (spread + 1 tick + charges + 60 minutes of theta)
is 3.5%, ITC 3.8%, SBIN 2.3%, Reliance 2.4%, Bank Nifty 1.7%, Nifty 3.2% (weekly, more theta). Rank agreement with
the legacy Lab: Spearman 0.60.

## Decision
- Round-trip cost = measured spread + slippage ticks (bullet) + charges at that day's rates + ATM theta for the
  holding time (square-root rule). Spreads come from live quotes (chains / tape), per instrument per day.
- The tradeable list uses these measured costs (tanu/costs/rules.json); it is on trial (shown, not enforced).
- Results from the legacy Lab for stock options are not trusted for cost; T3 recomputes option P&L with measured costs.

## Consequences
- Stock options are back in scope for buying where measured costs are low (168 of 224 underlyings passed on at least
  half of the 7 days). Earlier advice that stock option buying "costs 2-19% a trade" is withdrawn.
- Quotes at snapshot times can be tighter than during fast moves; the tape (ADR-0006) will measure spreads at the
  moment of each signal once Tanu records live.
