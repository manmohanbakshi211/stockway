# Research 0004: the "5 EMA" video (13 Nov 2026)

**Input.** A YouTube transcript sent by Mandeep (presenter Subhashish, "5 EMA, game changer scalping setup"). Mandeep:
in it "buying" means buy a call, "selling" means buy a put. Only the rules are kept here, in our words; the
transcript itself is not stored.

**Rules (as stated).**
- Selling side (buy PE): 5-minute candles, 5 EMA. Alert candle = closes above the EMA and its low does not touch it.
  If the next candle is also such a candle it becomes the alert. Enter when price breaks the alert's low. Stop = the
  alert's high (keep at least Rs 30-40 on the option). Target at least 1:3. At most 3 entries a day.
- Buying side (buy CE): the mirror on 15-minute candles (alert below the EMA, high not touching; enter on a break of
  its high; stop = its low).
- Claims: "accuracy 60-70%, even 50% is fine at 1:3"; "you will never miss 90% of trend reversals".

**Gun vs bullet.**
- Gun (built once, 0.11.0): higher-timeframe candles with an EMA inside the 1-minute engine and the alert-candle
  rule (`tanu/engine/tf.py`, features `ealert_<tf>m_<n>` and `ealert_<tf>m_<n>_stop`, any timeframe / period by
  name); exit type `rr` (stop at a level from the signal, target at R x risk, no trail); `max_per_day` re-entries,
  one position at a time.
- Bullets (shadow): `ema5_sell_5m` (puts) and `ema5_buy_15m` (calls), 1:3, max 3 a day, ATM options.

**Index pre-test (2022-01 to 2026-11-11, ~1,190 days, no option prices, no costs).** Win = target before stop.

| Side | Index | Trades | Target first | Stop first | Flat 15:15 | Avg R | Avg index points |
|---|---|---|---|---|---|---|---|
| Sell 5m (buy PE) | Nifty | 3,202 | 24.6% | 70.9% | 4.6% | +0.06 | +1.3 |
| Sell 5m (buy PE) | Bank Nifty | 3,103 | 23.4% | 71.3% | 5.3% | +0.02 | +1.9 |
| Buy 15m (buy CE) | Nifty | 1,531 | 15.1% | 62.9% | 22.0% | -0.01 | -0.4 |
| Buy 15m (buy CE) | Bank Nifty | 1,455 | 15.1% | 61.4% | 23.4% | -0.01 | +0.4 |

Versions also looked at (all in `setups/pretests.json`): the alert staying until a candle closes back through the
EMA (same results), and the video's minimum stop (~Rs 35 on the option = 70 index points: Nifty sell target 6%,
mostly flat at the close, +0.05R; buy -0.02R).

**Costs.** Measured ATM round trip (ADR-0008): Nifty ~3.2% of a ~Rs 115 premium = ~Rs 3.7 = ~7 Nifty points at
delta 0.5; Bank Nifty ~1.7% of ~Rs 617 = ~Rs 10.5 = ~21 points. The best side earns +1.3 / +1.9 points before costs,
so after costs every version loses.

**Reading.** The accuracy claim does not hold: 15-25% of trades reach 1:3, not 60-70%. The selling side is about
break-even before costs (it does catch the falls it is built for, but pays many small stops on the way); the buying
side is not. The engine replay of the two setups on the full history gives the same picture (sell: 23% target,
71% stop; buy: 15% target).

**Next.** Real 1-minute option prices on the Mac (`python -m tanu research`): the definitive ₹ and % per trade after
costs, learn vs unseen days. Until then both stay in shadow (graded, no phone alerts); expected to be recorded as
failed, and kept.
