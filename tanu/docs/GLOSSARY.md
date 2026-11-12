# Glossary

| Term | Meaning |
|---|---|
| Manu / legacy app | the old Stockway code (live/, lab/, webapp/, radar/), frozen from 13 Nov 2026 |
| Tanu | this core; replaces the legacy app at cutover |
| Gun | the stable engine pieces that know no strategy (ADR-0003) |
| Bullet | a strategy, threshold, calendar, cost rate or universe stored as dated data |
| Shadow | computed and recorded, not shown or traded |
| Trial | shown / paper-traded on its own account while it is being graded |
| Champion / challenger | the current setup vs a candidate measured on the same unseen data |
| Holdout | days never used for any choice; the final check |
| Walk-forward | choose on earlier days, test on the next days, roll forward |
| Cost drag | average % lost per trade from spread, slippage, charges and theta with no edge |
| Tradeable list | instruments whose cost drag is low enough to buy options on |
| Day type | quiet / range / trend so far, vs the instrument's own typical day by that time |
| With / against | trade direction vs the day's move so far |
| In play | a stock with unusual activity today (volume, news, gap) |
| Tape | the raw record of every broker message (ADR-0006) |
| Footprint | everything known about a trade: state at entry, best / worst point, exit reason, costs |
| Chandelier exit | stop that trails the best price by a multiple of ATR |
| R | the risk of a trade (entry to stop); results in R are size-free |
