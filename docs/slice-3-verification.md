# Slice 3 Verification

Date: 2026-08-18

## State Machine Contract

Passed:

- `state_machine/transitions.py` is the only production location that assigns `Payment.state`.
- The complete legal transition graph includes all terminal states with no outgoing transitions.
- Each transition re-reads the payment by both `payment.id` and `payment.tenant_id` using `SELECT ... FOR UPDATE` before deciding.
- Accepted transitions update state and add one `payment_transition` audit event in the same transaction.
- Rejected transitions leave state unchanged and add one `illegal_transition_attempt` audit event with a reason code and attempted amounts.
- Capture requires an authorized amount and cannot exceed it; refunds cannot exceed captured amount.

## Test Gate

Passed:

- Direct database-backed tests cover accepted transition auditing, rejected transition auditing, capture bounds, refund bounds, and terminal states.
- Hypothesis runs 500 generated lifecycle sequences and accepts only edges in `LEGAL_TRANSITIONS`.
- A second 500-case Hypothesis test validates capture and refund amount bounds.
- Host suite: `ruff check .`, `mypy api models schemas state_machine`, and `pytest -v` all pass with 21 tests.

## Docker Service Gate

Passed inside the live Docker Compose stack:

- The API container runs Ruff and mypy successfully.
- `pytest -p no:cacheprovider tests/test_state_machine.py -v` passes all 16 Slice 3 tests inside Linux against Compose PostgreSQL.
- `alembic upgrade head` is idempotent and `alembic check` reports no schema drift.
- PostgreSQL is healthy and the API health endpoint returns `{"status":"ok"}`.

## Deferred Patch Requirement

The transition graph allows `APPROVAL_REQUIRED -> AUTHORIZED`, but atomic approval consumption, the raised `ApprovalRequiredForAuthorizationError`, its exactly-one-audit-event assertion, and the `python -O` regression test are deliberately assigned to Slice 4 by Execution Spec Patch v1.2. No partial approval-consumption behavior has been added here.
