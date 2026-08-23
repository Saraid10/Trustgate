# M0 Verification - Clean-Clone Reliability and Concurrency Backfill

**Date:** 2026-08-23

## Invariants Proven

1. Host-side local PostgreSQL configuration targets the Docker Compose IPv4 loopback bind.
2. A hung test fails within a bounded time rather than silently blocking the suite.
3. Two independent PostgreSQL sessions cannot both reserve the final daily budget amount.
4. Two independent sessions cannot both consume the same approval or checkout authority.
5. Policy publication holds the tenant lock long enough that authority issuance re-reads the policy
   and rejects drift.

## Changes

- Replaced host-side `localhost` PostgreSQL defaults with `127.0.0.1` in local configuration,
  test fixtures, application fallback configuration, and Alembic configuration.
- Added `pytest-timeout` with a 60-second per-test and 300-second session limit using the portable
  thread method.
- Added repository line-ending normalization through `.gitattributes` and formatted the project.
- Added the initial README and verified editable installation in the API container.
- Added `tests/test_concurrency.py`, which uses separate committed sessions against PostgreSQL for
  daily-spend reservation, approval consumption, authority consumption, and policy publication.
- Reframed optimized-mode verification as a real subprocess behavior test, because `pytest` test
  assertions are intentionally removed by `python -O`.

## Commands and Results

| Gate | Result |
|---|---|
| `ruff check .` | Passed |
| `mypy` | Passed, 25 configured source files |
| Focused PostgreSQL concurrency suite | 4 passed |
| Optimized-mode missing-approval behavior | 2 passed (normal and `python -O`) |
| Alembic `head -> base -> head` | Passed across all 9 revisions; final revision `0009_razorpay_order` |
| Full Docker suite | 91 passed |

## Non-Blocking Warnings

- `pydantic_settings` emits an installed-dependency forward-reference warning; no project source
  location is involved.
- The container test user cannot write `.pytest_cache`; test results are unaffected.

## Decision Record

The IPv4 local-database choice is recorded in `docs/decision-log.md` under
"Local PostgreSQL IPv4 Defaults".
