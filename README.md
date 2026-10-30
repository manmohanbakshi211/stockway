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
