# Roadmap

Each phase ships as a tagged release with tests, docs and a CHANGELOG entry. A phase is done only when its
acceptance checks pass. Order follows the research in claude/tanu_blueprint.md and claude/chart_research.md.

| Phase | What | Acceptance (must all pass) | Status |
|---|---|---|---|
| **T0** | Foundation: package, settings, logs, safety guard, doctor, boundary tests, docs | tests green; no Manu ↔ Tanu imports; docs complete | ✅ 0.1.0 |
| **T1** | Data: tape writer + reader, feed process (tested on a fake broker), 1-min bars from tape, chains, dated facts calendar, gap check + fill, legacy import (5-year Lab tables, recordings, alerts, paper) | a recorded fake day replays byte-for-byte; bars from tape = broker 1-min candles; legacy import counts match | next |
| **T2** | Costs: spread, slippage, tick size, charges, theta → cost drag per instrument per day; tradeable list | drag per symbol matches the 5-year Lab within tolerance; list published daily | |
| **T3** | State + setups + engine + evaluator; first bullets: sharp 15-min move on a trend day (with the move), VWAP pullback 11-13, pin at a level 10-11, chandelier exit; no buys against a trend day / on expiry day (shadow) | golden replay test; evaluator passes known-answer tests; each bullet's report (₹ and % per trade, learn vs holdout) | |
| **T4** | Paper broker + risk + journal + evening AI review; Sniper paper account | fills at bid / ask with delay; every trade has a full footprint; review writes hypotheses to the queue | |
| **T5** | Light UI: Today · Setups · Journal (with day replay) · System | headless check: no console errors, no sideways scroll at phone width; each screen loads < 1 s | |
| **Cutover** | Tanu's own feed live; legacy app stops | one full market day: no gaps, no errors, tape = broker candles; Mandeep's go | |
| **T6** | Video Lab: link / file → transcript → idea cards (gun vs bullet) → tests (history, recordings, shadow) | every card recorded with its result, failed ones kept | |
| **T7** | Stock options on the low-cost list, "in play" days only | passes ADR-0005 | |
| **T8+** | Swing / positional, investing (cash stocks), ETF, mutual funds as horizon plugins | same protocol | |

## Cutover checklist
- [ ] Tanu feed connects, subscribes, reconnects; tape written all day; disk card green.
- [ ] End-of-day gap check: 0 missing minutes (or filled and marked) for every index and stock.
- [ ] Paper broker and journal ran all day; evening review produced.
- [ ] Legacy data imported; counts match.
- [ ] Start script switched to Tanu; legacy code moved to `legacy/`; Mandeep's go.
