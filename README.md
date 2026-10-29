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

Optional: a free Google Gemini key (https://aistudio.google.com/apikey) adds a written market pulse
and a "Why?" answer on alerts. Only prices and public headlines are sent.

Costs: Upstox data is free with an account. Gemini free tier is free. Telegram is free.

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

**Time Machine** (any index or F&O stock): replays the rest of the day 10,000 times from the symbol's own last
60 sessions of 5-minute bars (same time of day), plus the same plan on each real past session, and gives the odds
of target first / stop first / neither and an honest verdict that weighs the reward:risk. Idea from the MIT
day-trader-skill, rebuilt for NSE hours and Upstox data.

**Insider trades**: promoter, director and key-manager trades disclosed under SEBI's insider-trading rules, for
every F&O stock (₹50 lakh+ in the market), net by stock, with alerts for new ones over ₹1 crore. The chart page
shows each company's last quarterly results (from NSE's integrated filings) and its insider trades. Since May 2026 NSE
publishes these as XBRL filings; the app reads each filing once (first read about 10 minutes, then only the
last ten days every 3 hours).

**10 years of end-of-day data (NSE bhavcopy, free)**: every index option contract's daily prices since 2015.
Tests hedged premium selling held to expiry (iron condor / iron fly) and buying straddles, walk-forward by year,
plus the volatility risk premium year by year. The **Expiry desk** paper-trades the variant that passed for this
year, entered near the close and settled at expiry.

**World**: global index futures, currencies, commodities, US yields, VIX, crypto and Indian ADRs (vs NSE price),
each with its past correlation to Nifty and an indicative "global pressure" on Nifty; this week's economic
calendar, F&O results for 10 days, and global news tagged by likely impact on India. Big moves (crude, dollar,
US futures, Asia) and high-impact events 15 minutes ahead come as alerts.

**AI market brain**: every few minutes a desk note — bias with confidence, key levels, risks, watchlist and
what changed. Works on rules with no key; written by AI when a Gemini key is set.

## Pages

| Page | What it answers |
|---|---|
| Chart | TradingView-style chart with indicators, levels, setups and markers, plus a chart read. |
| World | Global markets, forex, commodities, rates, ADRs, economic and results calendars, global news. |
| Insider trades | Promoter / director buying and selling in F&O stocks, net by stock, every disclosure. |
| Strategy desk | Today's strategy plan and positions, paper P&L by day, out-of-sample backtest results, history download (Upstox Plus or Dhan). |
| Live | Index cards (PCR, max pain, support/resistance from option OI, IV, expected move, bias), live alerts with option ideas, market pulse, movers, news. |
| Scanner | Every F&O stock ranked by activity, with buildup, volume pace, IV, PCR, unusual options, bias. Filters and sorting. |
| Option chain | Full chain for any index or stock with OI bars, OI change, IV, greeks, walls, max pain, intraday chart. |
| Market context | FII/DII/Pro/Client positioning, cash flows, ban list, big deals, company filings, global markets. |
| Briefs | Pre-market plan and post-market review. |
| Alerts | Every alert saved by day. |
| Scorecard | Which alert types actually work, graded automatically, plus the learning model's status. |
| Paper trades | Automatic and manual paper trades, P&L after charges, win rate by alert type. |
| Live setup | Upstox keys and login, alert levels, Telegram, AI, paper trading settings. |
| Tomorrow | Will the next session be a big-move day? Expected range in points, direction lean, scheduled events, why the model thinks so, and how your recent calls turned out. |
| History | Every big day on the chart: flagged, missed, or false alarm. Click any point for that day's story. |
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
