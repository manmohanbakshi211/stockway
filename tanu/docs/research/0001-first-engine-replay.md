# Research 0001: first replay through Tanu's engine (13 Nov 2026)

**Question:** do the three candidates from the 5-year Lab research (claude/chart_research.md) survive Tanu's engine?

**How:** `python -m tanu research` (tanu 0.5.0). Nifty + Bank Nifty, every day with 1-minute option prices
(25 Sep 2024 - 9 Nov 2026, 523 days). Real 1-minute option bars (legacy expired-option files, read in place), nearest
expiry, ATM at the signal minute. Signal at the close of a minute, fill at the next minute's option open + half the
measured spread (Nifty 0.27%, Bank Nifty 0.33%) + 1 tick; exits at the bid side; charges at the day's rates; theta
inside the prices. Exit: chandelier on the underlying (1.5 x 5-min ATR, trail 2.5 x ATR, checked on 1-minute
highs / lows), premium stop -40%, flat by 15:15. Learn = first 80% of days, holdout from 10 Jun 2026. Pass bar:
learn t >= 2.39 (Bonferroni over 3 setups), holdout average > 0, beats the baseline in both.

| Setup | Learn n / avg / t | Holdout n / avg | ₹ / trade | Verdict |
|---|---|---|---|---|
| sharp_move_trend | 350 / -3.07% / -1.63 | 74 / +9.91% | -253 | fail: learn below 0 |
| vwap_pullback_trend | 181 / +4.51% / 0.94 | 50 / -0.96% | -58 | fail: luck not ruled out; holdout below 0 |
| orb15_break | 1062 / -4.07% / -4.71 | 249 / +1.92% | -323 | fail |
| random_baseline | 676 / -3.28% / -2.71 | 166 / -0.32% | -349 | yardstick |

**Findings**
- None passes. All stay in shadow. The 5-year Lab numbers did not hold under stricter fills (next-minute open, spread,
  1-minute stop checks) and a different, shorter period.
- Results swing by quarter (sharp move: -9.7% in 2025 Q4, +8.7% in 2026 Q3): regime-dependent, not a stable edge yet.
- 93% of exits are the chandelier stop: on 1-minute data a 1.5 x ATR structure stop is hit by normal noise. Exit design
  is the next lever to test (wider / time-based / premium-based exits as bullets), with the multiple-testing bar.
- Nifty did better than Bank Nifty on both candidates (sharp move +1.1% vs -2.9%; VWAP pullback +9.0% vs -1.8%).

**Next:** exit variants and situation filters as bullets, run through the same engine and bar; nothing is promoted
without passing ADR-0005.
