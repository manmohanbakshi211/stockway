# Roadmap

Each phase ships as a tagged release with tests, docs and a CHANGELOG entry. A phase is done only when its
acceptance checks pass. Order follows the research in claude/tanu_blueprint.md and claude/chart_research.md.

| Phase | What | Acceptance (must all pass) | Status |
|---|---|---|---|
| **T0** | Foundation: package, settings, logs, safety guard, doctor, boundary tests, docs | tests green; no Manu ↔ Tanu imports; docs complete | ✅ 0.1.0 |
| **T1** | Data: tape writer + reader (✅ 0.2.0), feed process on a fake broker, 1-min bars from tape, dated facts, gap check + fill, legacy import (✅ 0.3.0); chains from the feed, universe / instrument list, token keeper (✅ 0.9.0) | a recorded fake day replays byte-for-byte ✅; bars from tape = broker 1-min candles (check on the first live day); legacy import counts match ✅ | mostly done |
| **T2** | Costs: spread, slippage, tick size, charges, theta → round trip per instrument per day; tradeable list (on trial) | measured from quotes (ADR-0008); compared with the legacy Lab (Spearman 0.60, Lab overstated stock costs); list per day ✅ | ✅ 0.4.0 |
| **T3** | ✅ engine core 0.5.0 (research 0001: no setup passes yet); 0.6.0 exit variants, filters and the ledger-controlled search; research 0002: 56 combinations, none passes (k = 42). State + setups + engine + evaluator; first bullets: sharp 15-min move on a trend day (with the move), VWAP pullback 11-13, pin at a level 10-11, chandelier exit; no buys against a trend day / on expiry day (shadow) | golden replay test; evaluator passes known-answer tests; each bullet's report (₹ and % per trade, learn vs holdout) | |
| **T4** | ✅ 0.7.0 paper accounts + risk + journal + deterministic evening review (AI write-up later); Sniper account (takes trial / live setups only) | fills at bid / ask with delay; every trade has a full footprint; review writes hypotheses to the queue | |
| **T5** | ✅ 0.8.0 light app: Today · Setups · Journal (trade charts) · System; `tanu web` on 8790 | headless check: no console errors, no sideways scroll at phone width; each screen loads < 1 s | |
| **Cutover** | ✅ 0.9.0 built: live loop, token keeper, universe, alerts, supervisor, rehearsal, Start Tanu. Left: the switch (Mandeep's go) and one clean live day | rehearsal = replay on real days ✅ (3 days); one full market day: no gaps, no errors, tape = broker candles; Mandeep's go | built, switch off |
| **0.10.0** | Mandeep's gap-down idea (research 0003): morning features + 2 shadow setups; real-option test on the Mac next | pre-test on index ✅; research / search on the Mac | built, shadow |
| **0.11.0** | 5 EMA video (research 0004): timeframe EMA alerts, rr exit, re-entries (gun) + 2 shadow setups | index pre-test ✅ (claim not met); research on the Mac | built, shadow |
| **0.12.0** | Option selling version of every setup + 4 paper accounts at ₹5L + Selling tab; 10 EMA video (research 0005) | real-option research on the Mac ✅ (nothing passes; selling loses less) | built, shadow |
| **T6** | Video Lab: link / file → transcript → idea cards (gun vs bullet) → tests (history, recordings, shadow) | every card recorded with its result, failed ones kept | |
| **T7** | Stock options on the low-cost list, "in play" days only | passes ADR-0005 | |
| **T8+** | Swing / positional, investing (cash stocks), ETF, mutual funds as horizon plugins | same protocol | |

## Cutover checklist (ADR-0009)
- [x] Live path = replay path (rehearsal on 23 Oct, 27 Oct, 6 Nov 2026: same trades, tape rebuilds bars).
- [x] Token keeper, universe inside Upstox limits, alerts, end-of-day jobs, Start Tanu.command (own environment).
- [ ] Mandeep enters the Upstox API key / secret and Telegram bot on the System page (Mac only).
- [ ] After market: close the old app, `python -m tanu cutover`, double-click Start Tanu, log in with Upstox.
- [ ] Tanu feed connects, subscribes, reconnects; tape written all day; disk card green.
- [ ] End-of-day gap check: 0 missing minutes (or filled and marked) for every index and stock.
- [ ] Paper broker and journal ran all day; evening review produced.
- [ ] Legacy data imported; counts match.
- [ ] Start script switched to Tanu; legacy code moved to `legacy/`; Mandeep's go.
