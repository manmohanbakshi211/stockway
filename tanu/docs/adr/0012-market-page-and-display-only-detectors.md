# ADR-0012: One Market page from public sources, and display-only detectors

- Status: Accepted
- Date: 2026-11-13
- Decided by: Mandeep ("Bahut saare detectors: sharp move, unusual options, OI buildup, IV spike, VWAP, ORB, patterns,
  candles, news, insiders, FII/DII ... ye abhi rakho sirf dikhane ke liye ... page jaise world, market insider,
  watchlist, market context ka sab milake ek page banao andar alag alag tab banado")

## Context
The legacy app had separate pages for World, Insider trades, Watchlist and Market context, and many detectors (3,474
alerts on 13 Nov). Tanu has the evidence that none of the chart setups makes money after costs, so the detectors are
wanted as information on screen, not as trade signals. After the switch-over the legacy app is closed, so Tanu must
read these sources itself.

## Decision
1. **One page, six tabs:** Context (GIFT Nifty, world cues, India VIX, FII / DII cash, participant positions, F&O ban,
   results this week, high-impact events, important filings), World (markets, Indian ADRs vs NSE, economic calendar,
   world news), Insiders (FII / DII history, participant positions, insider trades with net by stock, bulk and block
   deals), Watchlist (Mandeep's symbols with price, ban, next event, insider net, latest filing and headlines), News
   (Indian market news and filings), Alerts (the alert book).
2. **Gun:** `tanu/intel/` reads public, non-broker sources (NSE website and archives, NSE IX, Yahoo Finance,
   ForexFactory, RSS feeds) through one client: every URL through `safety.check_url`, browser-like headers, timeouts,
   polite gaps per site, size cap, no secrets. Each source runs in isolation and is cached in `data/tanu/intel/<id>.json`
   with its time and error; a failed read keeps the last good data (shown with its age). Parsers are pure and tested
   on saved samples. **Bullets:** `sources.json` (what, how often, on / off), `world.json` (markets),
   `feeds.json` (feeds, headline and filing rules), `detect/detectors.json` (thresholds, cooldowns).
3. **When sources are read:** before the switch-over only while the Market page is open (the legacy app reads the
   same NSE pages from the same Mac; keeping Tanu's reads to page views keeps the load small, ADR-0007). After the
   switch-over the supervisor's `tanu-intel` thread reads every source on its own schedule.
4. **Detectors are display only.** `tanu/detect/` runs on the live loop's closed minutes (sharp move against the
   option-implied move, opening range break, new day high / low, VWAP cross, volume surge, unusual option buying /
   writing, option OI build-up, OI walls, PCR shift, IV spike, 5-minute candles, double tops / bottoms). New items from
   the sources (big insider trades, red-flag / buyback / results filings, high-impact headlines, the day's FII / DII,
   large world moves) go to the same alert book. Nothing is sent to Telegram, nothing feeds a setup, an account or a
   broker. A detector that raises is switched off for the day and logged; the live loop catches any detector error.
5. **Noise control:** each alert kind has a cooldown per symbol and direction; the Alerts tab shows "important and
   up" by default with counts by kind, and filters by day, level and kind.

## Consequences
- Tanu's live day carries the legacy app's information pages without the legacy code (no imports, ADR-0007).
- Stock volume "pace" is measured against the stock's own day so far (Tanu keeps no normal-volume history yet);
  index VWAP is not available (no index volume). OI build-up is read from the watched option strikes, not futures.
- NSE sometimes refuses automated reads (403); the page then shows the last good data with its age and the error.
- Turning a detector into a trading setup would need the full evidence path (ADR-0005), like any other idea.
