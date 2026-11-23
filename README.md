# Stockway

> **15 Nov 2026:** Stockway is the only app. The trial rebuild ("Tanu") was removed in 2.78 after its useful pieces
> were copied in (gap fill 2.74, light / dark theme 2.75, replay 2.76, Strict track 2.77). Start it as always with
> **Start Radar.command**.

A Mac app (runs in your browser, on your own computer) with two parts:

- **Live market** – streams Nifty, Bank Nifty, Fin Nifty, Midcap Select and every F&O stock with
  their options (CE and PE) from Upstox, checks everything every second, and alerts you the moment
  something big happens. Includes a scanner, option chains, AI explanations, Telegram alerts and
  automatic paper trading.
- **Daily radar** – learns from 10+ years of Bank Nifty prices, India VIX, global markets, events
  and news, and tells you every evening whether **tomorrow is likely to be a big-move day**.

Not financial advice. Paper trade the signals before using real money.

---

> **Alerts and paper trading only.** Stockway never places real orders or touches money in a trading account;
> Upstox is used for market data only.

## Install (once)

Needs git (type `git --version` in Terminal; if it isn't installed, macOS offers to install it) and Python 3.10+.

```bash
git clone https://github.com/manmohanbakshi211/stockway.git ~/Documents/banknifty_radar
```

Then double-click **Start Radar.command** in that folder. The first start installs everything
(a few minutes). Your browser opens **http://127.0.0.1:8765**. Keep the Terminal window open while
you use the app; close it to stop the app.

Tip: drag **Start Radar.command** to the right side of your Dock for one-click launch.

## Updates

Automatic. Every time you start the app, it pulls the latest version from GitHub and installs any
new packages. Your data, trained models, results and settings stay on your Mac and are never
uploaded (see `.gitignore`). The version and date show at the bottom of the sidebar.

## First use

Go to **Data & settings** and click:

- **Train model** – prices, VIX, global markets, events, plus any news already downloaded. A few minutes.
- **Download news** (optional) – GDELT news since 2017, saved as it goes. The free server often limits
  requests, so it may take a few tries. Retrain once it's complete.
- **Train on demo data** – fake prices, just to explore the app offline.

Then open **Tomorrow** and click **Update forecast**.

## Live market (Upstox)

Setup once, in the app under **Live setup**:

1. Open https://account.upstox.com/developer/apps, click **New app**, name it Stockway.
2. Redirect URL: `http://127.0.0.1:8765/upstox/callback` (exactly).
3. Paste the app's API key and secret into Live setup and save.
4. Click **Log in with Upstox**. Upstox ends every login at 3:30 AM, so log in once each morning.
   The scanner then starts by itself at 9:00 AM and stops after 3:30 PM.

No Upstox account yet? Click **Run simulator** on the Live page: a made-up trading day with the
same screens, to learn the app. Simulator results are kept separate and mean nothing for real trading.

What it watches (about 2,300 instruments on one Upstox connection):

| Signal | Meaning |
|---|---|
| Sharp move | 5-minute move far bigger than what option prices expect |
| Opening-range breakout | Price clears the first 15 minutes' high or low, with volume |
| New day high / low, VWAP cross | Key intraday levels taken out |
| Volume surge | Volume running 3×+ its normal pace with a real move |
| Futures OI buildup | Long buildup, short buildup, short covering, long unwinding |
| Unusual options | One strike trading 5×+ its pace with big money (₹ lakh), buying vs writing |
| OI wall shift | Biggest call OI (resistance) or put OI (support) moved |
| PCR shift, IV spike, VIX jump | Positioning and fear changing fast |
| Bigger than priced | Day's move exceeds what options priced for the whole day |
| News | Headlines that name an F&O stock |

Also: order flow (buyer vs seller initiated volume, absorption, ₹5 cr+ single trades), dealer gamma flip
for indices, and live NSE company filings (results, order wins, M&A, pledges, resignations).

Every alert carries:
- a **signal score** (how many independent facts agree, the alert type's track record, today's market type,
  and a learned model once it has enough history)
- its **track record**: "worked 58% of 41 times"
- a **target** and the level where the **idea is wrong**, from real chart and option levels
- the **trade structure**: buy the option, use a debit spread (when options are expensive), or skip
- related alerts on the same stock grouped into one **story**

Every alert is followed for 60 minutes and graded (**Scorecard** page). Alert types that work under 35% of
the time are demoted automatically. Everything streamed is recorded on your Mac (compressed daily), and a
model retrains every evening on graded alerts.

**Market context** page: FII / DII / Pro / Client positioning in index futures and options (NSE, daily),
FII/DII cash flows, F&O ban and near-ban stocks, bulk and block deals, live company filings, global markets.
**Briefs**: a pre-market plan at 8:45 AM and a post-market review at close, on screen and on Telegram.
**Paper trading risk guard**: lots sized to 1% risk of your capital, stops for the day after a 3% loss or
12 trades.

Each alert suggests the at-the-money option in its direction. Strong alerts are paper-traded
automatically (1 lot, +30% target, −20% stop, exit by 3:15 PM, real charges). The **Paper trades**
page shows which alert types actually make money. Use that before risking real money.

**AI (optional): your Claude Pro plan works here.** Live setup → 4. AI → "Claude: my Pro / Max plan". Stockway
asks Claude Code on your Mac, which is logged in with your Claude account, so there is no API key and no extra bill:
it uses your plan's normal limits (shared with your Claude chats). One-time setup: in Terminal run
`curl -fsSL https://claude.ai/install.sh | bash`, then type `claude` and log in. Claude Code runs with every tool
switched off (it cannot read files, run commands or browse), in an empty folder; it only answers. Automatic notes are
capped per day (12 by default) and spread out; questions you ask are not capped by that.
It powers **Ask Stockway** (chat in English or Hinglish, answered from the app's live data), **Claude's research
reviews** on the Time Machine, Strategy desk and Scorecard (a sceptical read: what the numbers say, how far to trust
them, what to test next), the "Why?" answer on alerts, the AI chart read, briefs and the market brain. The menu shows
which AI is connected ("Powered by Claude Opus · your Pro plan"). Other choices: a free Google Gemini key, or a Claude API key.
Only prices and public headlines are sent.

Costs: Upstox data is free with an account. Claude uses the plan you already have. Gemini free tier is free. Telegram is free.

## 2.96: The clash: live.db commits held every screen and the paper exits (23 Nov 2026)
- **Mandeep (23 Nov, 09:38, restarted on 2.95 in market hours): "clash bhi hai or delay bhi".** Read from the app:
  the paper-exit watcher's 0.25 s check took 2.5-4.1 s; Paper trades calls 2.3 s on average (one 20 s), the Live page
  refresh 1.2-5.5 s; System page "health and events" 2.6 s and "checks (live)" 2.1 s; one analysis pass 15.3 s, of
  which "Saving 1-minute candles" 11.7 s; the Mac on battery.
- **Cause 1, the clash:** live.db (alerts, paper trades, outcomes, settings) was in SQLite's old journal mode with one
  connection and one lock. Every commit (several a second: alerts, paper marks, trackers) created, synced and deleted a
  journal file while holding the lock that every screen, the paper-exit watcher and the health view wait for.
  **Fix:** WAL (a commit appends to one log; synced at checkpoints: an app crash loses nothing, only a power cut can lose
  the last moments, the same as the test bench since 2.30), and the screens read on their own connection, never waiting
  for a commit (alerts, alert days, paper trades, settings). Measured on a 311 MB copy: 0.69 ms → 0.01 ms per commit
  here; much more on the Mac, where each sync is slower. Backups (SQLite's own copy) and every reader work the same.
- **Cause 2:** the System page counted the day's recording (13 counts over minutes, chains, news) on every refresh while
  holding the recording's lock, so the live loop's saving waited. Now counted at most once a minute.
- **Cause 3:** the 30-minute save of the day's 1-minute candles ran inside the analysis pass (11.7 s right after a
  restart). Now in its own thread (one at a time), with only a quick copy under the price lock.
- Keep the Mac on the charger in market hours: on battery macOS slows the CPU.
- Tests: `tests/test_speed_296.py` (4): a read during a 3-second write returns at once and sees the write after its commit.

## 2.95: Market-hours speed: the 5-year download waits, the System page and board do less (23 Nov 2026)
- **Mandeep (23 Nov, 09:19, market open): "check"** the Speed panel. Read from the running 2.94: System page 4.5 s on
  average (max 10 s), of which "jobs and log" 3.7 s; Strategy board 3.9 s (max 12 s); Alerts 1.8 s; live analysis pass
  2.8 s at the open (95% within 10.9 s, then 0.3 s); the 5-year Dhan download still running in market hours
  (23,581 calls by 09:19) inside the app, competing with the live scanner for the same Python.
- **System page:** "jobs and log" was the 5-year download's full view: it parsed both Strategy Lab results files
  (5.3 + 4.2 MB) for a comparison the System page never shows, and listed every downloaded file. Now a brief (running,
  status, % done); the full view (5-year Lab panel) parses the two files only when they change; the manifest is read at
  most once a minute.
- **5-year download waits out market hours**, the same window as the nightly Lab (15 min before the open to 5 min after
  the close, config.yaml lab: pause_in_market). It stops at a safe point, shows "paused" and resumes by itself after the
  close. Dhan's daily cap (92,000 calls) is reached at night anyway, so the download is no slower.
- **Strategy board:** the finished days are added up once (per days, family, day type) and kept; each call adds only
  today's rows. Pages share an answer for 10 s; the Live page's "what works today" asks for the board without its
  ~4,000 rows (`mini=1`). Same answers (tests: cached = fresh, today's new trades show at once).
- **Alerts page:** its pieces are now timed (read, shadow Focus picks, days list, packing) under "Inside them" for the
  next fix.
- Tests: `tests/test_speed_295.py` (3).

## 2.94: Quicker screens: slow work done once, not on every call (23 Nov 2026)
- **Mandeep (23 Nov, the System page's Speed panel): "check the speed one".** Read from the running app (2.93, 3 minutes
  after a restart, 5-year download running): Trade journeys 9.4 s, Strategy board 5.3 s (later calls ~2 s), Daily Radar
  status up to 3.9 s (avg 0.9 s on 12 calls, every page load), System page 1.1-1.7 s on every refresh.
- **Why:** each call redid the same slow work. The status loaded the Daily Radar model file (and its ML libraries) and
  17 price files every time; the board read all ~400k bench trades after every restart and scanned the whole index for
  its days on every call; the System page parsed the Strategy Lab's 5 MB results to show one date and re-ran ~60 settings
  checks; Trade journeys decoded every recorded path again.
- **Fix (`webapp/memo.py`, gun):** an answer is kept until a file it came from changes (path, modified time, size), on
  disk too where a restart would pay again. Status: model dates and price files read only when they change. Board:
  finished days kept on disk, compressed (`data/bench_cache`, a day's fingerprint from the index: trades and highest id,
  so a voided day is read again; days older than 400 dropped); its days found by one index seek each. System: Lab date by
  file, settings checks at most once a minute, recording folders every 30 s; live checks every call. Trade journeys:
  until the nightly job changes them. The three heaviest are worked out in the background right after a start.
- Measured: board days index 0.4 ms (was 19 ms; ~1 s on the Mac); 408k test trades: first board 1.9 s, after a restart
  0.4 s (was 1.9 s every restart); repeat calls ~2 ms. Answers identical (tests).
- **Speed panel:** a slow call in the first 2 minutes after a start is marked "just after start"; the pill says when all
  of them were; "Inside them" shows which piece of the System page / Strategy board took the time.
- Tests: `tests/test_speed_294.py` (6). Headless check of System, Strategy board, Tomorrow at 1440 / 390, light / dark.

## 2.93: Pattern learning: which candle patterns pay, and where (23 Nov 2026)
- **Mandeep (23 Nov):** "patterens to observe karte raho or ye dekhna hai humein ki system patterens achhe se pakdna
  seekhe toh do the best." The candle-pattern test bench keeps running exactly as before; the system now learns from it.
- **Gun (once): `lab/pattern_learn.py`.** Every candle pattern occurrence is tagged with its situation and the system
  learns, pattern by pattern, which situations make it pay after every cost: the pattern's own average plus each
  situation's difference from it (measured against the occurrences where that fact is known), shrunk toward zero by
  shrink_k occurrences (the situation picker's method). Days split train 60 / validation 20 / holdout 20: learned on
  train, the pick (top x% by quality) chosen on validation, every number shown is from the holdout days.
  Two sources: years of history (the Lab's 5-minute bar table: the at-the-money option bought its way at real prices
  with every charge, and, learned separately, whether the price itself moved the pattern's way in the next 30 minutes)
  and the live test bench (15-minute to weekly candles, honest 1-lot fills, R after costs).
- **Bullets: `rules/pattern_learn.json`.** The situations (daily trend with / against, time of day, key levels and
  touches, volume, VWAP / day's average side, Nifty with / against, other timeframes' candles, room to the next daily
  level, RSI, ADX, candle size, expiry, event risk, IV vs HV ...), cut points, words, shrinkage, pick sizes, bars.
- **Live, shadow only:** every candle pattern the test bench takes gets a quality 0-100 from the model learned on every
  bench day so far (bench record tag `pq`). Nothing alerts, trades or changes a paper account on it. The **forward
  check** compares high-quality patterns with all of them on days after the model was built: the one test it could not
  have seen. A failing learner never stops the bench (tested).
- **Runs** every night after the Lab (1-year table + bench; the 5-year table too), never in market hours; Strategy Lab
  page → "Pattern learning" (Run now, learning curve). System & logins lists it.
- **First results (Mac data, holdout days only):**
  - 5 years, 51 symbols, 737,879 five-minute patterns: all −6.2% of the premium a trade; the top 10% by quality −2.3%;
    quality fifths −8.5% → −3.6%. Price moved the pattern's way 49% of the time (picked 50%): **no better than a coin**.
  - 1 year, 32 symbols, 278,880 patterns: all −6.4%, top 10% −1.8%; moved its way 48% (picked 51%).
  - Live bench, 11 days, 39,466 patterns (15m-1W): all −0.18R, top half −0.14R; quality fifths −0.26R → −0.10R.
  - What the learning found: indices lose far less than stocks (spreads), expiry day is the worst, high volume and the
    1-hour / 75-minute candles are better, the wrong side of VWAP is worse. It **learns to sort**, but no candle pattern
    pays after costs on its own yet, and none predicts direction: patterns stay information next to other factors.
- Lab regression: lab/factory.py and lab/exits.py untouched (only read), so every saved rule rescores identically.
  Tests: `tests/test_pattern_learn.py` (8). Headless check of the Lab page at 1440 / 390, light / dark.

## 2.92: A shorter menu with tabs, smaller backups, no ₹40K left on screen (23 Nov 2026)
- **Mandeep (23 Nov), after the clean-up check:** alerts unchanged; candle patterns keep being observed on the test bench
  ("patterens to observe karte raho ... system patterens achhe se pakdna seekhe"); "pure system mein ab 40K jaisa kuch
  nahi hona chahiye"; backups 3 days + compressed; data/tanu + .venv-tanu deleted; menu in tabs.
- **Menu: 29 entries -> 15**, nothing removed. Related pages share one entry and a row of tabs on top:
  Option chain (chain, Options lens), Market & news (Market context, World, Insider trades, Briefs), Daily radar (All
  indices, Tomorrow, History, Biggest days, What drives moves, Model check), Results (Strategy board, Strict track,
  Scorecard, Strategy desk), System & settings (System & logins, Live setup, Data & settings). Each entry opens the tab
  used last; every old link and bookmark (#/world, #/strict, ...) still opens its page, with its tabs.
- **Backups** (`live/backup.py`, `rules/backup.json`): each daily copy is checked, then gzip-compressed (restore:
  `gunzip -k live.db.gz` while the app is closed), and 3 days are kept (were 7 x ~700 MB = 3.9 GB on a 91% full disk).
- **₹40K:** the exit board's "lots <= ₹10K" column, the ML strategies' ₹40K account and the 5-year Lab panel's ₹40K line
  are gone; CLAUDE.md says the capital set in the app. Left, on purpose: the Lab's own nightly ₹40K report inside
  `lab/factory.py` (not shown anywhere): that file is part of the Lab table's fingerprint, and any edit makes the Mac
  rebuild every 1-year and 5-year table (days of work); it goes with the next change that has to touch that file.
- The clean-up check itself (Mac data, 5-19 Nov): 41,388 alerts in 10 days; after costs only ORB, breakout-retest, day
  high / low and the strategy files were positive on the test bench; candle patterns ~40k bench trades a day at -1.8%
  to -3.4%. Mandeep kept the alerts and the candle bench as they are.
- Headless check: menu and tabs at 1440 and 390, light and dark, no console errors, no sideways scroll.
  Tests: `tests/test_menu_tabs.py` (1); backup and exit-board tests updated.

## 2.91: Live prices on every page, as fast as the chart (23 Nov 2026)
- **Mandeep (23 Nov):** "har chart jitni speed se upstox mein chal rha hai utni hi speed se app par bhi ho. prices live aa
  rhe ho saari jagah par live page, paper trade ya jahan bhi sab same speed as upstox".
- **How a price travels (checked):** Upstox websocket -> the feed's own process (2.71) -> a pipe -> straight into the
  app's prices (no batching) -> the browser over the app's websockets. The chart already moved on every price (/ws/ticks,
  0.1 s); the Live page's prices (/ws/prices) and open paper trades / the option chain (/ws/quotes) every 0.2 s; other
  pages only on their own refresh (Scanner 2 s, Watchlist 3 s, Ghanshyam 6 s).
- **Now:** one watcher on every page streams any price the page shows (a symbol's price and day change, an option's
  price and the trade's P&L), whatever page it is: Live, Scanner, Watchlist, Paper trades (every account), Ghanshyam, the
  option chain. Prices and quotes are pushed every 0.1 s (like the chart; were 0.2 s), alerts / paper changes every
  0.15 s (were 0.2 s); up to 250 symbols a page (were 60). `rules/chart_stream.json`.
- **Why the Mac felt slow on 19 Nov (its log):** the Mac still runs 2.78. Two Lab builds (1 year and 5 years) ran
  during market hours and the Upstox feed timed out 24 times that day ("ping/pong timed out", each a 1-3 s freeze while
  it reconnects), clustered while the Lab worked; the analysis pass also took 3-10 s every 5 minutes (stats and the
  setup scan inside the loop). Both are fixed since 2.80 (the Lab waits outside market hours) and 2.81 (stats and the
  scan off the loop): they reach the Mac with the next Start Radar restart. Replay of 19 Nov on 2.91: a pass 0.1 s
  (95% under 0.25 s); threads never waited more than 5 ms in 99% of checks.
- Headless check on the 19 Nov replay (Scanner, Watchlist, Paper, Ghanshyam, Live; 1440 and 390): prices move on
  screen without a page refresh, no console errors. Tests: `tests/test_streams_everywhere.py` (2).

## 2.90: Trend Rider confidence: every factor at once, Ghanshyam included (23 Nov 2026)
- **Mandeep (22 Nov, 23:46):** "ye ensure karna ki ye sirf entry exit check hai sab baaki factors ke saath jaise ek hi
  time par ghanshyam wale points bhi check kiye live market or chart mein or ye bhi check kiya or sab milake confidence
  or strong kiya". Until now the EMA videos sat in Trend Rider's check, but Ghanshyam's points ran apart (his own paper
  account on Nifty / Bank Nifty) and Trend Rider never looked at them or at the other strategies.
- **Gun** (`lab/rider.py` `confidence`): one score 0-100 per direction on the same closed 5-minute bar. Each factor
  votes with (+1) / against (-1) / neutral (0) / no view: breakout, trend day, the families (EMA 9/21 trend, momentum,
  higher lows / lower highs, VWAP side, order flow, option thrust, the EMA videos), not chasing, move vs decay, a
  tradable option, strength vs Nifty, and what only the live engine knows: Ghanshyam's points (his strategy file on
  Nifty / Bank Nifty), the other named strategies (library, books, strategy files, fusions) that fired on the symbol
  in the last 15 minutes, MindGame's 5-minute buyer / seller lean (only for a direction with a trigger, time-capped)
  and the candle forecast. Score = 50 + 50 x weighted net vote / weights with a view (50 = as much for as against).
  Labels at the rules' min 75 (ok) and 85 (strong): a signal that passes Trend Rider's own checks already scores about
  75-90 (19 Nov replay: 77 / 82 / 89 at the 10th / 50th / 90th percentile), so these split the signals that pass.
- **Same scan, same bar:** the strategies (Ghanshyam included) are checked first in the 5-minute scan and recorded in
  the engine's confluence record; Trend Rider runs right after and reads them, so they are on the same bar.
- **Bullet** (`rules/buy_setup.json` "confidence"): weights, min, strong; `use_in_decision` **false**. Shown and
  recorded, the decision is exactly 2.84's.
- **Shown:** the alert says "Confidence 72/100 (strong): saath ...; khilaaf ..." and carries the votes; a seventh
  sub-chart "Confidence" (calls / puts, min and 50 lines; on by default); the reading card's "Confidence" line (both
  sides, with / against / no view, Ghanshyam's and the strategies' counts).
- **Graded on the live record:** the Strategy Lab panel shows the graded Trend Rider trial alerts split strong / ok /
  weak (signals, reached target, average R). Confidence may gate entries only if strong beats weak there and Mandeep
  says so. There is no Lab way for it: what it adds exists only live, and the factors the Lab can see score 80-90 on
  every signal that passes anyway (19 Nov: 67 of 67 above 79).
- **19 Nov replay (one day):** 67 signals, all with a confidence; MindGame voted on 46 (45 with), Ghanshyam on none (his
  rules fired once on Nifty before 11:00, not near a Trend Rider signal), the candle forecast on none (at the bar close
  the new candle's forecast is not out yet). By score: under 80 n 20, 10% reached target, +0.19 R; 80-85 n 32, 12%,
  +0.28 R; 85+ n 15, 27%, +0.25 R. Not proven: the live record decides. A pass takes ~0.45 s (MindGame capped at
  1.5 s a pass, only where the setup passes).
- Regression: as-live signals on 19 Nov identical (224 of 224 symbol-days). Tests: `tests/test_ema_videos.py` (+4).

## 2.89: The 5 EMA and 10 EMA videos inside Trend Rider, on trial (22 Nov 2026)
- **Mandeep (22 Nov):** "jo 10 EMA or 5 EMA wali transcript thi uska bhi kuch use hua hai? ... saare factors ke sath ye
  bhi include karo jo transcript me hai". Until now: both videos were built in Tanu (13 Nov, research 0004 / 0005) and
  lost on 524 days of real option prices (5 EMA puts -₹137, calls -₹276, 10 EMA -₹229 a trade; random entry -₹349);
  when Tanu was removed (2.78) they were not copied, so Trend Rider did not use them.
- **Gun** (`lab/rider.py`, rules copied from Tanu's `engine/tf.py`, judged on closed 5-minute bars):
  - `ema_alert(b, n, k)`: the 5 EMA alert candle on k x 5-minute candles. A candle closing above the EMA with its low
    off it is a put-side alert (a later such candle replaces it); a break of its low in the next candle fires a put,
    stop = the alert's high; the mirror for calls. An alert lasts one candle.
  - `ema_pullback(b, n, k)`: the 10 EMA trend pullback. Above the EMA a red candle whose low stays off it; a break of
    its high in the next candle fires a call, stop = the EMA; the mirror (green below, break of its low) for puts.
  - `tf_groups` (15-minute candles from 5-minute bars, aligned to 09:15); `ema_videos` = both, with the bullets.
  - Exit (`lab/rider_exit.py`): `trail_mode` "ema" = the 10 EMA video's trail (after +1R the stop sits at entry;
    from 1:2 it follows the 10 EMA of the 5-minute closes). Default "atr" = exactly 2.85.
- **Bullets** (`rules/buy_setup.json` "ema_videos"): puts on 5-minute candles, calls on 15-minute (as the video), 10 EMA
  on 5-minute; a sixth agreement family "ema" = a video trigger in that direction within the last 3 bars.
  `count_in_agreement` and `as_trigger` are **false**: computed, recorded and shown, the decision is exactly 2.84's.
- **Live:** every bar's row has the distance from the 5 / 10 EMA (ATRs), what fired and the family vote; the alert
  carries them; the paper plan carries the setup's 10 EMA.
- **Chart:** a sixth sub-chart "EMA videos" (close vs 5 EMA and vs 10 EMA in ATRs, 5E / 10E marks where a rule fired);
  the reading card has an "EMA videos" line (what fired, which side the family agrees with, counted or not).
- **Lab** (`lab/rider_lab.py`, nightly): one reading, three entry ways per bar: as live, + the EMA family counted
  ("ema:"), + an EMA trigger standing in for a missing breakout ("trig:"); each also with the 10 EMA trail ("_t10").
  Signal cache rebuilt once (new format). The Strategy Lab panel shows those rows next to as live and the bench exit.
- **Regression:** on the 19 Nov days (224 symbols) the as-live signals are identical to 2.88 (224 of 224 symbol-days)
  and its result is the same (+₹17,274 on 52 trades). The other ways that day: EMA family counted 56 trades
  +₹16,303; EMA trigger 65 trades +₹17,320; 10 EMA trail +₹19,367 (as live entries), bench exit -₹28,310. One day.
- Tests: `tests/test_ema_videos.py` (7); Trend Rider tests updated.

## 2.88: Trades on a day without an NSE session are void (22 Nov 2026)
- **Why:** Mandeep's status check (22 Nov): 10 Nov 2026 was an NSE holiday, yet the main paper account shows -₹22,753
  of trades that day (opened on stale prices before 2.48 checked the NSE calendar). They are not real results.
- **What** (gun, `live/voidday.py`, once at every start, idempotent): a paper trade of the real market whose day had
  no NSE session (weekend or holiday, `live/session.py`; a special session such as Muhurat counts as a session) gets
  status "void" and "void: no NSE session that day" in its reason; a test-bench trade of such a day moves to the source
  "<source>|void". Nothing is deleted. Every money figure counts only closed / open trades of its own source, so void
  trades leave the accounts (main and extra), free money, the bench board, the strict test and the multiverse.
  Practice sources (sim, replay) are left alone. The start log says how many trades were voided and which days.
- Tests: `tests/test_voidday.py` (3).

## 2.87: Trend Rider tested on the Lab's history, by days (22 Nov 2026)
- **Why:** the 19 Nov replay was one day. Before any Trend Rider number changes, the setup and its exit are measured on
  every day of the Lab's option history (the year of stocks and indices; five years of Nifty / Bank Nifty once that
  archive is there). Mandeep: "baaki jo pending the ... ek ke baad ek khud karna".
- **Gun** (`lab/rider_lab.py`, Lab job "rider" in `lab/proc.py`, outside the Lab table build): the underlying's
  1-minute closes become 5-minute bars and the live setup (`lab/rider.py`) is read at each closed bar as live (first
  look 09:30, the windows, 2 signals a symbol a day 15 minutes apart). Entry: the at-the-money option at the bar's
  last minute + the Lab's slippage. Exit: the Trend Rider exit (`lab/rider_exit.py`) fed every minute; out at the
  minute's close - slippage; real charges, 1 lot (rules/lots.json). The same entries also exit the bench way
  (+30% / -20% / 60 min) as the yardstick.
- **Proxy, said on the page:** the Lab has no volume, bid / ask or order flow and no live greeks, so VWAP is
  time-weighted, flow and option thrust are unknown (at most 4 of 6 families can agree), delta 0.5, the theta
  stand-in.
- **Variants:** first look 09:30 (live) / 09:45 / 10:00 × no move after 3 (live) / 4 / 6 bars / never × emergency
  -35% (live) / -25% = 24, plus the bench exit.
- **Grading by days** (a day's trades are not independent): ₹ a day for 1 lot of each signal, t, the one-sided 95%
  lower bound, green days. The choice is made on the first 80% of days only; the last 20% (holdout) is never used to
  choose. Walk-forward: each month is traded by the way that was best on the months before it (3+ months, 20+ days).
- **Reports only:** nothing changes a rule, the live setup or a paper account; a change needs Mandeep's OK and a
  holdout win (CLAUDE.md champion vs challenger).
- **Nightly:** after the ML strategies (and on the 5-year archive after its Lab round), in its own low-priority
  process, waiting / frozen in market hours (2.80). Signals are cached per symbol and day (`data/lab/rider_signals.json`,
  rebuilt when the setup's code or entry numbers change), so a night reads only new days. Strategy Lab page: a
  "Trend Rider on the Lab's history" panel (as live, bench exit, best on train; all days, holdout, walk-forward) and
  Run now. System page: a new check.
- **Smoke run on 19 Nov** (the recording rebuilt as Lab days for 224 symbols; no yesterday's levels in a recording):
  52 signals (live had 66), 16 ms a symbol-day (a year of ~50 symbols: a few minutes). Trend Rider exit +₹17,274
  (14 won), bench exit -₹28,310, without the no-move exit +₹6,460, first look 09:45 -₹18,671. One day again: the
  year decides.
- Lab regression: identical by construction (no file of the Lab table build changed). Tests: `tests/test_rider_lab.py` (7).

## 2.86: Trend Rider sub-charts under the candles and its reading card (22 Nov 2026)
- **Mandeep (22 Nov):** "candle chart ke niche jo sub charts hain system unpar sab par sabkuch apply karre or analyse
  kare taaki main par dependent na ho or main chart clean rahe ... main chart ke side mein jo bhi analysis ya text hain
  vo bhi subchart ki reading se system lele".
- **Chart page, five sub-charts under the candles** (Indicators & levels → "Trend Rider: …"; agreement and option on by
  default, the rest one click away; intraday timeframes only; nothing is drawn on the candles' pane):
  - Trend & day: the trend-day score (line, its 50 bar dashed) and the distance from VWAP in ATRs (bars).
  - Breakout & chase: breakout quality when a level breaks (green up / red down bars, 55 bar) and the chase meter of
    that side (late above 65).
  - Flow: bid/ask + order-flow imbalance (bars, + = buyers) and strength vs Nifty.
  - Option: move vs decay for the call and the put (1 = worth it) and their tradability (dashed).
  - Agreement strip: how many families agree for calls (up) and puts (down), the 3 bar, and CALL / PUT marks where
    the whole setup passed.
  Each 5-minute reading sits on the bar it falls in, so the panes line up on 1m, 3m, 15m, 30m and 1H too.
- **Side card "Trend Rider reading"** (top of the chart's side column): the decision on the last closed bar (passes:
  which option, trigger and stop; or no trade and the first failed checks) and one line per sub-chart, from the same
  server numbers the sub-charts plot (`/api/live/rider/{sym}`: the rows of `live/rider.py`, the latest reading and
  the limits from `rules/buy_setup.json`).
- Side cards of the chart page are fetched again when you change timeframe (before, the engine eval card could stay
  empty for up to 20 seconds).
- Headless check on the 19 Nov replay (Nifty, TVS Motor; 1440 and 390 wide; light and dark; 5m, 1m, 15m, 1D): no
  console errors, no sideways scroll. Tests: `tests/test_rider_ui.py` (3).

## 2.85: Trend Rider exit and its own paper account, on trial (22 Nov 2026)
- **Mandeep (22 Nov):** "loss less ho, time limit tak sirf premium na ghista rahe agar koi movement ka chance nahi hai
  toh, reversal ho toh trail humara profit bachaye rakhe but ... ye nahi ki sl hit karwake aage ka achha move na le
  paaye. trade ka initial stoploss bhi achhi strong or confident ho", 1 lot, the capital chosen in the app, then
  "Karo ... ek ke baad ek khud karna".
- **Exit (gun, `lab/rider_exit.py`; outside the Lab table build, so no Lab table rebuild):** judged on 5-minute
  closes of the index / stock, never on a wick. (1) First stop beyond the broken level (or the breakout candle) +
  0.35 ATR. (2) At +1R the stop moves to entry: with 1 lot there is nothing to book half of, so locking is the
  booking. (3) Then a trail 2.5 ATR behind the best price, tightened to 1.5 ATR only when the closed candles show 2
  reversal signs (a rejection candle, two closes against, a close beyond the previous bar's far end): an ordinary
  pullback does not shake it out. (4) No move: 3 closes after entry, under 0.3 ATR of progress and the option not
  above entry, out (the premium does not melt to a time limit). (5) Emergency stop -35% on the premium, any tick.
  (6) Out by 15:10. The bar being built from ticks stays in memory, so the 0.25-second exit check writes the plan
  only when a bar closes.
- **Paper account "Trend Rider"** (`rules/paper_books.json` "rider", bullet): takes only Trend Rider signals, buys
  the suggested option, 1 lot, money = the capital set in the app, at most 5 open and 15 a day, the main account's
  daily loss limit. It follows the stop on the same price the setup's 5-minute bars use (the index / stock itself,
  not the future); a signal whose price is already through its stop is skipped. Never touches the main account,
  Telegram or Focus; the signal stays a trial (graded and on the test bench as in 2.84).
- **Re-entry:** signals now 15 minutes apart (was 30), still at most 2 a symbol a day: the second one is the
  re-entry after a stop (one open trade a symbol at a time).
- **Bullets:** `rules/buy_setup.json` "exit" (every number above) and "cooldown_min" 15; registry cooldown 15.
- **Replay check (19 Nov, the Mac's recording, one trend-up day; honest):** the account took 14 of 66 signals
  (the 09:30 burst filled the 5 slots; the daily loss limit and one-trade-a-symbol did the rest). Result -₹3,087
  (3 won). On the same 14 signals the test bench's buy with +30% / -20% / 60 min made +₹2,428 and holding to the
  day's end lost -₹14,379. What the exit did: TVS Motor +₹5,513 by trailing (bench +₹3,871 at its fixed target);
  no-move cut 5 losers smaller (Trent -₹114 vs -₹2,068); but no-move also cut TECHM at -14% before it ran to +27%
  (-₹2,452 vs +₹4,434), and Nifty's put fell 37% in 12 minutes, so the emergency stop (-35%) lost more than the
  bench's -20%. A second run with 10 slots took 11 trades, 10 of them at 09:30 (mostly puts on an up day), and lost
  -₹15,223: correlated first-bar signals, so the 5-slot limit stays. One day decides nothing: the Lab test (next)
  measures no-move after 3 / 4 / 6 bars, the emergency level and a later first window on a year of data before
  anything changes.
- System page: a new check (Trend Rider exit and paper account). Tests: `tests/test_rider_exit.py` (13).

## 2.84: Trend Rider v1, the option-buying setup, on trial (22 Nov 2026)
- **Mandeep (22 Nov):** "ek achhi option buying ka set-up banana hai ... saare FNO stocks and index ... loss less ho
  ... initial stoploss bhi achhi strong or confident ho", then "Karo". Plan: the doc "Option Buying Setup v1" and the
  project doc claude/buying_setup_plan.md (from the 5-19 Nov bench data: index option buys +0.04% a trade vs stock
  options -2.58%; breakout triggers, the first hour, opening-drive days and VWAP side did best).
- **Gun** (`lab/rider.py`, shared with the coming Lab test): Trend-Day Score (initial balance vs the day's ATR,
  opening drive, extension beyond the opening range, closes on one side of VWAP, VWAP lean), VWAP Pull-Go (side,
  distance and lean in ATRs; TWAP for an index), Breakout Quality (fresh break or break-and-retest of the day high /
  low, opening range, inside bar or yesterday's high / low: how clear, body, room to the next level, earlier tests),
  Chase Meter (how stretched: EMA 20 distance, closes in a row, last candle vs ATR, day range vs normal), Agreement
  (trend, momentum, structure, VWAP, flow, the option: how many families agree), Relative Strength vs NIFTY, Move vs
  Decay (the likely move over 45 min x delta against theta, half the spread and charges; above 1 = worth it),
  Tradability (spread, one lot against the account's capital, event day, ban list), Flow (bid/ask quantity
  imbalance and the order-flow change on the futures) and Option Thrust (the option's move against what delta says).
  The decision needs a breakout of quality 55+, trend-day score 50+, the right side of VWAP, chase 65 or less, 3+
  families, move vs decay 1+, tradability 40+, inside 09:20-10:15 or 11:15-13:15. The first stop is beyond the broken
  level (or the breakout candle) plus 0.35 ATR, judged on closes; a stop that would risk more than 30% of the premium
  skips the trade.
- **Live** (`live/rider.py`): every index and F&O stock at each closed 5-minute bar, inside the 5-minute scan (its own
  thread since 2.81); per-symbol readings and a row per bar are kept for the coming sub-charts. A passing setup is a
  trial alert ("trend_rider", shadow): recorded, graded on its levels (live/trials.py) and paper-tested on the test
  bench. Nothing you see or trade changes. At most 2 signals a symbol a day, 30 minutes apart.
- **Bullets:** every number in `rules/buy_setup.json` (valid from 23 Nov); a missing or broken file means the
  defaults, a symbol's bad data is logged and skipped.
- **Replay check (19 Nov, the Mac's recording, a trend-up day):** 63 trial signals over 224 symbols (49 of them
  09:20-10:15), graded on their levels: 12 hit, 23 missed, 28 timed out, average +0.23R. On the test bench the same
  signals bought ATM averaged +4.4% a trade (51% won) against -4.0% for every other alert's ATM buy that day (6,312
  trades). One day only: the Lab test and live trial decide. Move vs decay mostly passes on index options (it is a
  gate against slow expensive options, not a filter that bites often); the Lab will tune its level. The pass over all
  symbols took 0.39 s.
- System page: a new check (Trend Rider on trial). Tests: `tests/test_rider.py` (9).

## 2.83: Multiverse strict pick rule, in shadow (22 Nov 2026)
- **Mandeep (22 Nov):** "Go", after checking the evening AI analyst's three flags (22 Nov note):
  - "A test-bench rule leaked into regime picks": it shows (quiet day: "Library / book rule (test bench only)",
    166 trades), but nothing leaks. Regime picks are display only, and the Multiverse pick paper account takes
    test-bench signals by design. The weakness is that this pick stands on 2 days.
  - "The lcb ignores that trades cluster by day": not so in the code. The bound already uses the larger of the
    per-signal and the per-day (cluster) error, with Student's t on the number of days. But with 2-4 very uneven days
    one big day can still carry a timeline. The 5 live picks stand on 4-9 days, one green on only 4 of 8; the usual
    walk-forward's +16.88% comes from one day (19 Nov +49%); without it, -0.95%.
  - "Brain AUC under 0.5: flipped label": no. On data with a planted edge the brain gives AUC 0.65-0.73 and its top
    10% beats all signals every day. On the real 8 unseen days its AUC averages 0.48 (above 0.5 on 4): noise, no edge
    yet. It is already "on trial", information only, never a filter.
- **Strict rule** (`lab/multiverse.py` `day_lcb_table`, `strict_table`; bullet `rules/multiverse.json` "strict"):
  a timeline needs 5+ days with trades, green on 60%+ of them, and is ranked by the lower of the usual bound and a
  day-level bound where every day's average counts once (a day with 40 signals is one day, like a day with 1). Its
  best list, would-be paper picks, best by day type and its own walk-forward (with the usual picks on the same days,
  like for like) are shown on the Multiverse panel. **Shadow only:** the Multiverse pick paper account keeps the usual
  picks until Mandeep says switch.
- **Results do not move:** on an 8-day test universe the new code's usual outputs (best, luck, champion, by day type,
  live picks, walk-forward, verdict table) are identical to 2.82's; the strict picks are extra keys. Test: a timeline
  with 40 winners on one day and one loser on each of 6 other days gets a usual bound of +32% and a day-level bound of
  -28%: the usual rule picks it, the strict rule picks the steady one (green 6 of 6).
- Every pick also shows its day-level bound ("every day once"); the Multiverse pick list shows its green days.
- Fixed: the walk-forward's "main account's way" printed float32 noise (-1.309999942779541); now -1.31.
- System page: a new check (strict rule in shadow). Tests: `tests/test_2_83.py` (4).

## 2.82: the ML strategies' gradient-boosted trees run again (22 Nov 2026)
- **Mandeep (22 Nov):** "Karo" (fix the ML "hgb" error from the 22 Nov check).
- **What failed:** every nightly ML round since 6 Nov logged "ML hgb / std (swing, atr2r, be3r): window shape
  cannot be larger than input array shape" (13 nights x 4 exits on the Mac), so only logistic regression and random
  forest were tested. Cause: VIX is in the 1-year Lab bar table only from 5 Aug 2026 (72 of 529 days), so the VIX input
  is completely empty in the TRAIN part, and scikit-learn 1.9 (on the Mac: 1.9.1) refuses an all-missing column in
  gradient-boosted trees. Logistic regression and random forest already skip such a column (their imputer drops it).
- **Fix** (`lab/mlsafe.py` `KeepSeen`, used by `lab/mlstrat.py`): inputs with no value in the training rows are left out
  of the trees, and the same inputs are left out when a saved model scores live. Older scikit-learn gave such a column no
  splits, so the trees are identical to trees trained without it (checked in `tests/test_2_82.py`, bit-for-bit). Inputs
  with some values are untouched. Saved models of the other two kinds are unchanged.
- **What moves:** the next ML round tests 24 more versions (3 models instead of 2); a trees model still has to pass
  validation and holdout like any other before it can fire live (paper only). No Lab rule scoring changed.
- **Found on the way:** the VIX input carries nothing for the ML models yet (empty before Aug 2026 in TRAIN). Filling
  VIX history into the bar table would give it a chance; not part of this release.
- System page: a new check (ML trees accept an input empty in TRAIN). Tests: `tests/test_2_82.py` (4; the
  empty-VIX test fails on 2.81 with the Mac's exact error).

## 2.81: the scorecard and the 5-minute scan no longer hold up the live loop (22 Nov 2026)
- **Mandeep (22 Nov):** "go with it" (2.81 from the 22 Nov check: the analysis loop, which raises alerts and runs the
  alert tracker, test bench and strategy desk every second, stopped for 2-6 s every 5 minutes on the scorecard and for
  5-8 s at most 5-minute bar closes on the setup / Lab rules scan; 19 Nov log: "Slow analysis pass 10.0 s (Setup / Lab
  rules scan 8.0 s)"). Paper stop / target exits were not affected: they have run in their own watcher since 2.71.
- **Scorecard** (`live/record.py` `scorecard`): reads only the 7 columns it uses instead of every column. It used to
  load and sort each alert's stored facts too (38,000 rows, 39 MB on the Mac). Same rows, same order, same result:
  checked on a copy of the Mac's live.db (5,000 of 5,000 rows identical, in order) and in `tests/test_2_81.py`; the
  query took 0.08 s instead of 0.44 s on that copy. The 5-minute refresh also runs in its own short thread
  (`stats-worker`): the new scorecard replaces the old one in one step, and a refresh is never started twice.
- **5-minute setup / Lab rules scan** (`LiveEngine._scan_bg`, `ChartData.due`): on the live market it runs in its own
  short thread (`scan-worker`): the same scan of the same closed bar with the same time. The alerts it finds are queued
  and raised by the loop itself within a second, in the order found, so no alert is raised from a new thread.
  Never two scans at once; at the close the day waits for a running scan. A replay or the simulator keeps the scan
  inside the loop, step by step. Setting: `config.yaml` live `scan_in_background` (default true; false = as before).
  The off-by-default range-day paper straddle now takes the paper lock (the scan may run beside the paper watcher).
- **Replay check (19 Nov, the Mac's current Lab rules: 109 promoted + 52 five-year):** two runs with the scan inside the
  loop agree on 1,438 of about 1,470 scan alerts (a replay is not exact from run to run); the scan in its own thread
  agrees with them on 1,434 and 1,435, the same as one ordinary run with another. The few that differ are the same
  stock and setup a few minutes apart, around an alert cooldown. The scan itself took 0.6-2.9 s here.
- System page: a new check (scorecard and scan off the loop); the speed line shows the last scan and scorecard times.
- Tests: `tests/test_2_81.py` (6); full suite 538 passed (incl. test_no_clash). No Lab code changed, so the Lab
  regression is untouched. System page checked headless at desktop and phone width, light and dark.

## 2.80: the nightly Lab stays out of market hours and only prices new days (22 Nov 2026)
- **Mandeep (22 Nov):** "okay we will go with 2.80 build it", after the 22 Nov check showed the nightly Lab still
  running into market hours on 16, 18, 19 and 20 Nov, and the 5-year Lab rebuilding every stock from scratch each night
  (ICICIBANK 8 times since 7 Nov, one new day each time); its table has not finished since 10 Nov.
- **Market-hours pause** (`lab/proc.py`, `LiveEngine.lab_paused`): the nightly chain (mass search, day-type, Lab round,
  situation picker, ML strategies, the 5-year Lab), the trade journeys and the multiverse wait before starting, or are
  frozen if running, from 15 minutes before an NSE session opens to 5 minutes after it closes (special sessions too),
  then continue where they were: nothing is lost or redone. Runs started by hand never pause. A frozen job is woken and
  ended if the app quits. Settings: `config.yaml` lab `pause_in_market`, `pause_before_min`, `resume_after_min`. The
  System page shows "Nightly Lab (automatic): paused for market hours".
- **Only new days are priced** (`lab/factory.py` `_build_sym_cached`): each symbol's rows are kept in
  `lab/bars_cache/` with what they were built from (each day's data, its context, the build's code and settings).
  Each night only new days, days whose data changed and days whose context moved (e.g. the day before a new expiry)
  are priced; unchanged history files are not even read. A change to the build's code, strike / exit settings or
  cost rates prices that symbol again in full. Progress is kept per symbol, so an interrupted build continues the
  next night instead of starting over.
- **Results do not move:** on the same data the new full build equals the old code's build, and the cached build
  equals a full build after a new day and after an old day's data changes (synthetic history and a real ICICIBANK
  5-year sample from the Mac: 121 days, 5,281 rows × 190 columns, identical including column order). Real sample,
  one new day: **2.0 s instead of 16.7 s** (1 day priced, 120 kept).
- **Lab regression:** compared function by function with 2.79, only `build` and `day_table` changed (`day_table` gives
  the same table when called as before); `Book`, `score`, `validate` and everything they use are identical, so every
  saved rule (data/lab/factory.json) scores exactly as before on the same table. The 487 MB table is too big for this
  release's remote check, so the rescoring itself runs on the Mac (`python3 tools/lab_regression.py`).
- Tests: 532 passed (full suite incl. test_no_clash); System page checked headless at desktop and phone width, light
  and dark: no console errors, no sideways scroll.
- The first night after the update prices everything once (it builds the cache, like one full build); from then on it
  is the new days only. The cache takes about as much disk as the table itself (the 5-year one ~0.5 GB).
- New tests: `tests/test_lab_pause.py` (4), `tests/test_bars_cache.py` (3). System & logins has two new checks.

## 2.79: the week's bugs fixed (22 Nov 2026)
From the 22 Nov check of the Mac's log (16 to 20 Nov) and the System page. Mandeep: "go with it".
- **The Telegram bot token is never written anywhere any more.** A failed Telegram send used to put the bot's address,
  token included, into the log; from there it showed on the System page and went into the evening analyst's notes
  for the AI (16 and 17 Nov). Every log line is now masked (screen, `data/logs/app.log`, System page, AI notes), and a
  send without internet logs just "Telegram not sent: no internet". **Make a new bot token in BotFather** and enter
  it in the app: the old one has been in those places.
- **Telegram never loses a message to a stray "<".** The evening analyst's 22 Nov report was refused ("Unsupported start
  tag 0.9x"). The analyst's and the market brain's AI words are escaped now, and any message Telegram cannot read as
  HTML is sent again as plain text.
- **The daily summary never repeats an earlier day.** On 20 Nov (scanner never started) it said "0 MB recorded, 3,217
  option chains, 192 headlines, 52,687 test-bench trades": 19 Nov's counts. Now: "the live scanner did not run today".
- **"Live speed" is no longer red after the close** (it read the last session's stopped feed), so the System page is
  not "bad" every evening for nothing.
- **No internet at the start: one line and a back-off.** On 16 Nov the start failed 195 times, a full traceback every
  20 s, and sent 7 "many problems" alerts. Now it logs "No internet: the live scanner could not start" once (again at
  most every 10 minutes), retries after 1, 2, 3 ... up to 5 minutes, and says when the internet is back.
- **GIFT Nifty backs off without internet** (1, 2, 4 ... up to 10 minutes; one log line in 30 minutes at most): it
  wrote 281 lines this week.
- Nothing about alerts, scores, paper trades or the Lab changes. New tests: `tests/test_fixes_279.py` (7).

## 2.78.1: the Paper page never stays blank (17 Nov 2026)
- **Mandeep (17 Nov, 11:55):** the Paper trades page was not loading (empty screen).
- **Why:** the scanner started late that day (11:51; the Mac was asleep / offline from 10:38), so for its first ~6
  minutes it loaded chart history for 220 symbols, filled the morning's candles and ran a first analysis pass of 365 s.
  Every page request took 10-20 s in that time, and the Paper page waited for three of them one after another
  (the accounts, all accounts added up, then the trades) before drawing anything.
- **Now:** the page shows "Loading paper trades…" at once, says after 5 s that the app is busy and the page fills in by
  itself, draws the trades as soon as they arrive, and adds the accounts and the totals when they come (no longer
  waiting for them). A failed request says so and keeps trying. Nothing else changes.

## 2.78: Tanu removed; Stockway is the only app (15 Nov 2026)
- **Mandeep (15 Nov):** "after updating manu just delete the tanu ... now onwards we will only keep original stockway".
- Tanu's useful pieces are in Stockway (2.74 gap fill, 2.75 light / dark theme, 2.76 replay, 2.77 Strict track), so
  its code (`tanu/`) and `Start Tanu.command` are removed. Stockway's own code never imported Tanu, so nothing in the
  app, the alerts, the paper accounts or the Lab changes.
- On the Mac, Tanu's research ledger and measured costs are kept in `data/archive/tanu_2026-11` (experiments are kept).
  Its other files (copies of Stockway's recordings and history, caches, its own Python in `.venv-tanu`) are deleted
  only with Mandeep's OK.

## 2.77: Strict track: strict alerts, a Strict paper account, Explore vs Strict (15 Nov 2026)
- **Mandeep (15 Nov):** "kuch strict karna hai toh hum alage alerts banayenge, alag se us hisaab se trade lenge kyuki
  accountability or comparison se hi best ka pata chalega". Tanu's pass test (ADR-0005) is copied into Stockway
  (`live/strict.py`), run on Stockway's own live test bench.
- **The strict test.** Evidence: the test bench, where every signal is paper-traded in every style at the live ask / bid
  (the spread really paid) with every charge, so the costs are the real ones (Tanu's measured-spread model is not needed:
  the bench fills already are the measurement; time decay is inside the option prices). Measure: R = net after costs ÷
  money at risk. Each alert type + style is split by day into learn (first 80%) and holdout (last 20%, never used to
  choose). It passes only with 30+ learn trades averaging above 0R with t at the multiple-testing bar (Bonferroni over
  every pair with enough trades), 10+ holdout trades still above 0R, and beating the yardstick in both parts.
- **The yardstick: coin flips.** Every 5 minutes of market time a coin picks a symbol and a direction; the test bench
  trades it in every style under its own source (`upstox#coin`). Never an alert, never a paper account, never in the
  bench's own results, the multiverse or the journeys (they read `upstox` only). Until there are enough coin flips the
  yardstick is every signal of that style.
- **Strict alerts.** An alert whose type passed gets a ✅ Strict pill (and a "STRICT" line on Telegram). Nothing else
  about the alert, its score or whether it is sent changes.
- **Strict paper account** (`rules/paper_books.json` "strict"): takes an alert only when its type passed in Buy ATM (the
  style a paper account trades) and the risk check says PASS. Until something passes it stays in cash.
- **Strict track page** (menu, under Paper trades): Explore (the main account) vs the Strict account vs the coin flips
  since the first run, and the full test table with the reason each pair failed (failed ones kept). The test runs by
  itself every weekday evening after 15:40 (`rules/strict.json`), and from the page's "Run the strict test now".
- **Today's honest result** (the Mac's bench, 4 to 13 Nov: 248,157 closed trades, 798 pairs tested, t bar 4.0):
  **nothing passes yet.** Every style loses on average after costs (Buy ATM −0.08R on the learn days, −0.13R on the
  holdout). The closest: candle rectangle breakout 15m sold for the day (learn t 2.9), opening-range breakout sold for
  the day (t 2.1). So the Strict account starts in cash, and the comparison will show whether Explore's many trades or
  Strict's patience does better.
- Nothing else changes: the main account, alerts, the risk check, the other accounts and the Lab are untouched (no Lab
  code changed, so Lab results are identical). A replay keeps its coin flips apart too ("replay#coin", cleared at the
  next replay). System & logins has a "strict" check. New tests: `tests/test_strict.py` (7).

## 2.76: Replay a past day (15 Nov 2026)
- **Mandeep (15 Nov):** "the tanu's features of reply ... also add ... just take from tanu". Tanu's rehearsal idea (a
  past market day played through the whole live path) is now in Stockway, built on Stockway's own recordings.
- **Live → Replay a past day** (next to Run simulator, when the scanner is off): pick a recorded day and a speed (5×,
  10×, 20×, 30×; 10× plays a full day in about 38 minutes). The same scanner, alerts, risk check, paper trades,
  extra paper accounts, test bench and trials run on that day's real prices, on a replay clock.
- **What it plays** (`live/replay.py`): index and stock prices from the day's 1-minute candles (open, the two
  extremes, close inside each minute, so the candles it builds are the recorded ones); futures, options, VIX and Sensex
  from the minute snapshots; full option chains from the recorded chains, never one stamped later than the replay
  clock; that day's headlines as they were seen; that morning's daily history and IV history (cut before the day);
  that day's lot sizes; the previous session's closes. Contracts that expired since come back from the recorded chains.
  Nothing is fetched from a broker for the replayed day.
- **Kept apart like the simulator:** source "replay" for alerts, paper trades, grades, bench and trials (cleared when
  the next replay starts), its own recording folder (`data/replay`, never `data/recordings`), no Telegram, no world
  move alerts of today, no automatic AI notes (no AI calls spent), no model learns from it, never an order. Stopping a
  replay never pauses the live scanner, and the live scanner stops a replay by itself when the next session starts.
- **Honest limits:** one price path per minute, not every tick (an alert that needs seconds-level ticks can differ); world
  markets and FII figures shown are today's; the models in use were trained after that day. So a replay is a rehearsal
  of the system on a real day, not a fair test: the Strategy Lab's holdout stays the fair test.
- A slow computer slows the replay instead of skipping minutes: every replay minute waits (up to 8 s) for one full
  analysis pass. Days with gaps in the recording (13 Nov 09:15-09:19, the restart before 2.74) replay with those gaps.
- Checked on the real 13 Nov recording (224 symbols, 3,404 instruments): 09:15 to 10:24 in 150 s at 30×, 385 alerts and
  paper trades, all under "replay"; nothing written to the real recordings or the real paper book. New tests:
  `tests/test_replay.py`; System & logins has a "replay" check. Nothing else changes (no alert, score, paper rule or
  Lab result of the real market).

## 2.75: light and dark theme, like trading platforms (15 Nov 2026)
- **Mandeep (15 Nov):** "make light and dark theme like trading platforms have ... take from tanu".
- The sun / moon button next to the Stockway name (sidebar on a computer, top bar on a phone) switches the whole app:
  pages, tables, the option chain, and every chart (candles, grid, axes, VWAP / EMA / levels, Plotly charts). Light
  stays the default; the choice is remembered on that browser (copied from Tanu's switch).
- Every colour is now a token in `webapp/static/app.css` (light and dark sets); charts read the same tokens
  (`lwOpts()` and `plotLayout()` in `app.js`). The task panel and toasts stay dark in both themes.
- Checked: all 28 pages in light and dark at 1440 and 390 px: no sideways scroll, no page errors; the switch is
  remembered after a reload. Nothing else changes (no alert, score, paper rule or Lab result).

## 2.74: the day's gaps filled after a restart or a late start (15 Nov 2026)
- **Mandeep (15 Nov):** keep only the original Stockway and copy Tanu's useful pieces into it, starting with "the
  fill gaps from real data feature".
- **Problem:** a restart in the middle of the day started the 1-minute candles again from that minute (13 Nov began
  at 09:19), so VWAP, the opening range, EMAs and every candle-based alert saw only part of the day, and the next save
  of the day's candles replaced the morning in the recording.
- **Now** (`live/gapfill.py`, copied from Tanu's late-start backfill): when the scanner starts after the open it first
  restores today's own recording (the candles of every index and stock saved before the restart, and the last 45
  minutes of every instrument's minute history for OI / price-change alerts), then fetches Upstox's 1-minute intraday
  candles for any minutes still missing, indices first, at most 2 calls a second through the shared Upstox limiter,
  in its own thread (the start, the analysis loop and option chains never wait). A live candle is never replaced. The
  System page's no-clash checks list it; the log says how many minutes were filled.
- Nothing else changes: no alert, score, paper rule or Lab result (Lab code untouched). Tests: 5 new.

## 2.73: market situation picker: the right Lab rule for today's market (13 Nov 2026)

Mandeep, 13 Nov 2026: "why we have to select the rules ... the system should understand the market movement and choose
what is the best for making profit as per the market situation". (Rules were never hand-picked: a rule that passes the
Lab with moderate / strong evidence goes live by itself and switches off by itself after 15+ losing live trades; the
tick box is only an override. What was missing: every rule traded on every kind of day.)

- **Gun** (lab/situation.py; settings rules/situation.json): the market situation of any 5-minute bar from facts known
  at its close: the day so far (quiet / range / trend, against this symbol's typical day by that time; the day-type
  forecaster's definitions), the trade with or against the day's move, VIX (low / mid / high), expiry (expiry day / 1-2
  days / further), the opening gap, the daily trend. Each live rule's Lab trades are split by situation; on the train +
  validation days its average per situation (shrunk toward its own average by 20 trades, so a few lucky trades can't
  decide); a signal is taken when the expected result in today's situation is above zero. The holdout days check it
  (every signal vs the picked ones); then it is learned again on all days for live use. Every night after the Lab
  (Lab job "situation", reading only the columns it needs), once at the first start, or "Learn now".
- **Live**: each Strategy Lab rule alert carries the picker's verdict (take / skip, expected ₹, why); the new paper
  account **Auto pick** takes only Lab-rule signals the picker says fit today (1 lot, own money and rules). The main
  account is unchanged. Strategy Lab page: "Auto pick" panel (holdout proof, each rule's best / worst situations,
  today's situation of Nifty / Bank Nifty and each rule's verdict now). System & logins: its state.
- **First result on the Mac's Lab (honest)**: all 83 live Lab rules are ATM straddle sells (paper selling is off since
  11 Nov, so none of them paper-trades). On the holdout days (11 Jun - 9 Nov 2026) picking did **not** beat taking
  every signal: 2,276 trades ₹8.51L -> 2,047 picked ₹7.45L. These sells made money in every situation (₹ a trade:
  expiry day 803, trend day so far 568, range 458, quiet 467; weakest: gap up 363, gap down 376, daily uptrend 312).
  So the picker stays on trial; the Auto pick account waits for option-buying rules to pass the Lab. VIX could not be
  used yet (the table has it on a quarter of the bars, none in the learning days).
- Lab Book gets an optional `feats` (only the facts a job needs); the default is unchanged (Lab regression identical).
- Tests: test_2_73; full suite; Lab regression identical; headless desktop + phone clean.

## 2.72: no waiting anywhere: network off the analysis loop, history before the first scan, every pass timed (12 Nov 2026)

Mandeep, 12 Nov 2026 (13:27): "ek baar sabhi jagah puri app mein check karlo aisi delays dobara na aaye". The whole
app checked for places where prices, alerts or paper trades could wait. Paper only; market data only; the analysis
itself is unchanged (same rules, same data, only not waiting).

- **Network off the analysis loop**: the option chains of the indices (every 3-6 min) and the next-expiry chains were
  fetched inside the loop, and every newly streamed option (strike window moves, a new paper trade) waited for a REST
  quote first; a slow answer or a busy Upstox limit (it can wait minutes) held up alerts and paper exits. Now they
  run in one background worker (each job queued once); a new option streams at once and its REST quote never
  overwrites a newer live price; an expired login still stops the scanner as before.
- **History before the first scan**: the earlier days' 5-minute bars the Lab rules need (timeframe facts) were fetched
  on the first 5-minute scan of the day, symbol by symbol, inside the loop. Now fetched in a background thread right
  when the scanner starts (same data, earlier).
- **Every analysis pass timed**: a pass of 2 s or more records (and logs, at most every 5 min) its slowest parts;
  System & logins shows the pass time, 95% time, the last slow pass, the pages' refresh time and the network worker.
- **Delay warning**: prices more than 5 s behind Upstox for 30 s -> a warning on the app, in the log and on Telegram
  (once), and an all-clear when back. Slow passes (95% above 5 s) are logged with their slowest parts.
- **Pushed to the pages** (websocket /ws/events, ~0.2 s): new alerts (pop-up, sound, Live page) and paper trade
  opens / closes (Paper trades and Ghanshyam pages refresh at once; the P&L in the menu). Before: the next refresh
  (1-4 s) plus the snapshot's own time (~1.5 s). The snapshot's paper summary is worked out at most once a second.
- Already fast (checked): chart (/ws/ticks), Live page prices (/ws/prices), paper and chain quotes (/ws/quotes), the
  Upstox feed (own process, 2.71), Telegram (own thread), Dhan order book and Kite backup (websockets).
- Tests: test_2_72; full suite; Lab regression identical; headless desktop + phone clean.

## 2.71: faster live prices: the feed in its own process, fast paper exits, live quotes on the pages (12 Nov 2026)

Mandeep, 12 Nov 2026 (12:44): "paper trading is not fetching the options price as the real movement ... the data coming
from all the sources should be as fast as possible. Use websockets where it can be." Paper only; market data only.

- **Why it was slow**: the app's log showed the Upstox feed cut every 10-15 minutes in the morning (09:23, 09:33,
  09:48, 10:02 "Connection reset by peer"; at 09:33 "449 of 449 ... no price update for minutes"; 47 feed errors in
  the log). The feed's reader shared one Python process with the whole analysis; when the analysis was busy the
  reader fell behind, Upstox's buffer filled, the server cut the connection and prices froze until it reconnected.
- **Upstox feed in its own process** (live/feedproc.py, gun): the websocket, pings, reading, decoding and reconnects run
  in a separate process that does nothing else; the app gets ready ticks through a pipe. Restarted (and
  re-subscribed) if it dies; the old in-process reader takes over if it cannot run. Setting `live.feed_reader`
  ("process", or "thread" for the old way). Python also lets the price threads in every 1 ms instead of 5
  (`live.gil_switch_ms`).
- **Fast paper exits**: every account's open trades are checked against their stop / target / trail every 0.25 s
  (`live.paper_watch_ms`), not once per full analysis pass (which can take seconds); only changed peaks are written;
  a trade is never closed twice (lock).
- **Live quotes to the pages** (websocket /ws/quotes, ~0.2 s): the Paper trades page's "Now" and P&L move live in place;
  the option chain's streamed strikes show their live price (dotted underline; "N prices live"); the chain view puts
  the streamed price, bid / ask, OI and volume on top of its 20-second snapshot.
- **System & logins**: "Live speed": feed reader mode, pipe delay, Upstox data delay (red above 3 s), paper check time.
- Tests: test_2_71 (a local Upstox-like websocket server: ticks, re-subscribe, restart after the process dies,
  fallback); full suite; Lab regression identical; headless desktop + phone clean.

## 2.70.1: a stock's seconds chart starts at once (12 Nov 2026)

Mandeep, 12 Nov 2026 (KALYANKJIL 15s chart stayed on "No seconds prices yet"): per-second prices were kept for the
indices only, and a stock's seconds chart never opened its live stream while it was empty, so nothing started them.
Now opening a 5s / 15s / 30s chart starts that symbol's per-second prices at once (the current price is the first
candle); the next try (6 s) draws the chart and the stream keeps it live. Seconds history still starts when the chart
is first opened that day. Tests: test_2_70_1; full suite; Lab regression identical.

## 2.70: MindGame (buyer / seller scene on every timeframe), 4H and 1W charts (12 Nov 2026)

Mandeep, 12 Nov 2026 (02:50): show the buyer / seller scene on 1m / 3m / 5m on the main chart, and on the charts below
5m / 15m / 1H / 4H (replaces 75m everywhere) / 1D / 1W (long moves, strongest resistance, all-time high), each laid out
like the main chart with only its own timeframe's messages on the right. On trial: no alert, no paper trade.

- **MindGame** (live/mindgame.py, gun; rules/mindgame.json, 14 bullets from the Ghanshyam course): every behaviour is
  ACTIVITY, measured in the symbol's own typical candle on that timeframe (no fixed points, his rule): a straight run
  into a level (trap risk), a rest at the level then a strong break (real breakout), lows / highs holding, a tight
  range of small candles and the break that traps the other side, a slow weak move (someone absorbing), a side
  leaving, a confirmed hammer / star (only after a strong move), a retest, the gap, the OI strike, yesterday's close.
  Several at once are combined (bigger timeframes weigh more) into one short message: AI (fast tier, its own 60 a
  day, only when the scene changes) or the system's own lines when AI is not there. A failing check is skipped and
  named; the rest keep running.
- **Record**: every behaviour that newly shows on a closed candle is written down (live.db `mind_reads`) and graded a
  few candles later: right = a typical candle its way first, wrong = against first; a coin flip scores ~50%. The
  engine records Nifty / Bank Nifty 1m / 3m / 5m / 15m every minute in its own thread; 1H and up when the charts are
  open. Alerts only once a bullet's record is positive (Mandeep); none is on.
- **5-year check** (Bank Nifty Aug 2021 - Oct 2026, minute prices; right % of decided, coin flip 49.9% / 49.0%):
  better than a coin flip: gap booking 56% (5m, 123) / 57% (15m, 85), straight run = trap 53% (5m, 2,126), range
  break 58% on 15m (264). Worse: "most of the fall taken back -> reaches the high" 40% / 37% (the opposite happens),
  above / below yesterday's close 42% / 47%, range break on 5m 43%, rest-then-break 45% / 47%. Shown beside each
  behaviour; the weak ones weigh less in the combined reading. All kept on trial (failed ones recorded, not deleted).
- **Charts below the main chart**: 5m, 15m, 1H, 4H (09:15-13:15, 13:15-15:30), 1D, 1W; each full width with its scene,
  trend, support / resistance on the right (stacked on a phone); the weekly adds the all-time high, 52-week high / low
  and the strongest resistance / support (the band most weekly highs / lows of 3 years stopped at). 75m kept only for
  old links and the candle scanner's running trial (hidden on screen; its records kept); the candle scanner adds 4H.
- Tests: test_2_70 (18), full suite + test_no_clash; Lab regression identical; headless desktop + phone clean.

## 2.69: Ghanshyam section, paper totals and exits, seconds chart, faster Live page (12 Nov 2026)

Mandeep, 12 Nov 2026 (01:40). All paper only; broker APIs for market data only.

- **Ghanshyam section** (new page, under Trade today): his own paper account (rules/paper_books.json `ghanshyam`) that
  takes only his strategy-file signals on Nifty and Bank Nifty (1 lot, once per strategy, symbol and day, the main
  account's paper rules); a strategy accountability table (Lab 5-year / 1-year, test bench live, the account, one
  honest line each); every signal as a list with an expand button; a one-click "Load Ghanshyam's strategies".
- **One Ghanshyam file** (`rules/strategy_files/ghanshyam_course.json` v2, 25 strategies, 90 Lab rules): the 9:20
  interview file merged in, keeping the versions that did best in the 5-year Lab (920-E: yesterday's high / low taken
  out in the first candle with the trend, +₹104 / +430 / +843 per trade) plus the course's big-gap rule and the plain
  break as control; 920-A to D left out (they lost; recorded in the notes). Course fixes: Fibonacci only after a 1%+
  fall (5-year check: swing exit +₹643 / +20 / +917 vs negative at 0.5%); 2:20 "middle" = the 5 candles before it, as
  he said (standard exit +₹128 / +21 / +454, all three periods positive; the 13:00-14:15 version kept as data
  version); breakouts need 3+ touches, a wait and no long run (new facts; the check says this loses: kept on trial).
  Nothing passes the Lab bar yet.
- **Gun**: `lv_brk`, `lv_touches`, `lv_pause`, `lv_run`, `pm_edge5` (lab/pafacts.py); strategy-file signals reach
  paper accounts that ask for them (`pack_prefix`), tagged with the strategy (`mode = pack:<id>`); `packs.bench_variant`
  shared by the live scan and the page (same ids as before).
- **Paper trades**: every account added up on every tab (net, today, trades, open); Exit and Exit all in every
  account (were missing outside the main account).
- **Fortune chart**: the forming candle shows its start and close time (e.g. 9:20→9:25).
- **Main chart**: 5s / 15s / 30s candles from the app's own prices (today, from when the scanner started;
  live/secbars.py). **Live page**: prices streamed over their own websocket (~0.2 s) like the chart.
- Tests: 460+ (incl. test_no_clash); Lab regression identical; headless desktop + phone clean.

## 2.68.1: a held-back Lab rule is still measured (11 Nov 2026)

Mandeep asked whether one strategy can stop another from being measured. Checked on the live data (30 Oct - 11 Nov):
chart facts are computed once and every strategy checks its own rule; Focus and the main paper account pick one per
symbol by design, and the test bench records every strategy separately. One gap: when two different Strategy Lab
rules fired the same side on the same symbol inside the feed's 15-minute cooldown, the second was dropped before the
test bench. Now it is recorded on the test bench under its own rule id (nothing you see or trade changes).
448 tests, Lab regression 103 / 103 × 2 identical.

## Ghanshyam option-buying course: gun pieces + strategy file (2.68, 11 Nov 2026)

Mandeep shared the course transcript (72 pages, 2022). Split first (gun vs bullet), checked on Bank Nifty 2022-2026
before building (index 1-minute data, then the 5-year Lab's real option prices with every charge):

- His favourite **hammer** did not hold up (31-33% win, negative in both halves); his "90%" is not supported.
- **Shooting star after a rise on a gap-down day**: the best candle lead (51% win, small sample, not proven).
- **2:20 candle break WITH the morning move**: lost money in every period (about -Rs 300 to -670 per trade): an
  "avoid" check, kept on trial as a control. **AGAINST the morning move, away from the middle of the range**: the
  best lead (calls +Rs 83 / -97 / +514 per trade train / validation / holdout), not proven.
- **Fibonacci 0.71** after a fall: only after a big fall (0.8%+), mixed. Window strategy (1-minute), breakout "with a
  pause" and the round-figure rule: no edge found.

Gun (`lab/pafacts.py`, in every bar table and live through the same facts): `open_fall` / `open_rise`, `hs_conf`
(hammer / shooting star at the day's extreme confirmed by a close beyond it within 3 bars), `am_move` (open to 13:00),
`pm_rng` / `pm_edge` (where the 14:15-14:20 candle sits in the 13:00-14:15 range), `fib_dday` / `fall_day` and
`fib_uday` / `rise_day` (how much of today's fall / rise is taken back), and three level stops for strategy-file
exits: `pm` (the 2:20 candle), `fib38` (the 0.38 retracement), `sig` (beyond the confirmed hammer / star). Each is
isolated: a failure leaves only its own facts empty.
Bullets: `rules/strategy_files/ghanshyam_course.json` (17 strategies, 67 Lab rules, upload on Strategy Lab →
Strategy files). Everything starts on trial (test bench only). The bar tables rebuild once for the new facts.
Lab regression: 103 / 103 and 103 / 103 (1-year), 1,444 / 1,444 (5-year) identical.

## 2.67.1: sidebar order (11 Nov 2026)

Mandeep: the sidebar now reads Trade today, Market intel, Daily radar, Test & research, Setup. Same pages, new order.

## Daily Radar for all indices + grand view (2.67)

The Daily Radar (next session's big-move chance) now covers **Bank Nifty, Nifty 50, Fin Nifty, Nifty Next 50 and
Sensex** (free Yahoo daily prices since 2015). `radar/indices.py` runs the same radar engine for each index in its own
folders (`models/indices/<key>`, `output/indices/<key>`), with its own expiry calendar (dated data in
`rules/radar_indices.json`) and the same honest test: each year forecast by a model trained only on earlier years.

- **All indices** page (Daily radar → All indices): every index's next-session call, big-move chance against its flag
  level, expected move, unseen-year score, live record, and how many indices flag together ("broad big move likely"
  when 60%+ of them flag). An index picker on Tomorrow, History, Biggest days, What drives moves and Model check.
- The scheduled daily forecast and the Saturday retrain now run Bank Nifty first (unchanged), then the other indices in
  a low-priority background process; one Telegram message lists every index. A newly listed index trains by itself
  once (after 5 PM or at the weekend), or from the All indices page.
- A failing index (no data, broken prices) is skipped and marked; the others keep running. Midcap Select and Bankex
  are listed but off: Yahoo has no daily history for them.
- Bank Nifty's radar is unchanged: trained and forecast on the same demo data before and after this change, every
  output file (predictions, metrics, biggest days, factors, importance, backtest) and the forecast were identical.
- Information only: nothing trades on it.

## 2.66.1: Mac lag fixes (11 Nov 2026)

- Live metrics read the per-instrument tick and bar lists while the feed thread adds to them: "Metrics failed ...
  deque mutated during iteration" 15 times on 11 Nov (that stock's numbers skipped for that minute). They now read a
  copy. Same numbers.
- Evening Lab jobs (journeys, multiverse, mass search, Lab rounds, day type) run in macOS background mode as well as
  nice 10: efficiency cores, throttled disk. Last night's round ran 16:15 to 01:05; the Mac stays usable while it
  runs. Same results, slower only when the Mac is busy.

## Day-type forecaster: trend, range or quiet day, from the 09:45 facts (2.66)

`lab/daytype.py` learns from the Lab's bar tables (1-year and 5-year) how the first 30 minutes look on days that end
as a **trend day** (moved cleanly, wider than a usual day), a **range day** or a **quiet day** (under 60% of a usual
day's range). Facts used (rules/daytype.json): range so far vs a usual day, how cleanly it has moved so far, gap, VIX,
days to expiry, opening-range position, distance from yesterday's high / low, CPR width, ADX. Every night each
candidate model (logistic regression, small gradient-boosted trees) is tested walk-forward by month (a month is
forecast only by a model trained on the months before) against the usual shares; the best one is the live model.

First test (copies of the Lab tables, 11 Nov 2026): 1-year Lab (Nifty + Bank Nifty, 528 unseen days) 7.3% better than
the usual shares, the 20% most trend-like mornings trended 36% (all days 31%), the 20% most quiet-looking were quiet 29%
(all days 11%); 5-year Bank Nifty (631 unseen days) 8.1% better, trend 48% vs 31%, quiet 37% vs 14%. On trend days the
move by 09:45 already pointed the day's way 78-83% of the time. It does **not** beat "always say range" as a yes/no
call (range days are 55% of days): read it as odds, not a verdict.

Live: at 09:45 each index gets its odds (Live page under "What works", and the Strategy board), graded at the close
(data/daytype_live.json). On trial until the walk-forward clears the trust rule and 20+ live days agree. Information
only: nothing trades on it.

Privacy: from 2.66 every file the app writes under `data/` (models, Lab results, AI reports, paper records) is kept out
of git, not only the token and database files.

## Evening AI analyst (2.65)

After the close (after the trade journeys and the multiverse), `live/analyst.py` collects the day on one page (paper
accounts, alerts by type, the multiverse's best / walk-forward / brain, the mass search, the news radar's scores, the
day's error lines from the app log, failed self-checks) and asks the deep AI tier (Opus) for: what worked, what failed,
what looks broken (with the log line or number), up to 5 new filters to test, and one line for tomorrow. Its filters
go to data/multiverse_ideas.json and the next multiverse run tests them on days they never saw (on trial; nothing
trades them by being proposed). Report: Strategy board → Evening AI analyst (Run now too), data/analyst/<day>.json and
Telegram when set. Setting `ai.analyst` (on). Only the app's own numbers and log lines are sent.

## News radar: the fast AI reads every headline (2.64)

Until now a headline's direction came only from keywords ("rise" good, "fall" bad) and its stocks only from name
matching. `live/newsradar.py`: new headlines (RSS, Dhan, official feeds) go in batches of 25 every 2 minutes in market
hours to the fast AI tier (Haiku 5.5): impact 0-3, direction for the stocks it names (or the market), the NSE symbols
it is clearly about, a few words why. Each call is graded 30 minutes later on its stock's price (or Nifty), next to the
keyword tone: Market context → News radar shows both scores, high-impact accuracy, and stocks the AI found that name
matching missed. Shadow: news alerts stay keyword-based until the AI is clearly better, on Mandeep's say-so. Only
public headlines and symbol names are sent. Setting `ai.news_radar` (on). Fast-tier allowance now 250 a day.

## Multiverse brain: a learned model on every signal, judged on days it never saw (2.63)

Mandeep, 11 Nov 2026: "AI kaha kaha ... zada smart aur accurate bana sakta hai, kya kya cheej bhot jaldi pakad sakta hai".

- `lab/mvbrain.py`: gradient-boosted trees on every test-bench signal's facts (the same ones live: the stock, options,
  index, breadth, sector, world, VIX, news, order book, movers, bigger picture, alert type, day type), learning the net %
  under one fixed way of trading it (`rules/multiverse.json` brain.exit: ATM, at once, +30% / −20% / 60 min). Trained in
  each multiverse run; milliseconds per live signal.
- Walk-forward: the model for each day is trained only on the days before it. First run (Mac data): on 9 Nov, trained on
  5-6 Nov, its top 10% made +1.3% a trade vs −3.1% for all signals. One unseen day: on trial.
- Every alert shows "🧠 Brain: expects +x% on this one (better than y% of signals) · on trial"; the Engine eval panel and
  the Multiverse panel show it too. **Trusted** only after its top 10% beat all signals on 70% of 5+ unseen days; until
  then it is information only and decides nothing.

## AI router: the right Claude model for each job (2.62)

Mandeep, 11 Nov 2026: "model bhi vo choose karo jo sabse zada capable ho or fit ho or bina bhot zada time liye near to
accuracy analysis kar sake". Before: all 11 AI jobs used one model (Claude Code's default on the Pro plan, Opus 5.5).

- `rules/ai_models.json`: **fast** = Haiku 5.5, effort low (market pulse, world note, news) with its own daily allowance
  of 150, apart from the automatic-notes cap; **balanced** = Sonnet 5.5, effort medium (alert "Why?", market brain,
  India indication, briefs, Ask Stockway, AI chart read); **deep** = Opus 5.5, effort high (research reviews, Lab ideas,
  ebook reading, the evening analyst). Fable (Claude Code's most capable model) can be set for the deep tier; on some
  plans it bills usage credits, so it is not the default.
- A model your plan refuses is skipped for the day and the next one is used (opus → sonnet → haiku → Claude Code's
  default). An older Claude Code without `--effort` still works. Live setup → AI → "Pick the model per job" (on;
  off = your one model for everything, as before). System page: which model each job used today and how long it took.

## Engine eval: the multiverse's verdict on every alert, and on the chart (2.61)

Mandeep, 11 Nov 2026: "sab kuch dekh kar best evaluation line bhi ban rahi hai" (like a chess engine's evaluation).

- Every multiverse run now saves a verdict table: for each filter (alert type × fact), its best way to trade it (strike,
  entry time, exit) and its record on the recorded days (`lab/multiverse.py` `verdict`).
- **Every directional alert** carries the verdict for signals like it (the same facts the test bench records):
  "🌌 Multiverse (3 days): Volume surge signals, with Nifty: +4.2% a trade × 92, lower bound +1.1% · proven · best way:
  buy 1 OTM at once, exit +30% / −20%, 60 min · all Volume surge signals: +1.2% × 410". Also in Telegram.
- **Chart → Engine eval** (above Smart lines): the symbol's last 45 minutes of signals with their verdicts, a bar that
  leans to calls or puts only when that side's lower bound is above zero, and the best way to trade it
  (`/api/live/eval/{sym}`). Information only: it changes no alert, Focus, risk check or paper rule.

## Mass search: every fact alone and paired, all at once, on the Lab's years of data (2.60)

Mandeep, 11 Nov 2026: "1000 magzine ... saari mag khali karke best bullet ka pata laga le" and "prove you best".

- **Gun** (`lab/mass.py`): the Lab's bar table (every 5-minute bar of the index option history, its facts, the result
  of buying the ATM call / put under each exit, after charges) laid out as one block per day × index × direction with
  64 bars. A rule fires on its first matching bar in its window (the Lab's 1 trade a day), so a whole rule is one AND
  and one argmax: every fact at 3 thresholds above / below (singles) and the best 60 singles paired with every other,
  × 4 time windows × each direction and index × each exit. On the old 2-year Nifty / Bank Nifty copy: **879,120 rules
  in 44 seconds** (the Lab's search tries about 9,000 a night).
- **The judge:** the search sees only the Lab's train days, ranks by the lower confidence bound; month-by-month
  walk-forward on the later months (each month's pick from the months before it), next to buying every day with the
  same exit; the luck line from the same search with each train day's prices swapped with another day's. First run on
  that copy: best train lower bound ₹468 a trade vs luck ₹255; walk-forward 11 months, 43 trades, +₹363 a trade
  (+₹15,591) where buying every day lost ₹76,868 over 304 trades; most of it from one month (March 2026): a lead.
- The best 40 go to the Lab as ideas (source "mass search") and face the usual train / validation / holdout exams and
  promotion bar: nothing goes live by the mass search alone. Runs before each nightly Lab round on the 1-year and
  5-year Labs (`lab/proc.py` job `mass`), or Strategy Lab → Mass search → Run now. **Bullets:** `rules/mass.json`.
  System check `mass_search`.

## "Signals agree" alert and the "Index slot" paper account (2.59)

Mandeep, 11 Nov 2026 (Bank Nifty breakout: new day high 10:29, flag breakout + heavy 60900 PE writing 10:30, no pop-up,
the main account bought only at 10:42 because its 3 slots were full of stock trades): "jo best ho vo karo".

- **"Signals agree"** (`rules/confluence.json`, kind `confluence`): when 3 different alert types point the same way on
  the same symbol within 3 minutes, one alert names them, with a pop-up, a sound and Telegram (when set), whatever its
  strength. It does not change Focus, the risk check or any paper account (the main account doesn't trade this type);
  the test bench records it, so the multiverse grades it before anything trades it. System check `confluence`.
- **"Index slot" paper account** (`rules/paper_books.json` → `idxslot`): the main account's alert types and strength,
  but its last open-trade slot is never filled by a stock (`paper.index_slots: 1`; 0 = as before, the main account
  is unchanged). Accounts side by side shows whether keeping a slot for the indices pays; switching the main account
  is Mandeep's call.

## Multiverse: every signal down every timeline at once, judged honestly (2.58)

Mandeep, 11 Nov 2026: "saari possibilities simultaneously test karke check karke best path as fastest as we can ...
100 magzine, 1000 magzine ... gun itni smart ho ki saari mag khali karke best bullet ka pata laga le".

- **Gun** (`lab/multiverse.py`): every test-bench signal of the recorded days (data/bench.db + the day's minute
  recording) is replayed on its real option prices, minute by minute to 15:15, sold at the bid, every charge, net % of
  the premium. Timelines = filters (alert type × one fact: with / against Nifty, time of day, volume, IV, VWAP, trends,
  order flow, breadth, day type …) × strike (ATM, 1 OTM, 1 ITM) × entry time (at once, 2 or 5 min later at the ask
  then) × exit (target × stop × time, trailing). All exits come from one pass of array maths and every filter × exit
  total from one matrix product per day: **7.2 million timelines in about 20 seconds** on 3 days. The same move seen
  by several alert types or timeframes in the same minute counts once.
- **The judge:** ranked by the lower confidence bound, never the raw average; days (not signals) are the proof (the
  per-day error with Student's t for the number of days, so 2 days need 12.7 standard errors). Walk-forward: each day's
  pick is chosen only from the days before it, then scored on that day. The luck line: the same search on shuffled
  data (facts no longer tied to results). Next to it, the main account's way (its alert types and exit).
- **First run** (3 recorded days, 5, 6 and 9 Nov; 13,412 signals; 7,158,888 timelines): best lower bound +27.1% vs
  luck line +5.2%. Walk-forward on 9 Nov (chosen from 5-6 Nov only): the pick +2.9% a trade (39 trades), its top 5
  together +1.9% (195), the main account's way −2.9% (679). One unseen day: a lead, not proof. All 3 days were rising
  days; trend-down days have no data yet.
- **"Multiverse pick" paper account** (`rules/paper_books.json` → `mvpick`): takes a live test-bench signal only when
  it matches one of the live picks (lower bound above zero, entry at once, ATM or 1 OTM), with that pick's strike and
  exit, 1 lot, its own money. Nothing proven = no trades. The main account, alerts and the bench are untouched (the
  bench is called exactly as before; `_bench_add` only offers the signal to this account afterwards).
- Runs after the close (after the trade journeys) in the Lab's low-priority process (`lab/proc.py` job `multiverse`),
  or Strategy board → Multiverse → Run now. Results in data/multiverse.json. **Bullets:** `rules/multiverse.json`
  (filters, strikes, entry times, exit grid, the proof thresholds); a broken filter is skipped and listed. System check
  `multiverse`.

## Strike choice: the Lab can buy out-of-the-money strikes, alerts and paper buy the rule's strike (2.57)

Mandeep, 11 Nov 2026: "go ahead" on choosing strikes by premium (the Lab could only buy at the money).

- **Gun:** a Lab rule may name a strike (`rules/strikes.json`): `otm1` / `otm2` (strikes away from ATM on the
  out-of-the-money side) or `lowprem` (the out-of-the-money strike priced nearest 0.15% of the index, about ₹37 on
  Nifty 25,000). The bar table prices the same buy on those strikes for 3 exits (std, trail, chand) on the indices
  (32-bit columns, so the Mac's 1.1-million / 2.7-million-row tables stay small); the table is rebuilt once when
  they are missing. Scoring reads `exit@strike`; rule ids, words ("Buy the call 2 strikes out of the money …"),
  validation, the random search (tries a strike on 40% of buy rules) and mutations (switch strike / back to ATM) know
  it; strategy files may set `"strike"`. Live: a promoted rule's alert suggests that strike (`_suggest`), so the paper
  trade buys it. **A rule without a strike buys ATM exactly as before: old ids and results unchanged.**
- **First run** (the Mac's 1-year data copy, 8,302 rules, train / validation / holdout): no buy rule passed all three
  exams on any strike (1 straddle-selling rule did; luck alone ≈ 15). Among rules that passed train, the 2-OTM versions
  did best on validation (24 of 32 positive vs 28 of 58 at the money); 1 in the money did worst (0 of 9), so it is not
  priced for now. In ₹ per lot the further out-of-the-money options lose less per random trade (cheaper lots): a lead
  to keep testing, not an edge. System check `strike_choice`.
- A saved bar table without the new columns stays usable when there is no option history to rebuild from (tests,
  a fresh install): rules that need a strike column just can't be scored there.

## Live QA fixes (2.56.2)

Mandeep, 11 Nov 2026 (market open): "as a highly professional QA test the app fix the bugs". Checked page by page on the
Mac during the market. Nothing here changes an alert, a score, a paper rule or the Lab (Lab regression: identical).

- **Dhan news and market movers** said only "too many retries". Now the error says Dhan's answer (HTTP 429 = too many
  calls, 5xx = Dhan's server), a live call tries 2 times instead of 5 (a busy Dhan stalled the news loop up to 45 s),
  and a busy endpoint rests 2 min, doubling to 15 min, instead of being asked every minute. One busy movers list makes
  the others wait too. The 5-year download keeps its own 5 tries (`lab/dhan.py` `tries`).
- **Charts that took 45-100 s** to open a stock the first time: a chart's Dhan client now gives up after 2 tries and
  fills in from saved bars; an older-history fetch that failed is tried again after 10 min (it was never tried again
  that session).
- **Nifty Midcap Select "52 weeks"** showed only today's range: Yahoo has one monthly bar for that index. Less than a
  year of Yahoo history now falls back to the chart's saved daily bars (Dhan); no all-time high is claimed from under
  3 years of bars.
- **A slow page could draw over the next one** (Paper's data arrived after you opened Scanner and was drawn on top of it).
  Paper, Scanner, World, Watchlist, Strategy desk and System now drop data that arrives after you moved on.
- **The live snapshot was asked twice a second** (two loops side by side). Now one at a time: half the load on the app.
- **"Trailing exit" account took different trades from the main account** (main was full with 3 open trades, and in the
  first minutes after the scanner started there were no 5-minute bars), so the two could not be compared. Now
  `pick.mirror_main`: it takes exactly the trades the main account takes, with the same lots; the main account's limits
  decide. Only the exit differs, as 2.56 meant.
- **The conviction model was judged on a holiday.** On 10 Nov (NSE holiday) the scanner of that time (before 2.48's
  calendar) ran on Friday's frozen prices: 231 alerts, aggressive paper trades closed by hand, and 224 "graded" alerts
  with no move, which became the model's whole holdout (base rate 0, AUC 0.5: no verdict possible). Only NSE session days
  teach or judge the model now. Since 2.48 the scanner does not run on holidays. The trades stay in the record.
- Checked and fine: Alerts, Chart (smart lines: 8 lines, no sideways scroll), Option chain, Scanner, System; the
  "Range" day type on Nifty (opened at 25,338, still 25,361 after an hour: a gap-up then sideways is a range day).

## Trailing-exit paper account; the Daily Radar checked (2.56)

Mandeep, 11 Nov 2026: "go ahead" on: selling stays off (focus on buying), the trailing exit as a separate paper account
first, then strikes by premium / entries after 14:45 / per-strike OI. And: "how relevant is this section now ... check
if it is not affecting the system smartness and accuracy" (Daily Radar).

- **Selling** stays off (2.55 defaults): no paper straddles, one range-day signal per index a day below Focus.
- **"Trailing exit" paper account** (`rules/paper_books.json` → `trail`): takes the **same alerts as the main account**
  (`pick.same_as_main`: its alert types and strength, risk check PASS, lots from its own money) and differs only in the
  exit (`exit_style: "chand"` from `lab/exits.py`): stop 1.5 × 5-minute ATR, then a stop trailing 2.5 × ATR behind the
  best price, no fixed target, out by the day's exit time. Why: on 15,370 shadow-graded alerts it made +2.0% more on
  the option per trade than the +30% / −20% premium exit, better on 5 of 6 days. Paper page → its own tab; Accounts
  side by side compares it with the main account. Switching the main account stays Mandeep's call. Gun: books can give
  an account its own chart exit (`exit_style`, checked against the exit registry; a bad one marks only that account);
  the alert now carries the chart risk per exit style (`plan_R`).
- **Daily Radar** (Tomorrow / History / Biggest days / What drives moves / Model check; Bank Nifty next-day model):
  - It does **not** feed alerts, Focus, the risk check, paper trades or the Lab (only its own pages and the 18:30
    forecast), so it cannot lower their accuracy.
  - Its record (Mac, 1,936 test days): big-range days AUC 0.70 (0.61–0.80 every year); flagged days were big-range days
    35% of the time vs 20% normally (1.8×), catching 56% of them. Direction AUC 0.54: a coin flip. Buying a straddle
    every flagged day still lost after costs (−0.53% of spot a trade).
  - Fixed: the forecast date skips NSE holidays (on 10 Nov, a holiday, it forecast "for" 10 Nov). The Tomorrow page says
    how to use it (pick how to trade on flagged vs normal days, not a trade by itself).
  - Its call for the day is now recorded with every test-bench signal (`radar_big`, `radar_flag`) so the credibility
    check can show, after some days, whether trades on flagged days do better. Recorded only.

## No more naked straddle selling: one range-day sell signal, hedged versions tested first (2.55)

Mandeep, 11 Nov 2026: sell only in sideways markets and risk-controlled; "i also checked system selling call and put at
same time i think it's not hedging"; then "yes go for it".

- What was happening: 83 promoted Strategy Lab rules were live on his Mac and every one was "sell the ATM straddle"
  (near-copies of one idea: different time windows and stops). Each fired on its own, paper-sold the ATM call and put
  with no wings bought, and raised an "act now" alert (in Focus, so on Telegram). 11 such pairs, net −₹5,052; on 9 Nov
  (expiry) 6 straddles were open on Nifty at once.
- Now (`rules/range_sell.json`): the first of those rules to fire on an index raises **one "Range-day sell" signal per
  index per day** (the others count as agreeing), marked with the day's regime (range / quiet = fits, trend = skip),
  severity 2 so it stays out of Focus and Telegram, recorded under All alerts. **No paper trade**: `paper: "off"`
  (the old naked behaviour only with `"naked"`, and then only on range days). System check `range_sell`.
- Hedged challengers (`tools/hedge_study.py`, the Mac's 1-year Lab data, the 109 promoted straddle rules, 2,247
  holdout trades, ₹ per trade after costs, train / validation / holdout):

  | Exit | Train | Validation | Holdout | Rules positive in all 3 |
  |---|---|---|---|---|
  | Naked, 25% stop per leg (today's) | +469 | +545 | +283 | 66 / 109 |
  | + wings bought 10 strikes away | +35 | +444 | +13 | 22 / 109 |
  | + wings 8 strikes away | −31 | +468 | −39 | 18 / 109 |
  | + wings 4 strikes away | −94 | +447 | −152 | 14 / 109 |
  | Iron fly, wings 4 / 8 / 10, out at 30–150% of the credit | −140 to −287 | −521 to −659 | −88 to −293 | 0 / 109 |

  The wings cost about what the straddle earns in a day: no hedged version beats the naked one, so selling stays off
  paper until Mandeep decides (naked with leg stops on range days only, or no selling).

## Smart chart lines: the app draws the lines that matter, each with its record (2.54)

Mandeep, 11 Nov 2026: "check whether we are marking and analysing good support, resistance, target and stop loss and
what can be done better and also make some smart system that can draw lines on the chart by itself analysing each and
everything that matters".

- **What the check found** (4.8 years of Nifty + Bank Nifty 1-minute, 49,950 line touches; your Mac's live data):
  - Plain lines (yesterday's high / low / close, pivots, R1 / S1, opening range, round numbers, today's swings) **held
    about as often as random lines** (~50% vs 50.9%). How price comes to a line decides more: slow approaches held
    56-58%, fast ones broke 56-59%; yesterday's low, the 20-day low and the opening-range low touched on a down day
    broke 58-63% of the time.
  - Alert stop / target (21,338 alerts): 22% had the target nearer than the stop; within 60 minutes the first target
    was reached 19% of the time, the stop 31%. On 15,370 graded alerts a trailing exit (no fixed target) beat the
    +30% / −20% premium exit by +2.0% option per trade, on 5 of 6 days (shadow exits; paper exits unchanged).
- **The line book** (`lab/levelbook.py`, shipped as `rules/level_book.json`, rebuilt on your Mac from its own
  1-minute history every 7 days outside market hours, or `POST /api/lines/rebuild`): for each kind of line and each
  situation (how fast price came, with or against the day's move) how often it held or broke within 60 minutes, next to
  random lines in the same situation, older years (train, to 2024) and newer (holdout, 2025-26) apart.
- **The line finder** (`live/linesmith.py`): yesterday's lines, CPR and pivots, 5-day and 20-day highs / lows, today's
  open, round numbers, opening range, today's 5-minute swings, call / put OI walls, max pain, gamma flip, and diagonal
  trendlines through 5-minute swings of the last sessions (never broken by a 5-minute close). Lines closer than 0.08%
  become one zone. It keeps the nearest lines on each side plus farther ones that beat random lines.
- **On the chart** (Chart → Indicators & levels → "Smart lines", on by default; the old level layers are still there):
  colour = what the line usually does now (blue holds, orange breaks, grey 50/50, purple not tested yet), ★ = the line
  itself beats random lines in this situation (older and newer years agree), "(weaker than a random line)" when it
  does worse. The Smart lines panel says, for each line, the record in words and what it means for a buyer (break →
  calls / puts on a 5-minute close beyond it, stop back inside; hold → only after a rejection candle; 50/50 → no edge).
- **Live grading** (`live/linegrade.py`, shadow): every first touch of every line on Nifty / Bank Nifty is recorded with
  the situation and the book's call, then graded hold / break in 60 minutes; the panel shows the live record. OI walls,
  max pain, gamma flip and trendlines have no history yet, so they start "not tested yet" and build their record live.
- Bullets in `rules/lines.json` (touch distances, buckets, thresholds, how many lines, trendline settings, names).
  System check `smart_lines`. Display and shadow grading only: alerts, paper trades and the Lab are unchanged.

## Strategy files open and close (2.53)

Mandeep, 10 Nov 2026: "there is tab should also be expandable and contractable".

- Strategy Lab → Strategy files: each file is a card that is **closed by default**: its name, source and a one-line
  summary (strategies, how many can't be tested yet, how many passed all 3 in the 1-year and 5-year Lab). Click the
  card (or Tab + Enter) to open its strategy table; click again to close. The "On" tick does not open or close it.
- **Open all / Close all** above the cards. Which files you left open is remembered on that browser; a file you just
  uploaded opens by itself. Display only: nothing in the Lab, the test bench or the results changes.

## Kite (Zerodha) next to Upstox: a backup feed and a price cross-check (2.52)

Mandeep, 10 Nov 2026: "what if we can add kite to our platform ... using kite, dhan and upstox with their best potential",
then "okay let's do it" (step 1 of 3: the feed router; next: more strikes through Kite, then Kite's long history in the Lab).

- **Market data only.** `live/kite.py` reads Kite Connect's live price websocket (and its daily instrument list); it
  sends only subscribe / mode messages. No order, GTT, holdings, positions or funds call exists in the code
  (`tests/test_kite_feeds.py` checks this, and that Kite is reached from `live/kite.py` only).
- **The feed router** (`live/feeds.py`) sits between the live feeds and the app. Upstox stays the main feed. Kite gets the
  same instruments (matched by NSE exchange token; indices by name, `rules/feeds.json` → `index_map`; up to 2,900).
  - **Cross-check** every 5 s: each price seen by both feeds within 2 s of each other is compared (index 0.05%,
    future / stock 0.15%, option 2%). It counts agree / differ, and which feed is behind (last trade 30 s+ older).
  - **Failover**, setting "When Upstox goes quiet" (Live setup): **Only record** (default, shadow: Kite never feeds the
    app; it notes when it would have switched and for how long), **Use Kite's prices until Upstox is back** (Upstox
    quiet 8 s in market hours → Kite's prices go to the app; the first Upstox price switches back), **Do nothing**.
    Mandeep turns it on after a few market days of cross-check.
  - Without Kite keys or today's Kite login, or if Kite fails, the router is the Upstox feed exactly as before.
- **Setup** (Live setup → "1b. Kite"): a Kite Connect app at developers.kite.trade (₹500 / month), redirect URL
  `http://127.0.0.1:8765/kite/callback`, API key and secret typed into the app on the Mac (the secret is masked;
  the day's login is kept in `data/kite_token.json`, readable by you only, and ends at 6:00 AM). Log in once a day.
- System & logins shows the Kite row (off / login needed / running, the cross-check) and the self-check "feeds".
- Nothing in alerts, paper trades or the Lab changes: Lab regression identical.

## The chart moves with every price: a live stream to the browser (2.51)

Mandeep, 10 Nov 2026: "what if we connect the websocket for charting because we need TBT and price at the same time".

- The app already gets every price change Upstox sends on its one live feed (Upstox Market Data Feed V3, a websocket).
  Before, the chart asked the app for its last bars every 2 seconds. Now an open chart keeps a websocket to the app
  (`/ws/ticks`, `live/stream.py`) and gets each new price within about 0.1 s, with the forming candle of its timeframe
  (aligned to 09:15 like the chart's own bars), so the price and the candle move together. The streamed price shows its
  exchange time (● 09:22:59).
- Nothing new connects to a broker: the stream only reads the app's own prices. It checks every 100 ms and sends only
  when the price changed, at most 8 open charts (`rules/chart_stream.json`); every 10 s the chart still re-reads its last
  bars (volume, a missed bar). If the stream drops, the chart goes back to asking every 2 s and reconnects by itself.
- System & logins lists it ("Chart live stream (inside the app)": open charts, prices sent).
- About "TBT": exchange tick-by-tick (every single trade) is an NSE co-location / vendor product; Upstox V3 sends each
  price update as it has it, and the chart now shows every one of those.

## Credibility check, every stock and index, insiders in the indication, faster pages (2.50)

Mandeep's 10 Nov list, points 11, 6, 4, 2 and 7.

- **Credibility check** (`live/credibility.py`, Scorecard page, two Excel sheets). Read-only:
  - Every graded alert grouped by what the app said about it: signal score, the conviction model's chance (AUC and
    calibration), Focus, strength, risk check, time of day, day type, index / stock, direction, alert type.
  - Every test-bench "Buy ATM" trade split by each of ~50 facts recorded at the signal (Nifty with / against, VWAP,
    bigger timeframes, candles on each timeframe, news, VIX, IV, time...). A fact "helps" only with a clear gap that
    points the same way in the older and the newer half of the days, and 10+ days of data; before that it can only "lean".
  - First read on the Mac's data (30 Oct – 10 Nov; 21,338 alerts, 4,209 decided; 21,617 bench trades over 5 days):
    the **signal score is credible** (under 40: 55% worked → 70-85: 77%), **Focus is credible** (81% vs 68%), strength is
    credible; the **conviction model is weak** (AUC 0.56 on 7,253 alerts: stays hidden); mornings beat afternoons
    (75% vs 64%). On the bench, trades on the right side of the daily EMA 20, with the week's candle, with Nifty and with
    Nifty's VWAP lean better; an order book leaning the same way leans *worse*. Only 5 bench days: leads, not rules yet.
- **Candles are chart reading** (#6): candle-pattern rows on the Strategy board are folded under "Chart reading"; the
  credibility check shows whether a candle that agrees makes other signals better (so far: leans helpful, not proven).
- **Every stock and index** (#4): new symbol option "each index and each F&O stock, each on its own"
  (`factory.EACH`). With `rules/lab_universe.json` → `each_symbol_version` on, every directional strategy in every
  strategy file also gets this version (no re-upload); the file's own version is untouched (same id, same results).
  The Lab page shows each stock's result when you open a strategy. Straddle sells stay on Nifty / Bank Nifty; fusions
  are unchanged.
- **Insider trades in the indication** (#2): the last 5 days of promoter / director / key-manager market trades
  (₹50 lakh+) go to the AI as slow background and show on the India market tile (bought / sold ₹ crore, biggest names).
  The rules word doesn't use them (not tested yet); each reading records them for later grading.
- **Speed** (#7): big answers are sent compressed (alerts list 2.5 MB → 0.33 MB, Strategy board 0.83 → 0.10 MB, chart
  0.15 → 0.02 MB); the alerts list no longer carries the shadow Focus notes. The System page already lists slow calls.

## Best only, 3% a day, one Excel file, every stock behind a strategy (2.49)

Mandeep's 10 Nov list, points 14, 15, 10, 12 and 9. Paper only, as always.

- **Two more paper accounts** (`live/books.py`, rules in `rules/paper_books.json`), each with its own money and trades
  (saved as `upstox#best`, `upstox#target3`), never mixed with the main account, the scorecard or the test bench:
  - **Best only**: takes an alert only when it is Focus, strength 3, risk PASS, score 70+, and its type has a good
    live record (20+ graded, 55%+ worked, option up on average after an hour) or it is a Strategy Lab rule that passed
    the strict bar. Few trades by design.
  - **3% a day**: your own capital (default ₹1 lakh), compounding day to day, top alerts only (score 75+), one trade at
    a time. When the day's money (booked + open) reaches +3% of the morning's money it closes everything and stops; at
    −2% the same. Honest note on the page: 3% a day compounds to ~1,600× a year; this account measures how often a day
    gets there, it is not a promise.
  - Paper page: tabs Main / Best only / 3% a day, each account's capital box, why alerts were not taken, and
    **Accounts side by side** (trades, win rate, net, return, worst drop, green days, +3% days).
  - A broken account entry is skipped and shown as failed; the main account keeps running. System check `paper_books`.
- **Download Excel** (Strategy board): Alert check (every alert type: Working / Mixed / Not working / Not enough data,
  from graded live alerts), Alert check by day (the daily validation), every test-bench strategy × exit style, by day,
  exit styles (shadow), Strategy Lab 1 year and 5 years (train / validation / holdout: the historical test), paper
  accounts, and the last days' alerts and bench trades. Read-only. Needs `openpyxl` (added to requirements; the app
  installs it on the next start).
- **Strategy Lab page**: a "Start here" guide for beginners; click any strategy (strategy files) or rule to see every
  version and **every stock / index it was tested on**, with its result in train, validation and holdout. The split comes
  from the rule's own trades (saved apart in `lab/factory_sym.json`, so `factory.json` stays small); it fills on the next
  Lab run. Data downloads and the advanced tables (exit styles, ML, library, ebooks, runs) are folded away.
- **A time on every changing price**: index cards, the scanner, the stock page and open paper trades show the exchange
  time of their price.

## No trades on holidays, money that is really there, the scanner starts by itself (2.48)

What went wrong on **10 Nov 2026 (an NSE holiday)**: the scanner was running, the Upstox feed reconnected at 09:59 and
sent its last known prices again; the app read the jump as fresh activity, made alerts and opened **30 paper trades at
09:59:18** using **about ₹8.5 lakh in a ₹5 lakh account**. They closed on the time limit at 11:10 for **−₹22,753**.
Separately, on the **Diwali Muhurat session (Sun 8 Nov, 14:20–15:40)** the scanner would not start because it only
knew Monday–Friday.

- **NSE calendar** (`live/session.py`, dated data in `rules/market_calendar.json` + the holiday list in
  `rules/health.json`): regular hours, holidays and special sessions with the exchange circular (Muhurat: NSE
  FAOP76940, F&O 14:20–15:40). Nothing (alert, paper trade, test-bench fill, strategy desk) is made outside an open
  session; the exchange's own "market closed" message on the feed also stops it.
- **Prices judged by exchange time**: every quote keeps the exchange's last-trade time. A paper trade needs the
  option's last trade inside today's session and the underlying traded in the last 2 minutes, so a reconnect's old
  snapshot or a frozen feed never opens anything.
- **No Start button**: the scanner starts by itself before every session (09:00, or 14:00 on a Muhurat day) and stops at
  the close. The Live bar says when the next session is. "Pause for today" stops it; "Resume" brings it back. A day that
  ended no longer blocks the next day's auto-start when the app stays open overnight.
- **Paper money is real money**: a trade never uses more than the account's free money (capital + booked + open P&L −
  money in open positions). Lots are cut to fit; if one lot doesn't fit there is no trade, and the Paper page says how
  many were skipped and why. Aggressive mode is capped the same way.
- **Open trades update at once**: a new paper trade's option is streamed from the first second (before, it could wait
  for the next strike-window refresh), the Paper page refreshes every 1.5 s while trades are open, and each price shows
  its exchange time.
- **Your capital everywhere, no ₹40K presets**: the "Small account (₹40K)" button, the Lab's "Start with ₹40K" section,
  the ₹40K pill on alerts and the ₹40K columns are gone. Type your capital on the Paper page; the account rules of its
  size come from `rules/capital.json`; the alert plan and the capital test use your capital (the capital test adds it
  as its first account).
- **Options lens "Request failed (500)"**: after the day's recording was compressed and closed, the lens still read the
  closed file. It now reads the saved day, and any error shows as a message on the page.
- New check on System & logins: the NSE calendar (today's session, the next one, this year's holidays; asks for next
  year's list in December).

## Bigger timeframes, many days, "buy above the candle": the 51-strategy book (2.47)

- **Input (Mandeep, 10 Nov):** Aseem Singhal's "51 Trading Strategies" (e-book screenshots). Read in full; the
  gun-vs-bullet split was sent first. Extract: project doc `aseem_51.md`.
- **Gun (`lab/tfacts.py`, same code in the Lab and live):** 5-min bars of many days joined and resampled into 15m / 30m /
  1H candles (aligned to 09:15); a 5-min bar sees only the last *closed* candle (tests: identical on every cut, no
  look-ahead; live with 12 days of history = the Lab's facts). Indicators are a registry (Williams %R, MACD cross,
  Ichimoku, Parabolic SAR, Heikin Ashi, Donchian, Fibonacci retracement of the last swing, RSI divergence, fractals 4/4,
  Stochastic RSI, EMA 9/21, EMA 5/14 distance, Renko, consolidation breakout, retest, two-legged pullback, candle colour /
  pin bar, SMA 14/50/200, Supertrend 7,3, Bollinger squeeze / width); `rules/tf_facts.json` says which output runs on
  which timeframe (fact = `<output>_<tf>`, e.g. `wr_pull_30m`, `tk_age_1H`, `sma200_up_5m`). Also `new_<tf>` (this
  5-min bar closes a candle), `ph_brk_<tf>` (5-min close beyond the last closed candle's high / low) and yesterday's
  floor pivots (`piv_pos`, `piv_x`, `piv_rt`). A failing indicator leaves only its own facts empty.
- **Two-step entries** (`then`): the conditions mark a setup bar; the entry is the first later bar (within N bars)
  where the trigger holds: "green candle at the band, buy above its high". Older rules and ids unchanged.
- **Exits on a fact** (`exit_if`: out at the 5-min close where e.g. Supertrend flips, RSI reaches 70, price reaches
  VWAP / the upper band) and **on the option's own price** (`opt_stop_pct`).
- **Option momentum facts:** the ATM call / put chosen at 9:30 and 10:00, their premium change since
  (`ce_m0930`, `pe_m1000`...), and the straddle vs its average so far (`strd_avg`).
- **Live:** computed only when a rule uses them; history = Dhan 5-min bars (once a day per symbol). System page check
  `tf_facts`. The Lab's build makes its table every 40K rows (memory with the bigger table).
- **Also from three PDFs (10 Nov, 15:05):** liquidity-sweep facts (`pd_back`: closes back inside yesterday's range after
  a sweep of its high / low, counted; `pd_rt`: the swept level retested with a rejection candle; `eq_rt`: the same at
  equal highs / lows), 4-hour candles (09:15-13:15, 13:15-15:30) with a bigger-chart level broken and retested on a
  smaller chart (`xrt_4H_30m`, `xrt_1H_15m`), and a stop at the sweep's extreme (`dayx`: today's low / high so far).
- **Bullets:** nine strategy files (241 rules): `aseem_swing`, `aseem_intraday`, `aseem_advanced`, `aseem_scalp`,
  `aseem_options`, `aseem_price_action`, `daily_candle_sweep`, `james_berry_4h30m`, `nifty_liquidity_sweep`. Not testable yet (listed in the files): positional / daily holds (chapter 4,
  1.4, 1.5, 7.2, 7.5, 7.6), Elliott and Gann (hand-drawn), volume oscillator (no index volume), calendar / expiry decay /
  BTST / swing buying (overnight), 3 PM strategy (after 14:45), OI strikes and OTM-by-premium selling, Martingale (sizing).
- **5-year Bank Nifty (real option premiums, all charges): 0 of 249 rules pass train** (luck alone ~4.8 of 2,625 tried).
  Only lead positive in all three periods: James Berry sell (4H support broke on 30M, retest, close below the retest
  candle) +₹474 / +₹324 / +₹1,230 per trade on 37 / 13 / 19 trades (train t 0.73): too few trades to trust. Book
  scalping, SMC, Donchian, VWAP reversal, Supertrend / momentum option selling and buying lose in every period.
- Results don't move: Lab regression 280 / 280 on the rebuilt 5-year table, 103 / 103 on a 1-year set rebuilt from
  scratch (every old column identical).

## Fusions: the best strategies mixed, and how many agree at once (2.46)

- **Mandeep (6 Nov):** "system should prepare the list of all the fusion strategies like mixing the strategies and all
  the trades should be done with each strategy."
- **Every strategy already sees every trade:** the test bench takes every alert of every named strategy (library,
  book, file) on every symbol, and the Lab tests every rule on every day of the bar table. Nothing new was needed there.
- **Fusions (`lab/fusions.py`, a bullet maker):** two named strategies that must agree on the same bar. Mixing all
  with all would be tens of thousands of rules, and with that many tries some pass by luck alone, so: each named
  strategy is ranked on **train days only**; the best 24 per direction are paired (conditions joined, at most 4; the
  time windows' overlap; the symbols both trade); each pair is tested with 3 exits on train / validation / holdout like
  every rule, and every one counts in the Lab's "tried" number (the luck number grows with them). Validation and
  holdout days never choose the pairs. Queued in every Lab run (1 year and 5 years), only once each.
- **Never live by itself:** a promoted fusion runs on the test bench (kind "fusion") until you switch it on.
- **Confluence (gun, shadow):** at each alert the engine notes how many *different* named strategies fired the same
  way (`conf_al`) and the other way (`conf_against`) on that symbol in the last 15 minutes. Recorded with every bench
  signal, so the grader can show later whether "3 strategies agree" really wins more. Changes no alert or trade.
- 5-year Bank Nifty: 142 named entries, 49 with enough train trades, 1,221 fusion rules built (407 pairs × 3 exits).

## Trade journeys: every alert held all day, the full story of its profit and loss (2.45)

- **Mandeep (6 Nov):** "do not sell the trade whole day … record when profit start dropping and what was the
  situation … same with loss, when it started, why, how much max loss, what made it reverse."
- **What it records (`live/paths.py`, runs by itself after 15:50 from the day's recording; data/paths.db):** for every
  bench signal, its option bought at the ask and held to 15:15 with no exit, valued at the bid every minute:
  - highest profit and when; how deep it went first; how much it gave back after the peak;
  - when it first reached +10 / 20 / 30 / 50% and −10 / 20 / 30%; which came first, +20% or −20%;
  - where it stood at 15, 30, 60 and 120 minutes and at 15:15; whether a −20% trade came back to entry;
  - **the moments**: the peak, the start of the drop (gave back a third), the start of a loss, the bottom, the
    turn back (won back half), each with the market at that minute: underlying move since entry, distance from VWAP,
    last 5 minutes, option IV, open interest, Nifty.
- **Strategy board → Trade journeys:** the totals, the market at each moment, and a table per strategy. The **best
  fixed exit** (target × stop × time) is chosen on the older 60% of days and shown with its result on the newer days
  it never saw ("needs more days" until there are enough).
- **First real day (5 Nov, 5,036 signals):** +20% came first 22%, −20% first 15%; average max profit +13% at 39 min,
  then gave back 15.8 points; average max loss −11.3%; of 764 that fell 20%+, 12% came back to entry; held to 15:15
  +1.2% before charges. At the peak the underlying was +0.96% with the trade and only 6% were on the wrong side of
  VWAP; at the bottom 75% were on the wrong side of VWAP and Nifty was against 60%.
- Recorded only: nothing here changes alerts, the paper account or the bench's exits. A better exit becomes a bench
  style only when it wins on days it never saw, on your say-so (champion vs challenger).

## Four more strategies as files: option selling, EMA 8/20, Smart Money Concepts, daily 50% (2.44)

- **Mandeep (6 Nov):** four new inputs: Deepak Wadhwa's option-selling video (transcript + screenshots), Laksh Fxt's
  EMA 8/20 and SMC guides, KTZFx's daily-candle 50% guide. Gun-vs-bullet split said first, then built.
- **Gun pieces (once, same code live and in the Lab):**
  - `live/smc.py`: swings, break of structure (BOS), change of character (CHoCH), order blocks, fair value gaps,
    premium / discount, liquidity sweeps; no look-ahead (a swing is known only n bars later; tested on prefixes).
    Facts `smc_trend`, `smc_brk`, `smc_choch`, `smc_pd`, `smc_ob`, `smc_ob_recent`, `smc_fvg`, `smc_sweep`.
  - EMA 8/20: `ema8_20`, `ema8_20_x` (cross on this bar), `ema8_20_age`, `ema8_20s_x` (TradingView "smoothing line"
    version); `pd_pos` (where price is in yesterday's range; 0.5 = its 50% level); `brk_up_age` / `brk_dn_age`
    (bars since a 1-hour consolidation breakout).
  - Exit vocabulary for strategy files: stops at the other side of the **first 15 / 30 minutes' range** and **beyond
    the last swing**; targets in **index points / %** and in **option points / %**; and **option selling**
    (`"sell": true`: a bullish rule sells the ATM put, a bearish one the ATM call; bought back at the ask; safety stop
    when the option is up 40%). Old exits keep their keys (2.43 results unchanged).
  - The model strategies keep their 2.42 inputs; the search uses the new facts. Fixed nothing else.
- **Files (bullets): `deepak_orb30.json` (6 strategies × 5 exits), `laksh_ema820.json` (10 × 5), `laksh_smc.json`
  (12 × 5), `ktzfx_daily50.json` (10 × 4)**: 180 Lab rules, on trial (Lab + test bench only).
- **5-year Bank Nifty check (Aug 2021 → Oct 2026, real 1-minute option prices, every charge; Nifty runs on the Mac):**
  0 of 180 pass. Deepak's exact rule (30-minute range break, sell the ATM option, 30 option points target, stop at
  the other side of the range) wins **68-76% of trades** but averages −₹24 to −₹182 a trade after costs: many small
  wins, a few big losses. EMA crosses, SMC setups and the 50% level lose on train with every exit; a few look good on
  one period only (small samples). Nothing is promoted; they keep running on the bench.
- Results don't move: Lab regression 103 / 103 on all three saved sets, 280 / 280 on the 5-year Lab with the files.

## Strategy files: a new strategy is an upload, not an app update (2.43)

- **Mandeep (5 Nov):** "mein tumhe different transcript ya stretegies dunga tum file do or vo mein upload karu …
  kyuki baar baar version update nahi karna padega." So: you send a book, video, transcript, screenshot or script;
  Claude reads it, splits gun vs bullets, checks it on the data, and gives you one **strategy file** (`.json`).
  You upload it on **Strategy Lab → Strategy files**. No new version.
- **What the app does with a file** (`lab/packs.py`): reads it as data only (JSON, 256 KB max, a fixed vocabulary;
  nothing in it is ever run as code; there is no field for orders, brokers or accounts), keeps every upload (a new
  version never overwrites an old one), and
  1. queues each strategy for the next run of the 1-year **and** the 5-year Lab, one rule per strategy × exit, with the
     same train / validation / holdout test and the same charges as every other rule;
  2. runs each strategy live on the **test bench** (kind "Strategy file rule", paper-tested with the bench styles);
  3. never puts it on live paper or in your alerts by itself: a file rule can't even be ticked "Live paper" yet
     (its exits need their live version first). That stays your call.
  A strategy that needs a fact the app doesn't have is listed as **can't test yet** with what it needs; the rest of
  the file still loads. That missing fact is a gun piece: one app version, once; after that every file can use it.
- **What a file can use:** every Lab fact (`/api/packs/vocabulary` lists them), the built-in exits, and its **own exits**
  built from a fixed vocabulary (`lab/exits.py spec_check`): stop = ATR multiple / signal candle / **the other side of
  the first 5-min candle** / % of price; target in R or none; breakeven; ATR trail; **momentum exit** (the first 5-min
  candle that closes beyond the previous candle against the trade, or makes a new extreme against it and closes
  against it); time limit. The same exit gets the same key in every file and Lab (a content hash), and every exit
  ever uploaded stays known, so saved results can always be rescored. A file rule may start at the **09:25** bar (the
  9:20 setup); search, library and ML rules still start at 09:30.
- **Gun pieces added once (for the 9:20 setup, usable by any file):** `d_struct2` / `d_struct3` (daily structure:
  higher lows / lower highs over the last 2 / 3 days, `live/htf.py struct`, same code in the Lab and live),
  `fc_color`, `fc_range`, `fc_pd` (the first candle took out yesterday's high / low), `fc_run` (5-min closes in a row
  beyond the first candle), `hw_open` / `hw_day` (index heavyweights, weighted; `rules/heavyweights.json` is dated
  data: Bank Nifty = HDFC Bank, ICICI Bank, Axis Bank). The first-candle stop and the momentum exit.
- **First file: `ghanshyam_920.json`** (Ghanshyam Tech's 9:20 setup): 20 strategies × 5 exits = 100 Lab rules.
  Checked here on the real 5-year Bank Nifty history (Aug 2021 → Oct 2026, 1,273 days, real 1-minute option prices,
  every charge and slippage; train → Sep 2024, validation → Oct 2025, holdout → Oct 2026): **0 of 100 passed**.
  The plain first-candle break loses about ₹200-400 a trade in all three periods with every exit; the trend filter
  (920-A), against-trend (920-B), gap-against-trend (920-C) and the three banks agreeing (920-D) do not fix it. His four
  example days are found by the facts (23 Sep 2022: yesterday's low broken inside the first candle, 3-day downtrend,
  all three banks down: +₹5,389 a lot with his momentum exit). The only one positive in all three periods is 920-E
  call at 09:25 (yesterday's high taken out inside the first candle, 3-day uptrend) with the momentum exit:
  +₹104 / +₹430 / +₹843 a trade, but on 65 / 16 / 9 trades (t 0.4 on train: not proof). They now run on the test
  bench to build a live record; nothing is promoted.
- **Results don't move:** Lab regression 103 of 103 identical on both saved sets; the synthetic bar table rebuilt with
  2.43: its 101,268 old rows identical in every old column (1,044 new 09:25 rows) and 103 of 103 rules identical on
  it. The model strategies keep their 2.42 inputs and rows; the search never uses the 09:25 rows or file exits.
  The first Lab run after this update rebuilds each bar table once (new facts), 1-year and 5-year.
- Also fixed: the 5-year bar table build crashed on a symbol with no history (`day_table` on no rows).

## Full audit: every day's data kept, connected and safe (2.42)

- **Mandeep (5 Nov, 01:24):** ensure every piece of data strengthens the system, is connected and heads towards the
  best accuracy, alerts and paper trades; scan the full app as a QA / senior engineer. (The hard rule stays: alerts +
  paper only, no order code.)
- **Found on the Mac's real data (30 Oct – 5 Nov) and fixed:**
  1. **Data loss:** the 1-minute recordings of 3 and 4 Nov hold one minute each. An app restarted after the close
     (16:24 on 4 Nov, 23:26 on 3 Nov) ended its "session" at once and its one-minute save replaced the whole day
     (2 Nov, never restarted, kept 12.8 lakh rows). Now every part is merged into the day's earlier save (keyed, no
     duplicates), an unreadable file is kept aside, never deleted (`live/record.py merge_write`).
  2. **Night prices recorded as the new day:** a scanner started at 00:37 wrote 1.4 lakh rows of the last close into
     5 Nov. Prices are recorded only inside 09:00–15:31 IST, and the day's save drops any outside it (news keeps its
     own time). The simulator is unchanged (its days are replaced, as before).
  3. **Stale previous day:** the daily history built at 00:46 on 5 Nov had 3 Nov as its previous day (30 Oct had 28
     Oct): the broker publishes a day's daily candle hours later and the copy was cached for the whole day, so the
     previous high / low / close, gaps, the daily trend and daily patterns were a day old. It is now loaded again when
     it misses the last trading day, and retried every 30 minutes until the broker has it.
  4. **Newer readings not stored with signals:** every bench signal now also records the candle forecast (fc5 / fc15,
     signed strength and PAKKA), Nifty's (mfc5 / mfc15), the cascade's chance of green (nc / mnc), the India indication
     (ind_al) and the data guard (dq_bad), all aligned with the trade's direction, for the honest-label model later.
     Recorded only: nothing decides on them.
  5. **No log on disk:** the engine log lived only in the Terminal window. Now also `data/logs/app.log` (5 MB × 10),
     tokens / keys / PINs masked (`live/applog.py`).
  6. **No backup:** six months of the app's own record would sit in two SQLite files with no copy. Now a daily safe
     copy after 16:30 (SQLite online backup, checked, 7 days kept) in `data/backups/` (`live/backup.py`,
     `rules/backup.json`: set "dir" to an iCloud Drive or external-disk folder to keep a copy off this disk).
  7. A world-market alert at 00:46 was stamped on 5 Nov: macro alerts only 08:00–15:30 on weekdays now.
  8. The symbol page answered 404 (a console error) when the scanner was off: it now answers "closed" and shows the
     same message.
  9. The candle forecast trained only when the chart was opened: the watcher loop now starts it (first time, then
     weekly after 16:15), and a run that left a timeframe untrained (e.g. history missing) is retried after the close
     instead of waiting a week.
- **Checked and fine:** no duplicate rows (cascade grades, outcomes, bench trades / signals); no bench or paper trade
  left open; facts on bench signals 98–100% filled (IV percentile builds up with days); no secret in git; the server
  listens only on this Mac (127.0.0.1); no order / modify / cancel endpoint anywhere; every page on the Mac's data at
  desktop and phone width: no console error, no sideways scroll, no API call over 1.5 s.
- **Open, for Mandeep's say-so (it changes a working default):** the test bench triggers a stop on the executable price
  (bid for a long leg); where the bid-ask spread is wider than the stop distance the trade stops out at once (239 trades
  on 4 Nov, 0.7%, about −₹4,000 each). Brokers trigger SL-M on the last traded price. IV-spike alerts carry no
  direction, so the Scorecard does not grade them (160 on 4 Nov). `live/positions.py` and `live/strategies.py` are
  imported by nothing (kept: experiments are kept). Test coverage: 66% overall; bench 91%, paper 85%, recorder 89%,
  store 90%, cascade 91%, candle forecast 88%; the engine 61% (mostly broker / network paths).

## Candle forecast on the Fortune chart: colour with an honest strength, shape, patterns, 90% close zone (2.41)

- **Mandeep (4–5 Nov):** from the timeframe cascade, say which candle is forming on the real chart (hammer, shooting
  star, doji, marubozu, engulfing, inside / outside bar ...) and where it closes; give it strength from the times the
  chart looked like this before; 90%+ accuracy, counted only on predictions made before the candle closed.
- **Gun (`live/candlecast.py`):** pattern / shape labeller (8 shape families named by the move before: Hammer, Hanging
  man, Shooting star, Inverted hammer, Doji, Spinning top, Marubozu, plain up / down candle; multi-candle patterns from
  `live/candles.py`), similar-chart matcher (shape of the last 12 candles + the candle so far, nearest 50 in the
  history), strength ladder + PAKKA level, 90% close zone (quantile models widened on validation: conformal), forward
  grader (`candlecast` table, every stage of every candle), training job `lab/proc.py "candlecast"` (child process,
  nice 10, one at a time; first time when no models exist, then every 7 days after 16:15), System check `candle_forecast`.
- **Bullets (`rules/fortune.json` "cast"):** symbols (Nifty, Bank Nifty: years of 1-minute history), target 0.90,
  split 60 / 20 / 20 by days, model sizes, similar-chart pool and neighbours, pattern list, zone cover, retrain days.
  A failing pattern bullet is skipped and logged; the 2.39 cascade candle (champion) is unchanged.
- **Honest test (data/history 1-minute, Jan 2022 → Nov 2026, Nifty + Bank Nifty; learned to Nov 2024, numbers from
  the 240 days after 14 Nov 2025 no model or level saw):** PAKKA calls at 90%: 5m after 2 min 22% of candles 89.7%,
  after 3 min 54% 90.2%, after 4 min 87% 90.3%; 15m after 5 min 16% 90.9%, after 10 min 65% 90.0%; 1H after 30 min
  41% 89.9%, after 45 min 79% 88.8%; 1D after 5 hours 92% 89.9%. **The plain rule "it keeps the colour it has so
  far" was right as often on the same candles** (calls against it: almost none, about half right): price history alone
  shows what the chart already shows. Before a candle starts: 50–51% (a coin flip). Shape family: 4–6 points better
  than "the shape so far" on 5m / 15m / 1H (34–54% right), not on 1D. Inside / outside bar 90–96% right when called.
  90% close zone held 89.5–90.7% (1D 86–90%). Similar charts alone: weaker than the model (72% vs 78% at 5m after 3 min).
- **Chart:** side panel "Candle forecast (trial)": PAKKA / Watching, direction and strength, how right that strength was
  on unseen days, the plain rule's view, shape (top 3), patterns with their unseen-day record, 90% close zone, the 50
  most similar past charts (share green, closest three with date and symbol); arrow + strength on the forming candle and
  the 90% zone as two lines; report table "Candle forecast: how good" per stage with the live PAKKA record.
- **Next (to beat the plain rule):** option-chain facts from the 5-year 1-minute option files (premium, IV, OI, volume
  per strike) as a challenger voter; live order flow / OI / news after 5+ days of graded candles.

## Fortune chart: its own market of predicted candles (2.40.1)

- **Mandeep (4 Nov evening):** the shadow candle under the real one was hard to read. The Fortune chart is now its own
  "market ahead" chart: **only Fortune's predicted candles**, no real candles (those are on the chart above).
  **Blue = predicted up, orange = predicted down**; each candle is what Fortune predicted for that candle after its
  first small candles closed (same calculation as 2.39); the lighter one at the right edge is the candle forming now,
  drawn before the real one is done, with its likely close range as two grey lines.
- Its own timeframe buttons: **5m / 15m / 1H / 1D** (remembered), whatever the main chart shows. "Real close line"
  (off by default) draws the real closes as a thin grey line to compare. Past candles: the days the history never saw,
  then every live first call (graded) from the `nextcandle` table.
- Side panel in plain words (the candle forming now, how good it has been against "keeps its colour"); the smart
  report stays under the chart. The 2.36 / 2.38 ranges and ghost candles are no longer drawn (the data is unchanged).

## India market indication, the watcher loop back on, Paper page tidy (2.40)

- **India market tile (Live page, the market strip's empty space):** one word for the whole Indian market (Nifty, Bank
  Nifty, Fin Nifty, Midcap Select, Sensex together): **Gap up / Gap down** before the open, **Positive / Negative /
  Neutral** after it, with one or two short lines, marked **AI suggestion**. Claude writes it every 15 minutes from 8:30
  to 4:00 PM on trading days (8:30, 8:45, 9:00, 9:15 ...) from GIFT Nifty's move, the global indices and their pressure
  on Nifty, US futures, crude, USD/INR, the VIXes, FII / DII, today's events and the latest headlines (after the open
  also the indices' moves, breadth and the day type); after 4 PM the last reading stays until the morning. Without AI,
  past its own allowance (32 a day, apart from the other automatic notes) or on an unreadable answer, the same word comes
  from rules and the tile says "rules". Every reading is saved; after the close the last pre-open reading is graded
  (did it open with that gap, did it close that way) and the tile shows how often the open call was right.
  `live/indication.py`, bullets in `rules/indication.json`; it fills exactly the rest of its row at any width.
- **Found and fixed: the app's watcher loop had not been running since 2.11.** A second method named `_watch` (the
  Sensex / Nasdaq list) silently replaced the loop inside the class, so the 9:00 auto-start, login reminders, the 8:45
  brief, the nightly Strategy Lab, the 2.33 health monitor and keep-awake never ran. Renamed (`_watch_loop`); a no-clash
  test now fails on any method defined twice, and the System page checks the loop. From this version these all run:
  the nightly Lab in its own low-priority process (2.38) from 4:15 PM.
- **Paper page:** the prop-firm challenge numbers sit in a 2 x 2 grid (no more "−₹66," / "Biggest dro" cut off); the
  patterns say who took the trades in words ("the first day (before 2.14 ...)" instead of "None") and money as −₹251;
  the closed trades show the latest 25 (10 on a phone) with "Show all", dates as "3 Nov" and prices / exits on one line.
- Checked: full tests, Lab regression 103/103, Live strip at 1440 / 1100 / phone and the Paper page on the Mac's data.

## Next candle from the smaller ones: the Fortune chart's shadow candle and smart report (2.39)

- **Mandeep's cascade (4 Nov):** once 3 one-minute candles of a 5-minute candle have closed, say how that 5-minute
  candle ends; after 2 five-minute candles, the 15-minute one; after 3 fifteen-minute candles, the hour; after 3 hours,
  the day (pairs are bullets: `rules/fortune.json` "cascade"). The prediction updates with every small candle.
- **How (live/nextcandle.py):** every past candle of the same kind is cut at the same point and described by the same
  facts (move so far, stretch up / down, place in its range, the last small candles, volume against normal, distance
  from VWAP, trend on that timeframe, the previous candle, time of day, the day's move, the market's move in the same
  minutes: Nifty, or Bank Nifty for Nifty itself). The 60 most similar past moments (earlier days only) give the shadow
  candle: close at their middle, a 68% box, high / low by their usual stretch, and the chance it ends green.
- **On the chart:** the prediction is a faded candle under the forming one; the real candle is drawn on top and takes
  its place when it is done. "Past predictions" shows the earlier ones under the real candles (unseen days, then live);
  "Range cone" brings back the 2.38 cone and boxes (off by default: the chart is about the next candle).
- **Smart report (under the chart):** on the latest days the history never saw, live (all days) and today: how often
  green / red was called right, against the plain rule "it keeps the colour it has so far" (the real bar: after 3 of 5
  minutes most candles already show their colour), close error against "it stays put", and how often the real close
  landed in the 68% box; plus the latest graded predictions.
- **Tracked all day** (its own thread, every ~10 s): Nifty, Bank Nifty, any chart you open and the in-play stocks;
  every prediction is graded at its candle's close into the `nextcandle` table with the facts that have no history
  yet (order flow / buying, OI, PCR, news, event risk, breadth, VIX) for a later challenger.
- **First honest result (Mac history, Nifty and Bank Nifty, unseen days):** colour called right 74-89%, but "keeps its
  colour" was 76-89% on the same candles: the other facts add −2 to +1 points (no edge on any pair), the close error
  equals "stays put" (−2% to +1%), and the 68% box held 55-73%. The report says exactly this; the live record (with
  order flow, news and the rest) decides from here.
- Checked: full tests (a planted volume pattern is found, a random walk is not), Lab regression 103/103, chart on
  5m / 15m / 1H / 1D / 3m at desktop and phone on the Mac's data.

## Speed, lot and money on every trade, dates on the board, market hours, ghost candles (2.38)

- **Why the app crawled with the market closed:** the nightly Strategy Lab (thousands of rules on a 200 MB table, often
  until the morning) ran inside the app's own process and took Python's single lock from every screen; the 5-year
  Dhan download used the whole shared Dhan pace (72,000 calls by 7:45 AM today), so a chart's Dhan calls queued
  behind it; and the settings file was parsed again on every call (~30 ms each).
  - Lab rounds and model strategies now run in their own low-priority process (`lab/proc.py`, nice 10): the same
    code, the same results (a test checks it), one run per Lab folder as before.
  - Screens, news and movers go first on the shared Dhan pace; the download waits while they need it (`lab/dhan_budget.py`).
  - Settings are parsed once and again only when the file changes (0.2 ms a call).
  - Every screen call is timed: **System → Speed** shows the slow ones (over 1.5 s), so speed is measured, not guessed.
- **Lot size, quantity and money on every trade:** paper trades show lot size × lots = quantity and the money the trade
  took (premium; margin estimate for a short) out of the account size; the same is recorded for every test-bench and
  shadow-exit trade (new columns, old trades worked out again).
- **Strategy board:** every row shows its first and last trade time; tap a row for its trades (in at, out at, price,
  lots, quantity, money, net). Strategy desk positions show in / out time and quantity.
- **Live page:** every block shows when its data was fetched (or the last session's time), every market card says
  Open / Closed / Break / Holiday / No update from `rules/market_hours.json` (dated bullet: each market in its own time
  zone, lunch breaks, overnight GIFT Nifty, Sunday-Friday futures and currencies, 24/7 crypto, NSE holidays), and the
  World markets block says the global pressure on Nifty in words with its biggest drivers, like the World page.
- **Fortune chart ghost candles:** faded candles ahead of the real ones. The forming candle is drawn finished at its
  likely close; the next 6 are purple range candles on the middle path (box = where that candle's close likely lands,
  68%; wicks = its usual high / low). No direction is made up. They redraw every 10 s while the market is open
  (`rules/fortune.json`: ghost, ghost_candles, refresh_s).
- Checked: full tests, Lab regression 103/103, Live / Chart / Board / Paper / System / Desk at desktop and phone with
  the Mac's own data (no errors, no sideways scroll; a long Lab rule name no longer widens the chart page on a phone).

## Shadow Focus: is Focus leaning on one or two alert types? (2.37)

- **Why:** on the first live days 89% of Focus came from two alert types (unusual options 53-54%, sharp move 36-37%),
  and both lost on paper. The old grade (last price, ±20%) is far kinder than real fills: the same 30 sharp-move alerts
  the paper account took were +1.8% on the old grade and −6.5% in real paper trades (spread, slippage, stops).
- **Champion vs challengers (live/focuslab.py, rules/focus_shadow.json):** the current Focus is untouched. Every
  directional alert is also judged by 3 challenger versions at the same moment, and their choice (pick + why) is saved
  with the alert (`data.focus_ch`) and on its test-bench signal. Nothing you see, Telegram, the risk check or the paper
  account reads it (checked on the System page and in the tests).
  - `honest_cap30` (main): honest grade + no alert type over ~30% of the day's picks;
  - `cap30`: the current Focus rules + the same cap (is it the cap that helps?);
  - `honest`: honest grade only (is it the grade that helps?).
- **Honest grade:** the test bench's 1-lot trades of that alert type (ask in, bid out, every charge, the paper
  account's exits), from earlier days only. 15+ trades and average minus its uncertainty above 0 = winning (gets in from
  severity 2, even if the old scorecard demoted it); average plus uncertainty below 0 = losing (never in); anything else
  uses the current Focus bar. Today the bench has no record yet, so the challengers start on the current bar.
- **Type cap:** with 4+ types good enough today, none above 30% of the picks; with 2-3 types, an even split at most;
  while only one type has shown up, 2 of it. One pick per symbol per hour, like Focus.
- **Where:** Scorecard → "Focus: current vs challengers" (picks, mix, honest ₹ a trade on the same alerts, old grade,
  by day); Alerts → **Shadow Focus** view. Days before a version existed are replayed with the same code ("replayed");
  days before the current Focus rules (2 Nov) are not compared.
- **Replay on the Mac's 2 and 3 Nov:** current Focus 255 picks (127 a day: 54% unusual options, 37% sharp move);
  `honest_cap30` 46 picks (23 a day: 30% / 30% / 28% volume surge / 7%), option +9.3% vs +6.4% an hour later (last
  price, before costs: not proof). The honest bench result decides from today on.
- A challenger replaces the current Focus only on Mandeep's say-so, after it beats it on the same days with enough trades.

## Fortune chart: honest ranges for the forming candle and the next few (2.36)

- **Chart page, under the main chart (live/fortune.py, rules/fortune.json):** for the timeframe you pick (1m to 1M) the
  app replays the symbol's own history: every past candle is cut at the same point the forming candle has reached
  (say 70% of its time) and compared with how it really ended. It shows:
  - where the forming candle's close will likely land (68% and 95% ranges, orange lines on the chart) and how far its
    high / low still stretch;
  - its shape so far (hammer, inside bar...) and how often that shape held to the close in this history;
  - how often the rest of such candles went up: about 50% means **no direction edge**, and the page says so;
  - a range cone for the next 6 candles (purple: 68% and 95%), moves measured in the ATR of the time;
  - the trend on that timeframe with its own historical hit rate (a lean, not proven) and the event risk.
- **Checked live:** every forecast is graded when its candle closes ("the 68% range held 69% of 120 forecasts"). In
  the tests, the 68% range held 58-78% on days the history never saw.
- **Buying at 70% of a candle, tested honestly:** the candle scanner now also checks every 15m / 30m / 1H / 75m candle
  at 70% of its time; alertable shapes are paper-traded on the test bench as version `<tf>@70%` next to the
  close-based `<tf>` version, and how often a 70% shape held at the close is counted. The Strategy board will show
  whether entering early beats waiting. Information only: no alert, no paper account.
- Checked: 198 tests, Lab regression 103/103, chart page (1m-1D, index and stock) at desktop and phone, no errors.

## Event engine: official feeds and event risk on every alert (2.35)

- **Official feeds (live/events.py, rules/event_sources.json):** RBI press releases and notifications, SEBI orders and
  circulars, Government (PIB, economy-related only), NSE circulars and BSE notices: free RSS, read every 2 minutes in
  market hours (checked live: all six read on the Mac). Each item is tagged high / medium / low by its words, noise
  (recruitment, greetings, sanctions lists, auction results...) is dropped, it is matched to F&O stocks by name, kept
  across restarts and saved with the day's data. A new **high-impact** one naming an F&O stock (or hitting the whole
  market) goes to Telegram, at most 6 an hour.
- **Event risk on every alert and every test signal:** India's scheduled days (RBI policy, Budget, rules/events.json),
  high-impact world events in the next 2 hours (US CPI, Fed...), the stock's results / board meeting, expiry day, the
  F&O ban list, a market holiday on the next weekday, and fresh official news on the stock or the market. Shown as one
  "⚡ Event risk" line on the alert, the important ones as warnings in the risk check, and recorded as facts
  (ev_level, ev_glob_min, ev_results_d, ev_expiry, ev_ban, ev_off_sym_min...) for the models. **It never blocks an
  alert or a trade.**
- Live page "Events today" panel; System page card for the official feeds (each feed's status); selfcheck
  event_sources. Checked: 194 tests, Lab regression 103/103, Live / Alerts / System pages at desktop and phone.

## Data quality guard (2.34)

- **live/dataguard.py, rules/dataguard.json:** every 5 seconds every live price is checked.
  - A jump bigger than 1.5% (index), 6% (stock / future) or 45% (option above ₹3) since the last check is held as
    suspect: if it snaps back within 10 s it was a **bad print** (recorded, never used); if it stays it was a real move.
  - Crossed quotes (bid above ask) are suspect while they last.
  - An index / stock / future with no tick for 90 / 300 / 120 s in market hours is **stale**.
- **What it changes:** a price-based alert on a symbol whose own price is suspect or stale **waits** (mode "hold",
  on your say-so; listed on the System page; news alerts are never held). The test bench never fills on a quote older
  than 3 minutes or suspect, and never closes a trade on a suspect price. If 30%+ of the liquid instruments go stale
  at once, the health monitor tells you (a stuck feed).
- **Saved for backtests:** every finding goes into the day's recording (part "quality"), so bad ticks can be left
  out of any test. The Kite cross-check will plug in here as one more rule.
- System page "Data quality guard" card; selfcheck data_guard. Simulator: time limits scale with its speed.
  Checked: 192 tests, Lab regression 103/103, System page at desktop and phone.

## Health monitor and keeping the Mac awake (2.33)

- **Health monitor (live/health.py, rules/health.json):** every ~20 s, whether or not the scanner runs, it checks that
  the app is really collecting data and tells you on Telegram and on the System page when it isn't:
  - the Mac slept, the app froze or was closed during market hours ("not running from 10:12 to 10:40, 28 market
    minutes"); every gap is saved per day so backtests know which minutes are missing;
  - the live scanner isn't running when it should (with the reason, e.g. Upstox login needed);
  - today's recording stopped growing; problems piling up (12+ in 10 minutes, the top 3 named);
  - disk under 10 GB; the Mac on battery during market hours;
  - one summary after the close (MB recorded, chains, headlines, candle patterns, test trades, gaps).
- **Keep the Mac awake:** on trading days 08:50-15:45 the app runs macOS's built-in `caffeinate -i -w <app>` (a
  temporary "don't sleep while idle"; it ends at the close or when the app quits; no setting is changed). Closing a
  MacBook's lid still sleeps it: keep it open and on the charger. Switch: rules/health.json keep_awake.
- **NSE holidays 2026** (dated, rules/health.json): nothing nags on a holiday or weekend.
- System page: "Health monitor" card (scanner, recording, keep-awake, power, disk, problems, gaps today, latest
  notices); selfcheck health_monitor. Checked: 188 tests, Lab regression 103/103, System page at desktop and phone.

## Candle patterns on every timeframe (2.32)

- **Multi-timeframe candle scanner (live/mtfcandles.py, rules/candle_tfs.json):** every candle pattern (engulfing,
  harami, hammer / shooting star, pin bar, morning / evening star, tweezer, dojis, inside / outside bar, NR7, inside-bar
  breakout and fakey, rectangle breakout and failure) is checked on **5m, 15m, 30m, 1H, 75m, daily and weekly** candles
  of every index and F&O stock, the moment each candle closes. Intraday candles come from the live 1-minute candles,
  joined to yesterday's recorded ones; daily / weekly from finished days only. A candle still forming is never a pattern.
- **Recorded:** every pattern on every timeframe (data/recordings, part "candles"), and the day's 1-minute candles of
  every symbol (part "bars": backtest data, and tomorrow's scanner continues from them).
- **Facts on every test signal:** which way the latest candle on each timeframe points (cdl_15m_al ... cdl_1w_al,
  cdl_al_sum), so the honest-label model can learn which multi-timeframe combinations work.
- **On trial on the test bench:** alertable patterns (with their key-level rule) on 15m / 30m / 1H / 75m / daily /
  weekly get a paper trade in every style (kind candle_<pattern>, version = timeframe; weekly ones on the week's first
  session). The older 5-minute candle trials keep running as they were. No alert, no paper account until the record
  earns it.
- **Chart page:** "Candles on every timeframe": the last closed candle's patterns and how each forming candle looks so
  far (information only). System page: patterns recorded today and the scanner's last run per timeframe.
- One timeframe per loop and a one-pass pattern scan (same results as the Lab functions, tested): 218 symbols in
  about 0.4 s per closing candle. Checked: 182 tests, Lab regression 103/103 on all baselines, pages at desktop and phone.

## Every signal 3 ways, 4 capital accounts, the mover sweep and the data vault (2.31)

Testing at full width: every signal paper-traded in 8 styles, at 4 capitals, with everything around it recorded, so in
a few months the app has its own backtest data no broker app gives (intraday full option chains, every headline, every
signal and its honest result). Paper only: nothing is ever sent to a broker.
- **3 ways of trading, 8 styles (rules/bench_styles.json, bullets):** only buying (ATM, 1 OTM, 1 ITM, ATM held to the
  close), buying + selling (debit spread, credit spread) and only selling (sell ATM, sell 1 OTM held to the close).
  Fills like the paper account (buy at the ask, sell at the bid, every charge on every leg); target / stop are % of the
  premium paid or received. 2.30's option buy and debit spread fill exactly as before. A style is a list of legs: add
  one in the file, no code change; a broken one is skipped and shown on the System page.
- **Money blocked per lot (live/margin.py, rules/margin.json, dated):** premium for buys, an estimate of SPAN + exposure
  for sold options (hedged ones about 75% less). So each capital only takes what it could really afford: with ₹40K that
  is buying and some spreads; naked selling needs about ₹1.2L a lot of Nifty.
- **Capital test (Strategy board):** 12 virtual paper accounts, ₹40K / ₹1.5L / ₹3L / ₹5L × only buying / buying +
  selling / only selling, each following its tier's rules (lots, risk a trade, trades a day, daily loss stop, profit
  lock, money for margin) and growing with its profit. Each is run two ways: every signal that fits (first come), and
  **proven only** (strategies already positive after costs on earlier days: a walk-forward test of following the board).
- **Mover sweep (live/movers.py, rules/bench_movers.json):** every 15 minutes the F&O stocks moving most today (measured
  in their own daily range) and fast-moving indices are tested both ways, with the move and against it: the yardstick
  every strategy has to beat. They stay "in play" for the day and every Lab library / book rule runs on them too.
- **Facts on every test trade:** one row per signal (bench_signals) with what the app knew then (trend, day type, VIX,
  IV, PCR, OI, order flow, news, results and events ahead, daily / weekly trend, world, movers) and how it rated it
  (severity, score, conviction, Focus), for the honest-label model next.
- **Data vault (rules/vault.json):** besides every streamed price each minute, today's recording now keeps every full
  option chain the app fetches (indices every 3-6 min) plus a full chain of every F&O stock every 30 minutes (only with
  spare Upstox calls; it never changes what the scanner uses), every headline, and the market around it every 5 minutes.
  Compressed at the close into data/recordings. The System page shows what was recorded today and on past days.
- **The bench has its own database (data/bench.db):** tens of thousands of test trades a day, and the board / accounts
  reading them on their own connection, can never slow or lock the live engine (tested: a write during a full
  recompute takes 2 ms; the board on 1 million trades: 3 s once, then 0.1 s). The 2.30 trades are copied over once and
  the originals are kept.
- Checked: 177 tests incl. no-clash (new: bench styles, mover settings, the vault only with spare calls, today's
  recording); Lab regression 103 of 103 on all four baselines; Strategy board and System page at desktop and phone
  width, no errors, no sideways scroll; a simulator session ran all 8 styles, the mover sweep and the vault.

## Test bench, Strategy board, capital for any amount (2.30)

The goal: find what makes money for a ₹40K option buyer, from live markets, as fast and as honestly as possible.
- **Test bench (live/bench.py):** every directional alert (Focus or not, demoted or not), every idea on trial and every
  Strategy Lab library / book setup on the indices (84 entry rules) gets a 1-lot paper trade, filled like the paper
  account: buy at the ask, sell at the bid, STT 0.15% and every charge, the paper account's target / stop / time limit,
  closed by 15:15. Two styles each: **option buy** and **debit spread** (the same option bought, the one 2 strikes out
  sold). No daily cap: the record grows with every signal. Day type, days to expiry, time and money per lot are kept.
- **Strategy board (Test & research → Strategy board):** every strategy × style ranked by money per trade after costs
  minus its uncertainty, for today's day type (trend / range / quiet / volatile) or all days, with today's live
  results, filtered to what fits your capital. "Best for you right now" needs 5+ closed trades and a positive rank.
  The Live page shows the top 3 for today's day type.
- **Honest grade on the Scorecard:** next to the old "worked %", each alert type's real ₹ a trade from the bench.
  Demotion, Focus and the conviction model keep the old grade until you switch.
- **Capital:** type any amount ("40k", "1.5 lakh", "2,00,000", "1 cr") on the Paper page or the Strategy board; you see
  what it means before saving; wrong input gets a clear message (the old field read "40k" as ₹40). One capital for
  paper, the risk check, the Lab and the board; saving applies the account rules for that amount (rules/capital.json;
  ₹40K = the small-account preset, unchanged).
- **Prop-firm challenge (Paper page):** +10% without a 4% losing day or a 10% drop, over 20+ trading days (rules in
  rules/challenge.json). Start a new one any day.
- **Tilt coach (Paper page):** your patterns in numbers (after two losses, by who took the trade, by hour, busy days);
  after two losses in a row the risk check shows a reminder (a warning, never a block).
- Checked: 168 tests incl. no-clash; Lab regression 103 of 103; Strategy board, Live, Paper, Scorecard and settings at
  desktop and phone width, no errors; typing in the capital box survives the Paper page's 3-second refresh.

## Live page, Alerts and a broker-style chart (2.29)

- **Live page works when the market is closed:** index cards from the last session (saved every minute while the
  scanner runs), else Yahoo. No alert list on the Live page any more (they live on the Alerts page).
- **Index cards:** day range, 52-week range, all-time high / low (Yahoo, since 2007 for Nifty), daily and weekly trend,
  and **merged support / resistance**: OI walls, yesterday's high / low / close, pivots, last week's and the 20-day
  high / low, 52-week and all-time highs, round numbers; levels that agree are one zone (live/levels.py).
- **Sensex and GIFT Nifty:** change in points, open, previous close, day range, 52 weeks, all-time high; GIFT's average
  price, contracts and its gap to Nifty (premium now, or a hint for the next open after the close).
- **World markets:** US (Dow, Nasdaq, S&P 500 + futures, US VIX), Asia (GIFT, Nikkei, Hang Seng, Shanghai, Kospi,
  Taiwan, ASX, Singapore), Europe (FTSE, DAX, CAC, Euro Stoxx), money and commodities (USD/INR, dollar index, US 10Y,
  Brent, gold, silver, Bitcoin), with day range, 52-week range and all-time high; refreshed every 30 seconds.
- **Alerts page:** Act now → Focus (Focus plus the other important ones that are not demoted) → All, each with its
  count; the type chips are quick filters; the view, type and symbol are remembered.
- **Chart:** 1m 3m 5m 15m 30m 1H 1D 1W 1M with years of history (Dhan, cached on the Mac; Yahoo without Dhan); older
  bars load as you scroll left; each timeframe opens at its own zoom. Clean by default: candles plus the move
  prediction (scenarios, expected move); every indicator and level is one click away and remembered. Indicators are
  computed on all loaded bars. Opens at once when the market is closed (side panels follow).
- **All timeframes:** small to large (5m → daily), each at its own zoom, and an **All timeframes read** at the top of
  the side panel: one verdict (aligned trend, pullback or bounce, range), each timeframe's trend, RSI, structure and
  nearest levels, and what would change the view.
- Checked: 162 tests incl. no-clash; Lab regression 103 of 103; Live, Alerts, Chart, Scorecard and System at desktop and
  phone width, no errors, no sideways scroll.

## Everything connected: the bigger picture on every alert (2.28)

An end-to-end check of chart data → strategies → news → fundamentals → score → risk check → paper. What was missing
or not joined up, and what changed (nothing that decides a trade changed: score, Focus, risk verdict and paper are the same):
- **Daily / weekly trend was on the chart only, not in alerts.** Now every alert records the bigger picture (daily and
  weekly trend, supertrend, RSI, where price sits in its 20-day range, room to the 20-day high / low) from the daily
  candles already loaded, finished days only. Shown on the alert as "🔭 Bigger picture".
- **No results calendar.** NSE's upcoming board meetings (results, dividends, fund raising) are read every 6 hours.
  Alerts on a stock with results today / tomorrow say so; index alerts on RBI / Budget days say so.
- **Risk check warnings (not blocks):** results today / tomorrow (IV crush, gap), index event today, against the daily
  trend, right at the 20-day high / low. PASS / BLOCK is unchanged; these become rules only on your say-so, once the
  record shows they help.
- **Conviction model:** a challenger that also uses the bigger-picture facts competes with the current model on the
  same unseen days and replaces it only if it is 0.02 AUC better. The current model is unchanged (tested).
- **Strategy Lab:** the same daily-trend facts (d_trend, d_vs_ema20, d_pos20) in the bar table and in live Lab rules;
  5 more book setups that follow the daily trend; untested library / book setups are queued automatically before every
  Lab run (1-year and 5-year, including the nightly run), so nothing new waits for a button.
- **5-year Lab rules never reached live.** Rules it promotes now run live **on trial** (recorded and graded per rule,
  Scorecard → On trial), until you switch one on.
- Chart overlays say when a Lab rule needs facts the chart can't rebuild for past days (VIX, expiry, IV, daily trend).
- The AI "Why?" note now reads the bigger picture too.
- Checked: 154 tests incl. no-clash; Lab regression 103 of 103 identical on old and rebuilt bar tables; alerts,
  Scorecard, System, Lab and Chart pages at desktop and phone width, no errors. System page lists the NSE results
  calendar and two new checks (results calendar loaded, daily trend known).

## The basics, trained in: candles, price action and chart patterns (2.27)

From your three basics books (The Candlestick Trading Bible, Fidelity / Kirkpatrick "Identifying Chart Patterns", A to Z
of Stock Market), split into gun and bullets:
- **Gun (built once, shared live and in the Lab):** a candle-shape engine (live/candles.py) that reads 15 patterns on
  any bars; "formed at a key level" (the bar came to yesterday's high / low / close, the opening range or VWAP and
  closed on the right side); and a **trial recorder** (live/trials.py) that follows each idea's stop and 2R target live.
- **Bullets (on trial, nothing you see changes):** engulfing, harami, dragonfly / gravestone doji, hammer / shooting
  star, pin bar, morning / evening star, tweezer, outside bar, inside-bar breakout and fakey, rectangle breakout and
  failed breakout. Each is one line in the bullet catalogue marked "shadow": the engine records and grades it, but it
  never reaches your alerts, Telegram, Focus, AI notes, scorecard, conviction model or paper account. Live on the
  indices and the Lab's first 30 stocks. Results: **Scorecard → On trial**.
- **Strategy Lab:** 15 book setups (306 versions with every exit), e.g. pin bar with the trend at a level, engulfing at
  the 21 EMA or at a 50-61.8% pullback, inside-bar breakout / fakey, stars at the day's extreme, tweezers at
  yesterday's high / low, rectangle breakout with confirmation, failed breakout = stop and reverse. Tested on both
  indices and every F&O stock with history. Press **Test the strategy library** after updating (book setups show 📗).
  New Lab facts: 15 candle patterns, distance to the 21 EMA, distance to the nearest key level, level touch.
- **Chart page → All timeframes → strategy overlays:** every candle idea can be applied / removed on the chart (where it
  fired in the last 10 days, stop / target, how each ended).
- On synthetic data every book setup fails, as it should (random data has no edge); the real test is your 5-year data.
- Checked: 148 tests incl. no-clash and a new self-check (ideas on trial stay out of alerts / Telegram / paper); Lab
  regression 103 of 103 identical on old and rebuilt bar tables. Your next Lab run rebuilds the bar table once (new
  facts), which takes a few extra minutes.
- Later from the books: wedges, triple tops, gaps, and option structures (spreads, strangles) for the strategy desk.

## Options lens (2.26)

Trade today → **Options lens**: StockMojo-style tools built from the app's own minute-by-minute recording (no scraping,
no extra data plan), so every value is recorded and the Lab can test it later.
- Per symbol: **ATM straddle** through the day with its average, **call vs put premium** (which side is decaying),
  **put OI − call OI** change since the open (who is writing), **ATM IV and PCR** through the day, **OI by strike** now
  and since the open; straddle decay, max pain and the call / put walls.
- Whole market: **breadth** through the day (F&O stocks up / down, % above VWAP), **futures buildup** heatmap (long
  buildup, short covering, short buildup, long unwinding), **sectors**, **who is moving the index**, **FII / DII cash
  and participant positions**, and an **IV grid** of every F&O stock (IV, 15-min change, IV ÷ HV, PCR).
- While the scanner runs it shows today; otherwise the last recorded session. Refreshes every minute.

## All timeframes on the chart page (2.25)

Chart page → below the main chart, **All timeframes**: 1D, 75m, 1H, 15m and 5m side by side (one column on a phone).
- **Markings that suit each timeframe** (computed, the same maths as the scanner and the Lab; not drawn by AI):
  1D: previous day / week / month high-low, EMA 20/50/200, swing highs/lows. 75m and 1H: EMA 20/50, Supertrend, swings
  (HH/HL/LH/LL) and structure. 15m: VWAP, 30-min opening range, CPR, chart patterns. 5m: VWAP bands, EMA 9/21, 15-min
  opening range, OI walls.
- **Higher-timeframe levels** on lower charts (tagged "1D·PDH", "1H·Swing high"), **confluence zones** where 2+
  timeframes meet (table + orange lines), and a **trend strip** ("4 of 5 timeframes up", "all aligned").
- Less clutter: each chart shows only the levels near the price; a zone replaces the levels inside it. **Layers**
  turns each kind on or off.
- **Test a strategy on the chart:** pick a setup or a live Strategy Lab rule → **Apply**: every signal of the last
  5/10/20 sessions on the 5-minute panel and the main chart, with stop, target and how it ended (R), plus a score.
  **Remove** clears it. (Underlying moves only; the Lab tests options, costs and slippage.)
- 75m = five candles a day from 09:15 (the Indian standard); 1H and 75m are built from 15-minute bars.
- Data: Dhan candles (daily, 15-min, 5-min), cached in data/charts and refreshed at most every 30 minutes, plus
  today's live minutes; without Dhan the scanner's own history. Panels refresh as candles close (every minute).
- **AI read this chart** now also gets every timeframe's trend and the confluence zones.

## Bullets out of the engine: rules/ folder and the bullet catalogue (2.24)

- **rules/** (dated facts, plain JSON you can read): `events.json` (Budget, RBI policy, election days; now up to date
  with RBI's FY27 calendar: 8 Apr, 5 Jun, 5 Aug, 7 Oct, 4 Dec 2026, 5 Feb 2027), `costs.json` (charges, each entry valid
  from a date), `lots.json` (index lots), `stocks.json` (named stock lists). A broken file falls back to the built-in
  values and shows on the System page. Paper trades, the risk check and the Lab read charges from here.
- **live/registry.py** (the bullet catalogue): every alert / setup / pattern / strategy kind with its label, cooldown,
  auto-paper flag and display details. The detectors, chart scanner, paper account and screens read it; a new kind is
  one line there. System page → Bullets lists them all.
- Same values as before, so nothing moved: tests compare the old tables and charges with the new ones; Lab regression
  on a synthetic Lab: 103 of 103 rules identical. Only change in results: the Strategy desk's strict filter now also
  skips the 2026 RBI policy days (they were missing).
- "Any F&O stock" Lab rules firing on a stock the Lab never tested them on now say so in the alert ("tested on other
  F&O stocks, not on X"). Setting `lab.stock_rules_tested_only` (off by default) limits them to tested stocks.

## System & logins page (2.23)

Setup → **System & logins**: open it before the market opens.
- **Before the market opens:** Upstox logged in, Dhan token, TOTP, Claude, Telegram, disk, no clashes: ✓ / ✗ / –.
- **Every connection** (Upstox, Dhan, Claude, Telegram, Yahoo, NSE): status, what it is used for, and its logins and
  buttons in one place (Log in with Upstox / Log out; Dhan client ID, token, TOTP on/off, Test, Renew; Test Claude;
  Telegram test message).
- **Run live check:** one small call to Upstox and Dhan (no AI message, no Telegram message).
- **Which source does what**, with each one's limits; **background jobs**; **no-clash checks** (the same checks the
  tests run, live/selfcheck.py); **problems in the last hour** grouped from the app log.

## One Dhan token, one keeper (2.22.1)

All ways of getting a Dhan token (pasted token, Log in with Dhan, Renew, TOTP) go through one keeper, one at a time;
whichever token was saved last is the one every part of the app uses (download, order book, news, movers switch to it
on their next call). Order: renew ~3 h before expiry; if the token is gone, TOTP makes a new one (at most every 5 min).
If Dhan refuses a token TOTP made minutes ago, the app does not make token after token: it says to check the Data API
plan and tries again after an hour. Tip: let the app manage the token; a token made elsewhere for the same account
can replace the app's (Dhan keeps one active), and the app then simply makes a fresh one.

## Dhan automatic login with TOTP (2.22)

The app makes a new Dhan token by itself whenever the old one is gone (Mac was off, token expired, Dhan refused it),
using Dhan's documented Generate Token API (client ID + PIN + TOTP code). No daily step at all.

One-time setup:
1. web.dhan.co → Profile → DhanHQ Trading APIs → **Set-up TOTP** → Add TOTP. Scan the QR in Google Authenticator
   (or any authenticator) **and** copy the text key shown under the QR. Confirm with the first 6-digit code on Dhan.
2. Strategy desk → Dhan keys → Automatic login (TOTP): enter the Dhan PIN and the text key → **Turn on automatic
   login**. The app checks them with Dhan first (and makes today's token); only then are they kept.

Safety:
- PIN and key are kept in the **macOS Keychain** (encrypted, tied to your Mac login), never in user_settings.json,
  the logs, the browser or git. They go only to auth.dhan.co over HTTPS; error messages never contain them.
- A wrong PIN / key stops automatic login at once and says so (app + Telegram): no retry loop that could lock your
  Dhan account. It restarts when you save them again. At most one automatic login every 5 minutes.
- The token is used for market data only: the app has no order / position / funds / holdings calls (enforced by
  tests/test_safety.py). **Turn off** removes the PIN and key from the Keychain.
- Keep the Mac's own login password on, and FileVault on (System Settings → Privacy & Security).

## Log in with Dhan (2.21.1)

Dhan tokens last 24 hours and an expired one can't be renewed (the Mac was off). For a lasting setup, use Dhan's
API key + secret (valid 12 months):
1. web.dhan.co → Profile → DhanHQ Trading APIs → switch to **API Key** → name `Stockway`, Redirect URL
   `http://127.0.0.1:8765/dhan/callback` → Generate. Copy the key and the secret.
2. Strategy desk → Dhan keys → paste client ID, API key, API secret → Save.
3. **Log in with Dhan**: Dhan's own login page opens (password / OTP / PIN go to Dhan only); the app gets the token
   and saves it. While the app is open it renews the token by itself; if the Mac was off for a day, one click on
   Log in with Dhan (the Telegram note and the 5-year panel link to it).
- **Renew now** checks that renewing works with your token.
- The app never stores your Dhan password, PIN or TOTP. Market data only.

## 5-year download that completes itself, and a sturdier engine (2.21)

**Download (Strategy Lab → 5-year Lab)**
- Speed is set by Dhan: 5 calls a second and 100,000 a day on the Data APIs; one call = one strike, one side, one
  trading day. The download now uses up to 92,000 calls a day (the rest stay for live news and movers) at the full
  shared speed, and starts again by itself after midnight.
- Nothing is missed: trading days come from Dhan's own Nifty candles (no guessed holidays); a manifest records every
  day on disk; each run fetches exactly the missing days (whole months, holes inside months, the newest days). A
  network error never counts as "no data": after 3 in a row the download pauses and retries the same day. Each
  finished day is saved at once, so closing the app or a sleeping Mac loses at most one day in progress.
- It resumes by itself whenever the app is open (after a restart, a network drop, a new day or a new token). A Stop by
  hand is respected for the rest of the day.
- Universe: Nifty, Bank Nifty and every NSE stock with options; the 30 most liquid first, top 80 by turnover ATM±3,
  the rest ATM±1. It pauses below 10 GB free disk.
- The panel shows the whole picture: months complete / partly on disk / pending, trading days and calls left, Dhan
  calls used today, token validity, disk free. The bar is overall progress (it no longer restarts at 0%).
- All Dhan clients share one speed limit and one daily counter, so the download and live news / movers never push
  each other over Dhan's limits. Upstox stays the live price feed; the two don't overlap.
- An expired Dhan token is remembered: news and movers go quiet (no log line every minute), a Telegram note goes out
  once, and everything resumes by itself when a new token is pasted.

**Engine (gun) armour**
- One alert, setup, stock or Strategy Lab rule that fails is skipped and logged (at most every few minutes); the live
  scanner keeps running. Paper updates, big prints, VIX checks and the market view are guarded the same way.
- Strategy Lab: one research round per Lab folder at a time (a manual run waits for the nightly one instead of
  overwriting it); results and the bar table are saved atomically.
- Simulator on a weekend: the risk check no longer blocks every paper trade as "market closed".
- Quieter window: yfinance's "possibly delisted" lines during a network blip are hidden; a repeated AI error is
  logged once per 30 minutes.

## Dhan token renews by itself (2.20.1)

Dhan Web tokens last 24 hours. While the app is open it now checks the token every 10 minutes and, about 3 hours
before it expires, calls Dhan's Renew Token API (Authentication → Access Token → Renew Token), saves the new token and
every running Dhan connection (5-year download, order book, news, movers) switches to it on its next call. The Dhan
keys box shows "valid till … · last renewed …".

- No PIN, TOTP or password is stored or used: only the token you pasted.
- An expired token can't be renewed. If the Mac or the app is off for more than a day, make one new token on
  web.dhan.co, paste it once, and it renews by itself from there.
- Market data only: renewing the token doesn't touch orders, funds or holdings.

## Dhan Pro, checked against Dhan's own docs (2.20)

- **5-year download fixed to Dhan's real limits:** one trading day per call, 5 calls a second, 100,000 a day. The app
  spends up to 90,000 a day (10,000 stay free for live news / movers), skips holidays after one call, and continues by
  itself every evening until done: Nifty + Bank Nifty in about a day, the 30 stocks over about 5 more.
- **Dhan news headlines** (every minute in market hours) join the news feed with Dhan's own stock tags, so news alerts
  and the "news with / against" fact see them sooner.
- **Dhan market movers** every minute: stock options / futures by OI gainers and volume, F&O stocks by price. The Live
  page shows the names in play; rank, lists and direction are new conviction-model facts.
- **200-level order book:** packet format confirmed (41 = buy side, 51 = sell side, little-endian).
- Not used on purpose: Dhan technical data (no history, so it can't be backtested; the Lab computes the same
  indicators itself) and the CAS / portfolio feeds (your real account).

## Dhan Pro data: 5-year Lab and the 200-level order book (2.19)

Needs Dhan's Data API (Pro, ₹999/month): client ID + access token on Strategy desk → Data and settings. Market data
only: no orders, no account or holdings data (Dhan's CAS feed is deliberately not used).

- **5-year Lab (challenger)**: Strategy Lab → 5-year Lab → Download 5 years. Dhan's expired-options archive (1-minute
  from 2021; Nifty / Bank Nifty ATM ±10, 30 stocks ATM ±3), only the months before the Upstox history, saved in
  data/lab5y with the Upstox files linked in. Resumable, newest month first. "Run 5-year research" runs the same search,
  library, ₹40K finder, exit board and model strategies there; the panel compares it with the current Lab. The current
  Lab, its results and its live rules are not touched; switching is your call. It also runs every night once it has data.
  Stock entries are every 15 minutes there and numbers are 32-bit, to fit 5 years of 30 stocks in memory.
- **Order book (Dhan 200-level)**: Nifty and Bank Nifty futures, live from the market open: % of quantity waiting to buy
  near the price and the biggest walls, on the Live page index cards. A row per minute is saved to data/depth (past
  order books can't be bought, so recording starts now), and the imbalance is a new fact for the conviction model.
- Next with the Pro plan: Dhan news headlines and market movers into the alerts (after checking their API with your token).

## Pro stop-loss / targets, the ₹40K finder and several models (2.18)

- **Pro plan on every alert (testing):** entry, stop-loss and target set on the chart the way well-known traders do,
  plus the option's estimated SL / target, max loss per lot and whether it fits ₹40K (lot ≤ ₹10K, loss ≤ ₹2,000).
  Four styles (lab/exits.py): ATR 2R (Van Tharp R-multiples + Wilder ATR), signal candle 2R (Linda Raschke /
  Al Brooks), breakeven 3R, Chandelier trail (Chuck LeBeau). The one shown is the style with the best worst case over
  validation and holdout. Paper trades keep the current exits (champion) until a style wins.
- **Champion vs challengers, on history and live:** the Lab re-runs the best rules with every exit (Strategy Lab →
  Stop-loss & target), and every live alert's option is followed in shadow under the current exit and all 4 styles
  (Scorecard → Stop-loss & target styles on live alerts). First result (2 years, Nifty + Bank Nifty): no chart style
  beats the current % exits out of sample yet; on random entries every exit loses (−₹208 to −₹261 a trade).
- **Start with ₹40K (Strategy Lab):** every option-buying rule re-tested only on lots ≤ ₹10K, chosen on validation,
  judged on the holdout, with a ₹40K account run through it (end balance, worst day, deepest fall). Index and stocks.
  First result: 10 of 43 rules that made money on validation also made money on the holdout; none with strong evidence.
- **Several models:** the conviction model now compares gradient-boosted trees (champion), logistic regression and a
  random forest on the same unseen days; a challenger replaces it only with 0.02+ better AUC. The Lab also trains
  model strategies (3 models × 4 exits × 4 score cut-offs × calls / puts) with the same train / validation / holdout
  test; ones that pass fire live on paper. First result: 0 of 96 passed.
- Lab rules can now use the chart-level exits; their paper trades follow the chart stop / target (paper only).
- Regression check: the 594 saved Lab results are identical after rebuilding the bar table with this code.

## Stock options and the conviction model (2.17)

Aim: start with ₹40K buying options on stocks as well as indices, with alerts that weigh everything at once.

- **Conviction score (0-100) on every alert**, from a model that learns which alerts are worth an option buy. It looks
  at the stock (move, VWAP, futures buildup, order flow, unusual options, IV), the option you'd buy (price per lot,
  bid-ask spread, delta) and everything around it: Nifty, breadth, the stock's sector, global markets, VIX change and
  news from the last hour (news is now checked every minute in market hours). Trained on earlier days, judged on the
  latest days it never saw; **trusted only with 5+ days and a holdout AUC of 0.60+**. Until then the score is hidden
  and Focus works as before; once trusted, Focus = conviction 85+. Scorecard → Conviction model shows its state and
  what drives it.
- **₹40K chip / filter**: alerts whose option lot costs ≤ ₹10,000 (fits a ₹40K account). "₹40K only" on the alerts list.
- **Context chips** on alerts: Nifty, sector, global, breadth and news, each with or against the trade.
- **Stock option history** (Strategy Lab → Stock options data → Download 12 months): 1-minute option prices of 30
  liquid F&O stocks from Upstox Plus, newest month first. 6-8 hours (Upstox's rate limit), in the background, resumable,
  and each new month is added by itself after the close. Market data only.
- **Strategy Lab on stocks**: once downloaded, the Lab tests rules on "any F&O stock" (one trade at a time, like a
  ₹40K account; the earliest signal of the day wins), with stock slippage (wider spreads). Promoted stock rules scan
  every F&O stock live and paper-trade like the index ones. No stock straddle selling (margin in lakhs).

## Strategy library, indicator settings and option selling in the Lab (2.16)

- **Strategy library** (lab/library.py, "Test the strategy library" button): 16 well-known setups in our own words with
  sources — first 5-min candle breakout ("9:20 setup" popular with Indian option buyers such as Ghanshyam Tech), 2:20
  candle, Crabel opening-range breakout (15/30 min), Supertrend + VWAP (10,3 / 7,3 / 10,2), fresh Supertrend flip,
  Raschke's Holy Grail, Connors' RSI(2), Bollinger squeeze and band fade (2.0 / 1.5 sd), VWAP bands, MACD + EMA, EMA 9/21,
  Ochoa's narrow CPR, previous-day high/low breakout, the 9:20 short straddle (25/35/50% stops, 10:30 variant) and a
  late-morning quiet-day straddle / iron fly. Every version × every exit is tested like any rule.
- **Indicators with their usual settings** added as facts: Supertrend (3 settings), Bollinger %B and width (2 settings),
  VWAP bands (z-score), MACD 12,26,9, ADX/DI 14, RSI 2/7/14, EMA 5/13 and 9/21, SMA 20, CPR width and position, first-candle,
  30-min and 14:20-candle ranges. Computed within the day (as the live app does), so long settings start later in the day.
- **Option selling** in the Lab: ATM straddle with a stop per leg (25/35/50%) to 15:15, and an iron fly (wings 4 strikes
  away, ~₹40-60K margin) with a loss stop on the credit. Promoted selling rules alert and paper-sell both legs when the
  paper account is ₹1.5 lakh or more.
- **Live by evidence**: promoted rules trade on paper by themselves only with moderate/strong holdout evidence (holdout
  t ≥ 1), or when you tick them.
- **Result (2 years, 48,019 rules)**: all 16 famous setups fail (the 9:20 straddle made +₹1,738/lot a trade in 2024-25,
  then lost in 2026, as reported when it got crowded). Random option buys lose ~₹220/trade, random straddle sells ~₹160,
  random iron flies ~₹590 (four legs of costs). The strongest found: selling the ATM straddle (25% leg stops) when
  +DI − −DI > 22 and the day's range is still under 0.82% — positive in all three periods, holdout 88 trades, +₹492 avg,
  t 3.5, 74% green days, worst day −₹3,657; it needs straddle margin (~₹1-2 lakh), not ₹40K.

## Strategy Lab: thousands of rules, ebooks and Claude, tested honestly (2.15)

- **What it tests**: intraday rules that buy the at-the-money Nifty / Bank Nifty call or put one minute after a 5-minute
  bar closes, on ~20 facts known at that moment (moves, VWAP, EMAs, RSI, opening range, yesterday's high/low, gap, range
  expansion, VIX, ATM IV and its change, priced move, days to expiry, weekday), with five exit rules. Per lot, real
  charges and slippage, on the 2-year 1-minute option history. ~7 ms per rule, so 10,000+ rules per run.
- **Where rules come from**: a random search plus mutations of the best; **your ebooks** (PDF / EPUB / TXT on the Strategy
  Lab page; Claude reads the rule-heavy passages, turns concrete rules into testable ones and lists what it can't test
  yet); **Claude's own ideas** from what has worked and failed so far.
- **The guard**: train (first 60% of days) → validation (next 20%) → holdout (last 20%, never used to choose). Promoted =
  passed all three; evidence strong / moderate / weak by the holdout t-stat. The page shows how many rules were tried and
  how many would pass by luck, and hides nothing that failed.
- **Small-account accounting**: 1 lot, the day stops after a loss, so green-day % and worst day are what a ₹40K account
  would have lived through. Paper page: **Small account (₹40K)** preset (1 lot, max 2 trades/day, stop after one loss,
  lock the day at +₹1,200).
- **Live**: promoted rules (switched on) fire as "Strategy Lab" Act-now alerts on the bar that closes, go through the risk
  check, and are paper-traded with the rule's own exit (including the trailing exit). A rule that loses money over 15
  live paper trades is switched off automatically.
- **Every evening (16:15)**: new expiries are fetched (when logged in), losing live rules are switched off, Claude adds a
  few ideas, and a research round runs.
- **First result on real data (30 Oct 2026)**: 23,000+ rules tried; random entries lose ~₹220/trade; 3 rules promoted
  (expiry-morning ATM put buys), all with weak holdout evidence and fewer than the ~43 expected by luck. No rule has a
  proven edge yet; the 3 trade on paper only to build a live record.

## Paper account, watchlist and trading from the chain and chart (2.14)

All of this is paper: nothing reaches a broker, ever.

- **Paper account**: starting capital, account value now, today's P&L (booked + open), free cash, money in open
  positions, all booked P&L. The side bar shows today's P&L next to "Paper trades" (green / red), every 5 seconds.
- **Close all** button; each position shows its stock's live price, today's % and the move since entry, with links to
  its chart and option chain. Closed trades show the stock's entry → exit price.
- **Option chain B / S**: paper Buy or Sell any strike at the live price (Lots box above the chain). Sell closes a
  position you hold, otherwise opens a paper short (margin estimated at 15% of the underlying value).
- **Chart**: Paper BUY (at-the-money call) / Paper SELL (at-the-money put) buttons; Focus alerts are marked BUY / SELL
  on the chart and listed in "Trade signals", each with a paper-trade button.
- **Aggressive auto trading** switch (Paper page): the system paper-trades every up/down idea (Important and
  Act-now), even when the risk check blocks it, up to 15 open, 09:16–15:15. Tagged "aggressive" in reports.
- **Alerts**: live price, today's % and the move since the alert on every card; the Paper trade button disappears
  once an alert is 5 minutes old (the server refuses it too).
- **Watchlist** page: your symbols plus up to 8 smart picks (Focus alerts in the last 90 minutes, then the most active
  F&O stocks), with price, 5/15-min move, VWAP, volume pace, futures buildup, IV/PCR, range, support/resistance, bias,
  last alert, and B / S paper buttons.
- **Scanner Simple view** (default): Bullish / Bearish / Mixed plus one plain sentence per stock; "How to read this".
- **Live page**: Sensex (live from Upstox, else Yahoo) and GIFT Nifty (near-month future from NSE IX's public site).
- **Market brain** writes a fresh rule-based note every 5 minutes between Claude's hourly notes.
- **World**: all markets refresh every 30 s in about 2 seconds (Yahoo chart endpoint in parallel); sparklines show
  today against the previous close (dashed), coloured like the % change; previous closes from the daily table (Yahoo's
  own is sometimes a day stale for Indian indices).

## Discipline, Focus and the day report (2.13)

Built from the first live day (30 Oct 2026: 27 paper trades, 24 of them against the risk check, net −₹27,670; the 3 it
passed made +₹948; 3,584 alerts):

- **The risk check binds paper trades.** Clicking Paper trade re-checks the idea at that moment. Hard rules cannot be
  broken, even by hand: market not open / before 09:20 / after 14:45, weekend, F&O ban, already holding the symbol,
  daily loss limit, over-trading limit, and a 30-minute cool-off after two stop-losses in a row. Soft reasons (reward:risk,
  spread, cheap option, losing history, size) can be overridden with a confirmation: 1 lot, tagged "override", reported apart.
- **Focus feed (default).** Act-now alerts that are not demoted, at most one per symbol per hour. Pop-ups, sounds and
  Telegram follow Focus. Everything is still recorded and graded; "All" shows it. On day 1, Focus alerts worked 78% of
  decided cases (option +6.4% after an hour) vs 64% (+1.0%) for all alerts.
- **Scorecard counts decided cases.** Hit rate = option +20% first vs −20% first, among alerts that reached either within
  an hour; flat hours no longer count as failures. Alert types under 45% over 20+ decided cases, or losing over 2% of the
  option on average over 40+ alerts, are demoted (out of Focus, no Telegram, no auto paper trade).
- **Day report** (Briefs page, also in the post-market review): paper P&L, PASS vs blocked vs override, entries outside
  the window, exits, best/worst trades, Focus vs all alerts, best/worst alert types, and lessons for tomorrow.
- **Claude's allowance goes where it matters.** On the Pro plan, automatic "Why?" notes only for act-now alerts the risk
  check passed, one at a time, with 8 calls kept for the hourly market notes.
- Fixed: the post-market review is filed under the session it describes (not the next morning's date); simulator
  notes and reviews never mix with real-market ones; notes made while the market is closed say so.

## Strategy desk (aim: a positive average day)

Institution-style intraday strategies on Nifty and Bank Nifty, every one with a capped worst case:

| Strategy | When it earns |
|---|---|
| Iron fly (sell ATM straddle + wings, a stop on each side) | Calm days: options lose value faster than the index moves |
| Iron condor (sell OTM call + put + wings, combined stop and target) | Range days |
| Expiry-day iron condor | Expiry days, when time decay is fastest |
| Opening-range breakout debit spread | Days that break out after 9:30 |
| Trend-day debit spread | Clean one-way days |
| Chart setup debit spread | Four of the chart setups below (not OI-wall rejection: no OI in history), same code as the live scanner |

How it decides, the way a desk does:

1. **Test on real history.** Upstox Plus expired-options data (or Dhan's Data API): 1-minute prices and OI
   for at-the-money ±10 strikes of the nearest expiry, held at fixed strikes exactly as a position would be.
   Implied volatility is computed from the prices (Black-Scholes).
   All charges (brokerage, STT 0.15%, exchange, SEBI, GST, stamp) and slippage are included.
2. **Walk forward.** About 100 variants (entry time, strike distance, stops, filters). Each month, only the
   previous 6 months are used to choose; only the following month counts. A variant must have enough trades,
   a profit factor of 1.15+ and profit about 2 standard errors above zero. Otherwise the desk stays in cash.
3. **Size and guard.** Lots are sized so that a trade hitting its stops loses about 1.5% of capital, with a hard
   cap on the theoretical worst case and on margin. Everything closes for the day after a 2% loss.
4. **Paper trade the plan** live every day (same rules as the backtest) and compare with the backtest.

**Setup report card.** The backtest also replays every live chart-setup signal on the same 1-minute option
history (buy the ATM option, exit at the setup's target/stop or after 90 minutes, all costs) and shows, per setup,
signals, win rate, average ₹ per lot and whether it beats luck. Live Nifty/Bank Nifty setup alerts quote this record;
setups that historically lose become info-only (no paper trade). An AI filter (gradient boosting on ~25 facts known
at the signal) is tested month by month on unseen months and is used live only if it passes.

**Days like today.** On top of the 10-year end-of-day test (iron condor / iron fly / straddles held to expiry), the
desk finds the 20% of past entry days that looked most like the latest close (India VIX level and direction, trend
vs the 20-day average, the day's move, how rich options are vs recent moves) and shows how each strategy and entry
timing did on those days, next to all days and next to the strict walk-forward's own picks, with the next entry date.
It refreshes itself after each new end-of-day file. History, not a forecast; paper only.

Setup: log in to Upstox (Plus plan) on Live setup → Strategy desk → Check access → Download history (about
1–1.5 hours per year of history, resumable; data starts 2022) → Run backtest. Re-run monthly.
Dhan works too (Client ID + token under "Or use Dhan instead").

## Chart, world monitor and AI brain

**Chart** (TradingView Lightweight Charts): 1/3/5/15-minute candles for any index or F&O stock with
VWAP + bands, EMA 9/21/50, Supertrend, RSI, MACD, volume, and every level the desk uses drawn on the chart:
yesterday's high/low/close, CPR and pivots, opening range, call/put OI walls, max pain, gamma flip and walls,
and the option-implied expected move. Alerts, setups and paper/desk trades appear as markers (hover for details).
The side panel gives a plain-English chart read, today's setups with live state, and the levels by distance.

**Chart setups** (checked on every closed 5-minute bar, each with a stop and a target from real levels):
EMA pullback in a trend, VWAP reclaim/loss, narrow-CPR breakout, previous-day high/low break, OI-wall rejection.
They fire as alerts, are graded on the Scorecard, and are backtested in the Strategy desk.

**Scenarios** (Nifty, Bank Nifty): today's session so far is matched against every past session since 2022
(1-minute, scaled to each day's normal volatility, plus gap, VIX and range). The 40 most similar days show what
happened next: probabilities of up / sideways / down for 5, 15 and 60 minutes, an 80% range in points, and three
projected paths drawn on the chart. The same method is replayed on the last 250 sessions without peeking, and the
panel shows its real track record: so far the size of the move is useful, the direction is close to a coin toss.
The 1-minute history downloads by itself after an Upstox login.

**Chart patterns**: swing points (HH / HL / LH / LL) and market structure, plus double tops/bottoms, head &
shoulders, triangles, flags, breakout + retest, RSI divergence and reversal candles at key levels, each with
entry, stop and target, drawn on the chart. Every pattern is replayed on the 1-minute index history and alerts quote
its record ("491 times, 41% won, avg −0.00R"). The status bar shows Upstox's data delay and analysis time.

**Risk gate**: every alert's trade idea gets PASS or BLOCK with the reason before it can become a paper trade:
entry window (09:20 to 14:45), F&O ban, an open position on the same stock, daily loss and trades-per-day limits,
historically losing signal types, reward:risk under 1.2, bid-ask spread over 3%, options under ₹3, and lots sized so
the stop loses at most your max loss per trade (if one lot is already too much, it is blocked, never rounded up).
A reminder five minutes before the 15:15 square-off lists what is still open.

**Time Machine** (its own tab under Test & research; any index or F&O stock): type a plan (direction, entry,
stop, target, entry time) or pick one of today's alerts. The rest of the day is replayed 10,000 times from the
symbol's own last 60 sessions of 5-minute bars (same time of day), and the same plan is run on each real past
session. You get target first / stop first / neither by 15:15, the break-even check for that reward:risk, a price
fan to the square-off, and your recent tests. History comes from Upstox when logged in, otherwise free from Yahoo
Finance, so it works with the market closed too. The "Time Machine" button on any alert opens this tab with the
alert's plan. Idea from the MIT day-trader-skill, rebuilt for NSE hours.

## Pages

The menu is grouped: **Trade today** (Live, Ask Stockway, Alerts, Chart, Option chain, Scanner, Paper trades), **Test & research**
(Time Machine, Strategy desk, Scorecard), **Market intel** (Market context, World, Insider trades, Briefs),
**Daily radar** and **Setup**.

| Page | What it answers |
|---|---|
| Ask Stockway | Ask about the market right now (English or Hinglish); answered by Claude from the app's live data. |
| Time Machine | Test any trade plan before taking it: 10,000 replays plus the real past, a plain "what to do", how far price usually moves after that time, and "Find a better stop & target" (24 plans ranked). |
| Chart | TradingView-style chart with indicators, levels, setups and markers, plus a chart read. Before the open or with the scanner off it shows the last session (saved history or Yahoo 5-minute bars). |
| World | Global markets, forex, commodities, rates, ADRs, economic and results calendars, global news. |
| Insider trades | Promoter / director buying and selling in F&O stocks, net by stock, every disclosure. |
| Strategy desk | Step-by-step research checklist and "what passed, in plain words", then today's plan and positions, paper P&L by day, out-of-sample backtest results, history download (Upstox Plus or Dhan). |
| Live | Sensex, Nasdaq, Dow and S&P strip, index cards (PCR, max pain, support/resistance from option OI, IV, expected move, bias), live alerts with option ideas, market pulse, movers, news. |
| Scanner | Every F&O stock ranked by activity, with buildup, volume pace, IV, PCR, unusual options, bias. Filters and sorting. |
| Option chain | Pick any index or F&O stock from the dropdown: full chain with OI bars, OI change, IV, greeks, walls, max pain, intraday chart. |
| Market context | FII/DII/Pro/Client positioning, cash flows, ban list, big deals, company filings, global markets. |
| Briefs | Pre-market plan and post-market review. |
| Alerts | Every alert saved by day. |
| Scorecard | Which alert types actually work, sorted into Trust / Keep watching / Ignore, graded automatically. |
| Paper trades | Automatic and manual paper trades, P&L after charges, win rate by alert type. |
| Live setup | Upstox keys and login, alert levels, Telegram, AI, paper trading settings (one capital for the whole app). |
| Tomorrow | Will the next session be a big-move day? Expected range in points, direction lean, scheduled events, why the model thinks so, and how your recent calls turned out. |
| History | In plain words: how many big days the radar caught and how much better than guessing, the last 15 days' calls, and every big day on the chart. |
| Biggest days | The 60 widest-range days in 10 years, and whether the model saw them coming. |
| What drives moves | How often a big day followed when each factor was high vs low. |
| Model check | Honest scores on years the model never saw, plus an option-buying proxy test. |
| Data & settings | Train, add headlines or your own events, set auto-forecast time and Telegram alerts. |

## Automatic runs

While the app is open, it updates the forecast every weekday at your chosen time (default
6:30 PM) and retrains every Saturday. Add a Telegram bot token and chat ID to get each forecast
on your phone.

## How it works

- **Big move** = the day's high–low range is in the top 20% of the previous year (adjustable).
- **No look-ahead:** each forecast uses only what was known before 9:15 AM that day.
- **Walk-forward testing:** for every year from 2019, the model is trained only on earlier years
  and scored on that year.
- **Model:** gradient boosting (scikit-learn). Drivers show how much each factor moved the
  probability compared with a typical day.

Data sources: Yahoo Finance (daily prices), GDELT (news tone and volume since 2017), optional
India News Headlines CSV, optional events CSV (`date,event`).

## Limits

1. Daily data only. Intraday timing needs paid 1-minute history (e.g. Kite Connect).
2. The option test is a proxy based on India VIX, not real option prices.
3. Yahoo's Indian index data can have bad candles on some dates; the app warns you.
4. Direction is close to a coin flip. Use the big-move flag to decide whether to trade, and your
   own setup to decide which side.

## Command line (optional)

```bash
.venv/bin/python run_all.py --no-news     # train
.venv/bin/python predict_today.py         # forecast
```

Settings live in `config.yaml`; changes made in the app are saved in `user_settings.json`.
