# Research 0005: the "10 EMA" video, and every setup bought vs sold on real option prices (13 Nov 2026)

## The 10 EMA video
Sent by Mandeep (Anant Ladha with Pushkar Raj Thakur). Rules, in our words: on a chart (5-min shown) with a 10 EMA,
when price is above the EMA a red candle whose low does not touch the EMA is the trigger; the next candle breaking
its high is a buy; stop = the EMA; target 1:2 (trail on the EMA after that). Below the EMA, the mirror (green candle
not touching, break of its low = sell). Option selling: sell the out-of-the-money option at the EMA level, premium
stop 15-30%. Claims: 3 of 4-5 trades right; 1:7 to 1:10 "easily".

**Gun vs bullet.** Gun: the pullback trigger candle on any timeframe / EMA (`epull_<tf>m_<n>`, `tanu/engine/tf.py`),
the selling version of every setup (ADR-0010). Bullet: `ema10_trend_5m` (both directions, stop = EMA, 1:2, up to 5 a
day; selling version at the EMA strike, premium stop 30%, target 50%).

**Index pre-test (2022-2026, no costs).** Won 37-40% of trades (claim: about 60-75%); 1:2 reached 28-30%; average
+0.09R Nifty / +0.02-0.03R Bank Nifty; +2.2 to +3.3 index points a trade (+2.4 to +5.1 with the EMA trail).

## All setups on real option prices
`python -m tanu research-steps` on the Mac: 524 days of real 1-minute option prices (Nifty, Bank Nifty, 25 Sep 2024
- 9 Nov 2026), strict fills (next minute's open, half spread + 1 tick each way), charges; holdout from 10 Jun 2026.
18,807 trades. k = 16 ideas in this run (8 setups x buy / sell), pass bar t >= 2.96. % per trade: on premium paid
(buying) or received (selling).

| Setup | Buying: learn / unseen, ₹ a trade | Selling: learn / unseen, ₹ a trade |
|---|---|---|
| 10 EMA trend pullback | -2.5% / -3.6%, -₹229 (2,754 trades) | -1.4% / -2.2%, -₹98 (2,750) |
| 5 EMA puts (5-min) | -1.3% / +1.0%, -₹137 (2,555) | -1.0% / -0.4%, -₹124 (2,503) |
| 5 EMA calls (15-min) | -4.3% / -3.9%, -₹276 (1,270) | -4.2% / -1.4%, -₹181 (1,265) |
| Gap-down call at 09:20 | -8.6% / +1.3%, -₹873 (34) | -6.4% / +9.6%, -₹644 (34) |
| Gap-down reversal | -10.3% / +1.6%, -₹966 (29) | -8.7% / +4.9%, -₹745 (29) |
| ORB 15 | -4.1% / +1.9%, -₹323 (1,311) | -1.6% / 0.0%, -₹142 (1,296) |
| Sharp move, trend day | -3.1% / +9.9%, -₹253 (424) | -1.1% / +3.1%, -₹146 (418) |
| VWAP pullback, trend day | +4.5% / -1.0%, -₹58 (231) | +0.7% / -1.3%, -₹35 (229) |
| Random baseline (yardstick) | -3.3% / -0.3%, -₹349 (842) | -0.3% / -1.3%, -₹155 (833) |

**Nothing passes**, buying or selling. Shadow accounts over the 524 days (every signal, 1 lot): buying -₹22.2 lakh on
9,450 trades (29% won), selling -₹12.3 lakh on 9,357 (35% won). Margin per sold lot about ₹1.9 lakh (estimate).

**Reading.**
- Selling loses less than buying on every setup (time decay works for the seller), but after spread and charges it is
  still negative; even the random-entry seller loses ₹155 a trade.
- The 10 EMA claims do not hold on real option prices; its selling version is among the least bad but still -1.4% /
  -2.2% a trade.
- The gap-down setups have too few days (24 learn) to judge; their unseen numbers are on 8-10 trades.
- Same conclusion as research 0001-0004: price-pattern entries on index charts have no edge after costs; the next
  place to look is what price charts do not show (option-side flow, order flow, in-play stocks, breadth), which needs
  Tanu's own tick tape after cutover.

## Next
- Selling variants worth testing as their own ideas (counted): wider OTM strikes, premium stop 50%, hold to the close
  on range days, expiry-day theta. Positional selling (35-60 days, daily chart) needs the swing / positional engine (T8).
