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
