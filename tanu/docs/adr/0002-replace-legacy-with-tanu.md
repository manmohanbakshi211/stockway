# ADR-0002: Tanu replaces the legacy app

- Status: Accepted
- Date: 2026-11-13
- Decided by: Mandeep ("ab pura manu ka system tanu se replace karna hai ... aaj se samajh lo manu khatam tanu shuru")

## Context
The legacy app (~52,800 lines of Python, ~90 live modules, 15+ overlapping engines, 2-4K alerts a day) grew feature by
feature without proving each engine's value. Research (claude/chart_research.md): no chart setup alone beats a random
option buy after costs; the market situation and a trailing exit matter most; stock options cost 2-19% a trade
depending on the name. A clean core built around evidence, costs and one engine is simpler and stronger.

## Decision
- All new work goes into `tanu/`. The legacy app gets no new features or patches.
- The legacy app keeps running on the Mac (paper + recordings) until **cutover**: Tanu's feed, recorder and paper run
  one full market day with no gaps, no errors and data matching the broker's 1-min candles.
- At cutover: the start script launches Tanu; legacy code moves to `legacy/`; after two clean weeks it is deleted.
  All legacy data stays (imported into Tanu's store).
- Proven ideas from the legacy app are rebuilt cleanly in Tanu, not copied.

## Consequences
- For a while two codebases live in one repo: enforced by ADR-0007 (no overlap) and boundary tests.
- Root documents (README, CLAUDE.md, VERSION) stay legacy until cutover; Tanu's live under `tanu/`.
