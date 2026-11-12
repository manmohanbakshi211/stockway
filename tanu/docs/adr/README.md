# Architecture Decision Records

One file per decision: context, decision, consequences. Never edit an accepted ADR's decision; write a new one that
supersedes it. Template: copy [0000-template.md](0000-template.md).

| # | Decision | Status |
|---|---|---|
| [0001](0001-market-data-only.md) | Market data only: no orders, no account access | Accepted |
| [0002](0002-replace-legacy-with-tanu.md) | Tanu replaces the legacy app; cutover after a clean full day | Accepted |
| [0003](0003-gun-and-bullets.md) | Gun (engine) vs bullets (dated data files) | Accepted |
| [0004](0004-one-engine-replay-equals-live.md) | One engine for history replay and live paper | Accepted |
| [0005](0005-evidence-before-trust.md) | Promotion protocol: costs, walk-forward, holdout, shadow | Accepted |
| [0006](0006-tick-tape.md) | Raw tick tape as the source of truth for replay | Accepted |
| [0007](0007-no-overlap-with-legacy.md) | No overlap with the legacy app before cutover | Accepted |
