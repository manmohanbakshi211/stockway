# Stockway: rules for anyone (or any AI) changing this code

## 13 Nov 2026: the legacy app is frozen; all new work is in tanu/
Mandeep: "ab pura manu ka system tanu se replace karna hai ... aaj se samajh lo manu khatam tanu shuru".
- live/, lab/, webapp/, radar/ ("Manu") get **no new features or patches**. They keep running on the Mac (paper +
  recordings) only until Tanu's cutover (tanu/docs/adr/0002-replace-legacy-with-tanu.md).
- All new work goes in **tanu/**, under **tanu/CLAUDE.md** and tanu/docs/. No imports between tanu/ and the legacy
  code either way; Tanu writes only under data/tanu/ and opens no broker connection before cutover
  (tanu/docs/adr/0007-no-overlap-with-legacy.md); the switch-over is ADR-0009 ("Start Tanu.command", `python -m tanu
  cutover` only on Mandeep's go). Tested by tanu/tests/test_tanu_boundaries.py and test_tanu_live.py.
- The rules below still hold for both (safety first); the legacy release rules apply only if the legacy code must be
  touched at all.

## Legacy rules

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
- **Every new input gets a gun-vs-bullet split first (Mandeep, 3 Nov 2026):** a book, video, idea or screenshot is
  read and sorted before anything is built: what is gun (shared engine pieces it needs, e.g. a shape detector, a level
  test, a grader) and what is bullet (the setups, patterns, thresholds and claims to test). Say the split to Mandeep,
  build the gun piece once, then add the bullets to registries / the Lab library, on trial (live/registry.py
  "shadow": True, graded in live/trials.py) and tested in the Lab on data they never saw.

## Test after every step (Mandeep, 3 Nov 2026)

"Har step mein jab bhi kuch karo toh har bar ek test karna hai ki ensure nothing is clashing."

- After every change, however small: run the full tests, including **tests/test_no_clash.py** (shared Dhan speed
  limit and daily count, one token keeper, Upstox option budget, order-book sockets, one Lab run per folder, one live
  price feed, every Dhan data call through lab/dhan.py). A new connection, job or data source adds its check to
  live/selfcheck.py, which the System & logins page shows too.
- Open the page you changed in a headless browser (no console errors, no sideways scroll at phone width) before release.
- New connections are listed on the System & logins page with what they are used for, their limits and their login.
