# UX (light, clear, phone-friendly)

Mandeep: "abhi jaisa light hai theek hai, heavy nahi chahiye, but UX achha karna".

## Principles
1. **Answer first.** Each screen opens with the one thing to know now (e.g. "Trend day up · Sniper: 1 open, +₹420").
2. **Few screens.** Four, not twenty: Today · Setups · Journal · System.
3. **Every alert is a decision card:** what, when, why (state + setup), stop, target, cost, evidence (n trades, ₹ and
   % per trade on unseen days). No card without evidence.
4. **Calm by default.** No blinking, no walls of numbers; details on tap.
5. **Same look everywhere:** one set of colours / spacing / type tokens; light and dark; green / red only for money.
6. **Fast:** each screen under 1 s; live numbers pushed over websockets, not polled.
7. **Phone first:** 390 px wide, no sideways scroll, thumb-reachable actions.

## Screens (built in 0.8.0, `tanu/ui/`)
| Screen | Shows |
|---|---|
| Today | market state (index day type, with / against), radar of instruments "in play", open paper trades, today's P&L, new alert cards; 0.9.0: the Live block (minute of the day, feed, index prices, open paper trades, today's signals with status) |
| Setups | each setup: status (shadow / trial / live / retired), results learn vs holdout vs live, which situations it works in |
| Journal | every paper trade with its footprint; replay the chart around it; the evening review |
| System | feed, tape, disk, gaps, delays, logs (searchable), versions; 0.9.0: switch-over setup (Upstox key, Telegram, live switch, daily login), secrets never shown back |
