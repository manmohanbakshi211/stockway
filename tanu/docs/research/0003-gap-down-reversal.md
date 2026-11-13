# Research 0003: gap-down reversal, Mandeep's 13 Nov chart idea (13 Nov 2026)

**Question (Mandeep, 13 Nov 2026, 15:59):** "9:20 par nifty mein call up side or bank nifty mein bhi chances dikh
rhe hain". On 13 Nov both indices gapped down, the first 5-minute candle was a big red one, and from 09:20 both
climbed (Nifty 25,116 -> 25,245 by 10:49; Bank Nifty 60,949 -> 61,237 by 10:45). Is "call after a gap-down" a
repeatable trade, or one day seen afterwards?

**Gun vs bullet split.** Gun (engine, once): morning features `gap`, `first5`, `first5_body`, `vs_first5`, and
`long` (so a setup can be calls only). Bullets: the setups below, in shadow.

**Pre-test (index only, no option prices, no costs).** 2022-01 to 2026-11-11 one-minute index history, ~1,197 days.
"Today-like" = down 0.6% or more from the previous close by 09:19 and a red first 5-minute candle of 0.3% or more.
Win = the index rises 0.4% before it falls 0.3% (until 15:15).

| Idea | Nifty | Bank Nifty |
|---|---|---|
| Any day, call at 09:20 (base rate) | 32% win / 52% loss (1,197 days) | 40% / 52% |
| Today-like, call at 09:20 | 31 days: 35% win / 65% loss; up by 10:50 on 32% | 36 days: 50% / 47%; up by 10:50 on 42% |
| ... plus 09:20 candle closes green, call 09:25 | 3 of 16 | 8 of 15 |
| ... plus new low then close in the candle's top 40%, call 09:25 | 0 of 5 | 2 of 8 |
| ... plus back above the first candle's close by 09:30 | 5 of 14 | 11 of 16 |

Nifty's last 12 today-like days: 4 wins, 8 losses (28 Oct, 28 Sep and 3 Jun 2026 looked the same at 09:20 and kept
falling). Selling into it (puts at 09:20, as the legacy app did today) is not an edge either.

**Reading.** The plain 09:20 call is a coin flip at best (Nifty worse). Only Bank Nifty's "back above the first
candle's close" looks interesting, on 16 days, after 3 versions were looked at on 2 indices: it can be luck.

**What was added (0.10.0, shadow, calls only, Nifty and Bank Nifty):**
- `gap_down_call_920`: the plain idea (signal at the 09:19 close, fill 09:20). Expected to fail; kept so the answer
  is on record with real option prices.
- `gap_down_reversal`: back above the first candle's close (crossing) between 09:20 and 10:00.
- `tanu/setups/pretests.json`: the 4 versions looked at count as ideas tried in the search's pass bar (ADR-0005).

On a synthetic option model the engine fires them on 30 / 26 Nifty and 36 / 26 Bank Nifty days of history (the
day lists match the pre-test); the 6,498 trades of the existing setups are unchanged.

**Next.** On the Mac (real 1-minute option prices, strict fills, costs, holdout from 10 Jun 2026):
`python -m tanu research` and `python -m tanu search`. A pass needs t >= the ledger bar and beating the random
baseline with the same exit; until then both stay in shadow (graded, no phone alerts).
