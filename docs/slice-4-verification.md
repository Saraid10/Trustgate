# Slice 4 Verification

## Scope

Implemented the deterministic policy engine, tenant-scoped payment-request and approval APIs,
token-gated immutable policy creation, and atomic approval consumption in the payment state
machine.

## Rules Cross-Checked

- Public tenant routes resolve `X-Tenant-Id` against the database and reject unknown tenants
  with `403`; the payment-request body forbids a tenant field.
- Every external identifier lookup in these routes includes trusted `tenant_id` in its query.
- The active policy is the highest tenant-local version. Daily spend counts only same-tenant,
  same-actor `ALLOW` decisions in the current UTC day; refunds and capture status do not affect it.
- Direct policy denials take precedence over approval. The evaluator returns the applicable
  canonical reason codes for expiry, currency, merchant, amount, and daily spend.
- Same-payload idempotency replays the original result. A changed payload with the same
  tenant-scoped key returns `409`, leaves the persisted decision unchanged, and writes one
  `IDEMPOTENCY_KEY_REPLAYED` audit event.
- Internal policy creation accepts only `X-Internal-Admin-Token`, never a tenant header, creates
  a new policy version, and tenant-filters every merchant in the allowlist.
- Approval-required authorization never uses `assert`. Missing approval emits exactly one
  `illegal_transition_attempt` audit event and raises
  `ApprovalRequiredForAuthorizationError`; valid approval consumption is conditional on
  `consumed_at IS NULL` in the same transaction as authorization.

## Host Verification

Run on 2026-08-18 with the project virtual environment:

```text
ruff check .                                      PASS
mypy api models schemas state_machine policy_engine PASS
pytest tests/test_policy_engine.py -v             PASS (27 Slice 4 cases: 25 example-based, 2 properties)
pytest -v                                         PASS (48 project cases)
```

The test suite invokes the missing-approval transition in a fresh subprocess twice, once with
normal Python and once with `python -O`; both runs produced the same raised domain error and
exactly one audit event.

The Slice 4 module includes two Hypothesis properties, each configured for 300 generated rule
combinations. They cover denial precedence and the full amount/daily-limit/approval decision
partition, including exact-boundary behavior.

## Docker Verification

After recreating the API service against the local PostgreSQL service:

```text
docker compose exec -T api python -m ruff check .                         PASS
docker compose exec -T api python -m mypy api models schemas state_machine policy_engine PASS
docker compose exec -T api python -m pytest tests/test_policy_engine.py -q PASS (27 tests)
docker compose exec -T api python -m alembic current --check-heads       PASS (0001_initial head)
GET http://localhost:8000/health                                         200 {"status":"ok"}
```

## Deferred Surface

`GET /payments/{payment_id}`, audit-read routes, provider webhooks, capture/refund APIs, MCP
exposure, and scenarios remain in their assigned later slices. The actor registry needed to give
`TENANT_ACTOR_MISMATCH` a persisted meaning is not part of the current domain schema, so that
reason is deliberately not fabricated from a naming convention.
