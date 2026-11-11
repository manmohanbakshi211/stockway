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
