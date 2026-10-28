# Bank Nifty Radar

A Mac app (runs in your browser, on your own computer) that learns from 10+ years of
Bank Nifty prices, India VIX, global markets, scheduled events and news, and tells you every
evening whether **tomorrow is likely to be a big-move day**.

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

## Pages

| Page | What it answers |
|---|---|
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
