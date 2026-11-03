# Stockway

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
