# Architecture

Status: T0 built (core/). Everything else below is the target design; each layer lands in the phase named in
[ROADMAP](ROADMAP.md). Decisions behind it: [ADRs](adr/README.md).

## 1. Goals and non-goals
**Goals:** find option-buying setups that make money **after costs** on days they never saw; alert clearly (what,
when, why, stop, target, evidence); paper-trade them automatically; record everything so any day can be replayed;
grow later into swing / positional / investing / ETF / mutual funds on the same core.
**Non-goals:** real orders or any account access (ADR-0001); predicting every candle; showing everything that can be
computed.

## 2. Layers
```
            ┌───────────────────────────── UI (light web app: Today · Setups · Journal · System) ─────────────────┐
            │                                                                                                    │
  Upstox ──►│ 1 FEED + RECORDER ─► 2 STORE (tape, bars, chains, facts) ─► 3 COSTS ─► 4 STATE ─► 5 SETUPS        │
  (Kite     │   own process          point-in-time, dated                 drag per    day type,   bullets:      │
  backup)   │                                                             instrument  with/against signals      │
            │                                   6 ENGINE (one code path: replay = live) ◄──────────────┘          │
            │                                     │                                                             │
            │                     7 EVALUATOR ◄───┤───► 8 PAPER BROKER + RISK ─► 9 JOURNAL ─► 10 REVIEW (AI)    │
            │                     walk-forward,     realistic fills, daily stop   footprints     hypotheses ─► 5  │
            │                     holdout, promote                                                               │
            └────────────────────────────────────────────────────────────────────────────────────────────────────┘
```
| # | Layer | Responsibility | Knows nothing about |
|---|---|---|---|
| 0 | core | settings, paths, logs, safety guard | markets |
| 1 | feed + recorder | broker websocket in its own process; every message to the tick tape; reconnects; failover | strategies |
| 2 | store | tape, 1-min bars, chains, dated facts (lot sizes, expiries, charges, events); gap fill; legacy import | strategies |
| 3 | costs | spread, slippage, tick size, charges, theta → cost drag per instrument per day; tradeable list | setups |
| 4 | state | day type vs the instrument's own typical day, with / against, volatility, expiry, gap, breadth, "in play" | setups |
| 5 | setups | declarative bullets: entry, filters, exit, sizing; patterns as timing only | engine internals |
| 6 | engine | turns bars/ticks + state into signals; identical for history replay and live | which setup is good |
| 7 | evaluator | costs-included tests, walk-forward, holdout, multiple-testing control, champion/challenger, retire on decay | UI |
| 8 | paper + risk | bid/ask fills, delay, stop/target/trail/time exits, daily loss stop, correlated exposure | evaluation |
| 9 | journal | every trade's footprint: state + features at entry, best/worst point, exit reason, costs | — |
| 10 | review | evening AI review → hypotheses to the research queue (never straight to live) | live trading |

Horizon plugins (later): intraday options (first), swing / positional, investing (fundamentals + factors), ETF / MF.
They reuse layers 0-4 and 6-9; only setups and holding horizons differ.

## 3. Processes (target)
| Process | Does | Why separate |
|---|---|---|
| `tanu-feed` | websocket, decode, tape writer | never falls behind the broker, whatever the analysis does |
| `tanu-engine` | state, setups, paper, journal | heavy work cannot slow the feed |
| `tanu-web` | UI + API | pages cannot slow trading logic |
Talk over local pipes / files; each process logs with its own component name and the same run id scheme.

## 4. Boundaries and rules
- Tanu ↔ legacy: no imports either way (tested); Tanu writes only under `data/tanu/`; legacy data read-only.
- One broker connection and one token keeper at a time: Tanu connects only after cutover (ADR-0007).
- All facts that change over time are dated data (ADR-0003): old dates keep old values, so history replays stay
  identical.
- Every URL passes `safety.check_url` (ADR-0001).

## 5. Data flow of one trade (target)
tick → tape (disk) and engine → bar closes → state updated → setup fires (with state + evidence) → risk check →
paper fill at ask (+delay) → exits checked on every tick → close → journal row + footprint → evening review.

## 6. Technology
Python 3.11+, numpy / pandas / pyarrow (Parquet + zstd), pydantic (settings, schemas), FastAPI (web), protobuf
(Upstox V3 feed), SQLite only for small transactional state. No new heavy dependencies without an ADR.
