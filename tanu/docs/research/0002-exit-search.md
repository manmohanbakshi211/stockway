# Research 0002: exits and filters, controlled search (13 Nov 2026)

**Question:** research 0001 found 93% of exits were the tight chandelier stop. Do other exits or an expiry-day filter
turn any entry into an edge?

**How:** `tanu search` (0.6.0) on the Mac, chunked. 4 entries (sharp_move_trend, vwap_pullback_trend, orb15_break,
random_baseline) x 7 exits (chandelier tight / wide / wide + no-move, premium trail 20 / 30, time 30 / 60 min) x 2
filters (all days / no expiry day) = 56 combinations, 36,736 trades, 523 days of real 1-minute option prices (Nifty,
Bank Nifty, 25 Sep 2024 - 9 Nov 2026), same strict fills and costs as 0001. Holdout from 10 Jun 2026. Ideas tried
(ledger) k = 42 -> pass bar t >= 3.24; each combination must also beat the baseline with the same exit and filter.

**Result: nothing passes.**

| Best by learn average | Learn n / avg / t | Holdout n / avg |
|---|---|---|
| vwap_pullback_trend, chandelier wide, all days | 181 / +5.36% / 1.05 | 50 / -0.17% |
| vwap_pullback_trend, chandelier tight, all days | 181 / +4.51% / 0.94 | 50 / -0.96% |
| vwap_pullback_trend, wide + no-move, all days | 181 / +3.97% / 0.79 | 50 / -0.32% |
| sharp_move_trend, time 30, no expiry day | 300 / -0.69% / -0.61 | 68 / +1.70% |
| random_baseline, time 60, no expiry day | 591 / -0.51% / -0.55 | 145 / -1.27% |

**Findings**
- No combination has an edge that survives luck control; the best learn result (t 1.05) is well inside chance and
  its holdout is negative.
- Exit choice barely matters for these entries: average learn result per exit type -1.3% to -2.2%. Exits move losses
  around; they do not create an edge.
- Skipping expiry day helped in 36 of 56 learn / holdout comparisons (median +0.5 points a trade): a small, broad
  improvement, kept as a candidate filter (shadow), not proof.
- Conclusion for the design: price-only entries on index options, judged honestly, sit near break-even minus costs.
  An edge, if there is one, has to come from information these entries do not use: option-side flow (OI change,
  premium expansion, IV), order flow, "in play" stocks, breadth across symbols. That data is in the tick tape (T1)
  once Tanu records live, and in the legacy recordings for 7-12 days only.

**Ledger:** all 56 results are in `data/tanu/research/ledger.jsonl` on the Mac; the next search starts at k = 42.
