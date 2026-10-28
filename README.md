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

Each alert suggests the at-the-money option in its direction. Strong alerts are paper-traded
automatically (1 lot, +30% target, −20% stop, exit by 3:15 PM, real charges). The **Paper trades**
page shows which alert types actually make money. Use that before risking real money.

Optional: a free Google Gemini key (https://aistudio.google.com/apikey) adds a written market pulse
and a "Why?" answer on alerts. Only prices and public headlines are sent.

Costs: Upstox data is free with an account. Gemini free tier is free. Telegram is free.

## Pages

| Page | What it answers |
|---|---|
| Live | Index cards (PCR, max pain, support/resistance from option OI, IV, expected move, bias), live alerts with option ideas, market pulse, movers, news. |
| Scanner | Every F&O stock ranked by activity, with buildup, volume pace, IV, PCR, unusual options, bias. Filters and sorting. |
| Option chain | Full chain for any index or stock with OI bars, OI change, IV, greeks, walls, max pain, intraday chart. |
| Alerts | Every alert saved by day. |
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
