# ADR-0010: Every setup has a buying and a selling version; separate paper accounts at Rs 5 lakh each

- Status: Accepted
- Date: 2026-11-13
- Decided by: Mandeep ("option selling ka setup bhi banana hai test karo ... option selling ka paper trading ke under
  ek section ... har account ka alag alag capital 5L each ... sab setup ya stretegy ke liye ... har stretegy ko
  selling ke liye bhi modyfy karo or buying ke liye bhi")

## Context
Until 0.11.0 a signal in direction d bought an at-the-money option (call for up, put for down). Option sellers act
on the same views the other way round: sell a put when the view is up, a call when it is down, and earn time decay
when the view is right or nothing happens. Mandeep wants both tested for every strategy, with selling trades kept
apart in the app.

## Decision
1. **Gun, not bullets.** The engine creates, for every signal of every setup, a second position: the selling version
   `<id>@sell` (sell a put for d > 0, a call for d < 0). Setup files need no change; each may tune its `sell` plan
   (strike "otm" n strikes out of the money, or "at_stop" = the strike at the setup's own stop level; premium stop %,
   premium target %, flat-by time) or switch it off (`"sell": {"on": false}`).
2. **Short fills and costs.** Sold at the option's next-minute open minus half the measured spread and a tick; bought
   back at the exit price plus half the spread and a tick; STT on the sell side, stamp duty on the buy side (dated
   charges). P&L = (sold - bought back) x quantity - charges; % per trade is on the premium received.
3. **Exits.** The setup's own underlying stop / target (the view is wrong or done) first, then premium stop (premium
   up x%), premium target (premium down y%), the setup's time stop, flat by 15:15. Intraday only for now.
4. **Accounts.** Four paper accounts, Rs 5,00,000 each: shadow and sniper (buying), shadow_sell and sniper_sell
   (selling). Selling accounts take only `@sell` positions; sniper_sell also checks margin (estimate: notional x 12%
   for index options, dated in `facts/margins.json`) against 80% of its capital.
5. **Evidence.** A selling version is judged against the selling version of the random baseline
   (`random_baseline@sell`) with the same exit and filter; buying and selling versions both count as ideas tried
   (ADR-0005). A version whose yardstick is missing from a run cannot pass.
6. **App.** The paper-trades page has two tabs, Option buying and Option selling; Today shows the four accounts;
   Setups shows each setup's buying and selling evidence.

## Consequences
- Every replay and live day produces twice the positions; buying results are unchanged (golden: all earlier trades
  identical).
- Margin is an estimate, not a broker quote; hedged selling (buying a far option to cut margin), positional selling
  (35-60 days, from the 10 EMA video) and option-selling-only setups are later work.
- Selling has open-ended risk on a real account; on paper the premium stop and flat-by limit it. Tanu still never
  places an order (ADR-0001).
