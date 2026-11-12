# Contributing

## Flow
1. Branch from main: `tanu-<version>` (e.g. `tanu-0.2.0`).
2. Change code + tests + docs together (Definition of done in [../CLAUDE.md](../CLAUDE.md)).
3. `python -m pytest tanu/tests -q` and `python -m pytest tests -q` green; `python -m tanu doctor` OK.
4. Bump `tanu/VERSION` (semver: 0.MINOR for each phase step, 0.x.PATCH for fixes); add the CHANGELOG entry.
5. Squash-merge to main, push. The Mac picks up main on its next start.

## Commits
`tanu x.y.z: <what changed and why>` — first line under 100 characters; body lists the parts and the evidence (numbers
on unseen data when results change). Co-author trailers as required by the session.

## Code style
- Python 3.11+, type hints on public functions, docstrings say *why* and the contract.
- Small modules with one job; no module imports a higher layer (core ← store ← costs ← state ← setups ← engine).
- No print in library code: use `tanu.core.log`. No bare `except:`; catch, log with context, continue or re-raise.
- Settings through `tanu.core.config`; no constants for things that change (ADR-0003).
- Every network call: timeout, retry with backoff, rate limit, `safety.check_url`.

## Adding a bullet (T3+)
Add a file to the registry with `valid_from`, a schema-valid spec and a one-line "why" + source (book, video, research).
It starts in shadow. No engine edit.

## Adding a component
Write an ADR if it changes the architecture, data or operations; add its health record and log component name.
