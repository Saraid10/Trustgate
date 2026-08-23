# Slice 2 Verification

Date: 2026-08-18

## Domain Contract

Passed:

- All ten canonical entities are represented by SQLAlchemy declarative models and Pydantic boundary schemas.
- Every tenant-scoped table has a non-null `tenant_id` with a foreign key to `tenant`.
- Every monetary column has a PostgreSQL non-negative `CHECK` constraint.
- Payment request idempotency and provider event delivery use tenant-scoped unique constraints.
- `PolicyMerchant` uses composite foreign keys to `(tenant_id, id)` on both `spending_policy` and `merchant`; a cross-tenant pairing is rejected by PostgreSQL.

## Migration Gate

Passed:

- `alembic upgrade head` applies revision `0001_initial` to the working Compose database.
- `alembic check` reports no pending upgrade operations.
- A uniquely named temporary database was created, migrated from an empty schema, tested, and dropped. This proves the initial migration is clean-room executable without modifying the working database.

## Test and Quality Gate

Passed:

- `ruff check .`
- `mypy api models schemas`
- `pytest -v` with 5 passing tests.
- The focused database suite verifies one valid fixture set spanning all domain entities, plus DB-level rejection of a negative amount, a missing `tenant_id`, and a cross-tenant `PolicyMerchant` row.
- `pyproject.toml` and the GitHub Actions workflow parse successfully; CI type-checks `api`, `models`, and `schemas`.

## Docker Service Gate

Passed inside the live Docker Compose stack:

- PostgreSQL is healthy and `pg_isready` reports `accepting connections`.
- The API container is running, exposes port `8000`, and `GET /health` returns `{"status":"ok"}`.
- The API container runs `alembic upgrade head` idempotently and `alembic check` finds no schema drift.
- Inside the Linux API container, Ruff and mypy pass and all 5 tests pass against the Compose PostgreSQL service.
- The API container was recreated once during verification so its editable installation included the new `models` and `schemas` packages; the refreshed container passed the complete in-container suite.

## Review Prompt Cross-Check

No findings for the Slice 2 diff:

- No API route or MCP input path exists in this slice, so no client amount, currency, or tenant ID is trusted.
- No payment transition, webhook parsing, audit write path, or lookup-by-ID query exists before its assigned later slice.
- State-machine amount-bound invariants are intentionally deferred to Slice 3; Slice 2 enforces their non-negative base constraints at the database layer.
