# Testing

```bash
python -m pytest tanu/tests -q        # Tanu
python -m pytest tests -q             # legacy suite (must stay green until cutover)
```
Test files are named `test_tanu_*.py` so they never clash with legacy test names.

## Kinds of tests
| Kind | What it proves | Example |
|---|---|---|
| Unit | one function's contract | settings load order, URL guard |
| Boundary | architecture rules hold | no Manu ↔ Tanu imports; writes only under data/tanu |
| Safety | ADR-0001 holds | trading / account URLs blocked; no order calls in source |
| Known-answer (T3) | the evaluator is right | synthetic data with a planted edge must be found; pure noise must not pass; adding ideas raises the bar (ledger) |
| Golden replay (T1/T3) | determinism (ADR-0004) | a recorded fake day replays to the same bars, signals and paper trades |
| Fake broker (T1) | feed + tape end to end | local websocket server speaking the broker format; disconnects and resubscribes |
| UI (T5) | pages work | headless browser: no console errors, no sideways scroll at 390 px |

## Rules
- Every bug fix comes with a test that failed before the fix.
- Tests never touch the network, tokens or real broker accounts; brokers are faked.
- Results on market data must not move by accident: golden files change only in a commit that says why.
