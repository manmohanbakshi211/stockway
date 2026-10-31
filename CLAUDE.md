# Stockway: rules for anyone (or any AI) changing this code

- **Never place real orders.** No order, modify or cancel calls to Upstox, Dhan or any broker; no algo trading;
  no access to funds, positions or holdings of a real trading account. Broker APIs are used for market data only.
- The app's job: clear entry/exit alerts (what, when, why, stop, target) and automatic **paper** trades at the same time.

## Don't break what works (Mandeep, 30 Oct 2026)

"System strong aur best banana hai, jo achha chal raha hai use change nahi karte jaana; bahut testing/trials karne hain."

- **Add, don't replace.** A new idea ships next to the current logic, never over it. It starts switched off or in
  shadow mode (computes and records, changes nothing the user sees or trades) until it beats the current version on
  data it never saw. Example: the conviction model stays hidden until trusted (5+ days, holdout AUC 0.60+).
- **Champion vs challenger.** Replace a working rule, filter, exit or model only when the new one is better on the
  same out-of-sample data (Lab holdout or graded live alerts), and say by how much in the commit and README.
- **Results must not move by accident.** Before every release run the full tests and the Lab regression check:
  rescore every saved rule (data/lab/factory.json) on the same bar table (data/lab/factory_bars.parquet) with the
  new code; every train / validation / holdout result must be identical unless the change was meant to alter them.
  (2.17: 594 of 594 identical.)
- **Experiments are kept, not overwritten.** Lab runs, ebook rules, Claude ideas and live paper records accumulate;
  a failed trial is recorded as failed, not deleted.
- Defaults that work (risk check, Focus, ₹40K preset, Strategy Lab promotion bar) change only on Mandeep's say-so.

## Gun and bullets (Mandeep, 31 Oct 2026)

"Gun aur bullet wala scene": the technical core is the gun; everything that changes or gets tested is a bullet.

- **Gun (stable, tested, rarely touched):** data loading, bar tables, backtest/live engine, risk check, paper trading,
  costs maths, scoring/holdout, alerts, shadow grading, app UI. It knows nothing about any one strategy, style, date or lot size.
- **Bullets (dynamic, added all the time):** strategies, entry/exit styles, indicators, ML models, event / expiry
  calendars, lot sizes, cost rates, stock universe, thresholds. They live in registries or data files (with a valid-from
  date), never as constants buried inside engine code.
- **A bullet can never break the gun.** The gun loads each bullet in isolation: a bad or failing bullet is skipped,
  logged and marked failed, and everything else keeps running. A new bullet starts in shadow mode (off for the user).
- **Adding a bullet must not need an edit to gun code.** If it does, that is a bug in the gun: fix the registry.
- **Facts that change over time (lot size, expiry day, STT, events) are dated data, not code.** Old dates keep their old
  values so past backtests stay identical (Lab regression check: 594/594).
- Every change keeps the stability rules above: add don't replace, shadow first, full tests + tools/lab_regression.py.
