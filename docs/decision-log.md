# Decision Log

## 2026-08-18: Canonical Patched Spec
**Decision:** Consolidate the original execution spec with patches v1.1 and v1.2 into `docs/execution-spec.md` before Slice 1.
**Alternatives considered:** Keep the original spec and patches as separate files.
**Rationale:** A single canonical spec lowers the chance of implementing superseded endpoint paths, scenario definitions, or schema constraints.
**Slice:** Pre-Slice 1

## 2026-08-18: Slice 1 Bootstrap Files
**Decision:** Include minimal `api/` and `tests/` bootstrap files in Slice 1.
**Alternatives considered:** Keep Slice 1 strictly to the original file list.
**Rationale:** The Slice 1 Definition of Done requires a FastAPI app responding `200` on `/health` and CI running tests; those requirements need an importable app and a health test.
**Slice:** Slice 1

## 2026-08-18: Foundation Git Ignore
**Decision:** Add `.gitignore` in Slice 1 for local virtualenv, cache, bytecode, and editable-install metadata.
**Alternatives considered:** Leave generated verification artifacts visible in `git status`.
**Rationale:** Slice review should focus on source and configuration files, not local tooling artifacts created while running the agreed checks.
**Slice:** Slice 1

## 2026-08-18: Health Endpoint Authentication
**Decision:** Keep `GET /health` unauthenticated while requiring tenant resolution on later tenant-scoped API routes.
**Alternatives considered:** Require `X-Tenant-Id` on every route including health.
**Rationale:** Slice 1 needs a simple readiness check for Docker, CI, and local smoke tests; it carries no tenant-scoped data or payment behavior.
**Slice:** Slice 1

## 2026-08-18: Slice Verification Notes
**Decision:** Record slice-level verification results under `docs/` when a formal gate depends on host tooling.
**Alternatives considered:** Keep verification only in chat output.
**Rationale:** The project should preserve which checks passed, which command failed, and whether a gap is caused by source code or local environment.
**Slice:** Slice 1

## 2026-08-18: Docker Compose Gate Completed
**Decision:** Accept the Slice 1 Docker Compose gate after a live, containerized health check and database readiness check.
**Alternatives considered:** Rely only on the local Python test suite.
**Rationale:** The Slice 1 Definition of Done explicitly requires Compose startup and `GET /health` returning `200`; Docker Desktop 4.86.0 successfully ran the API and PostgreSQL services, with PostgreSQL healthy and the API returning the expected payload.
**Slice:** Slice 1

## 2026-08-18: Slice 2 Alembic Bootstrap
**Decision:** Add `alembic.ini` and `alembic/env.py` alongside the specified initial migration.
**Alternatives considered:** Store migration configuration only in developer commands or defer it until a later slice.
**Rationale:** An Alembic revision is not executable without its configuration and environment module. These files are the minimal plumbing required to prove the Slice 2 migration against PostgreSQL; they add no payment behavior.
**Slice:** Slice 2

## 2026-08-18: Policy Merchant Tenant Consistency
**Decision:** Enforce `PolicyMerchant` tenant consistency through composite foreign keys to `(tenant_id, id)` on `spending_policy` and `merchant`.
**Alternatives considered:** Application-side validation or a cross-table `CHECK` constraint.
**Rationale:** PostgreSQL `CHECK` constraints cannot reference other tables. The composite foreign keys make cross-tenant policy and merchant pairing structurally impossible, independent of application code.
**Slice:** Slice 2

## 2026-08-18: Slice 2 Type-Check Coverage
**Decision:** Extend the existing mypy command to include `models` and `schemas`.
**Alternatives considered:** Rely on local-only model type checks or defer static coverage until routes use the models.
**Rationale:** Slice 2 introduces production domain and boundary code. CI must type-check it alongside the FastAPI package so a later change cannot silently regress the data contract.
**Slice:** Slice 2

## 2026-08-18: Slice 3 Transition Transaction Boundary
**Decision:** Make `transition()` asynchronous and require an `AsyncSession`, re-reading the payment row with `FOR UPDATE` using both its payment ID and tenant ID.
**Alternatives considered:** A synchronous in-memory state helper, a generic status-update endpoint, or database triggers.
**Rationale:** The fixed stack uses SQLAlchemy's async engine. The transition and its audit record must share a transaction, and row locking prevents competing callers from applying lifecycle changes from a stale state.
**Slice:** Slice 3

## 2026-08-18: Approval Consumption Deferred to Slice 4
**Decision:** Slice 3 enforces the lifecycle graph and amount bounds; the approval-ID requirement, atomic consumption update, `ApprovalRequiredForAuthorizationError`, and `python -O` regression test remain in Slice 4.
**Alternatives considered:** Partially consume approvals in the state machine before the policy and approval routes exist.
**Rationale:** Execution Spec Patch v1.2 explicitly assigns the raised domain error and its test to Slice 4. Deferring the incomplete approval behavior avoids a non-atomic placeholder implementation.
**Slice:** Slice 3

## 2026-08-18: Testbed Tenant Identity
**Decision:** Use `X-Tenant-Id` as a local testbed-only tenant identity mechanism, with production auth explicitly out of scope.
**Alternatives considered:** Implement signed JWT/session claims in MVP.
**Rationale:** The testbed needs deterministic tenant isolation scenarios without adding production authentication complexity before the payment safety surfaces exist.
**Slice:** Slice 1

## 2026-08-18: Slice 4 Active Policy and Evaluation Order
**Decision:** Treat the highest immutable policy version for a tenant as active. Evaluate expiry, currency, allowed active merchant, per-payment amount, then UTC-calendar-day allowed spend; only an otherwise valid request may require approval.
**Alternatives considered:** Mutable policy rows, fallback to an older unexpired policy, or an approval result alongside denial reasons.
**Rationale:** Versioned immutable policies make the policy used for a decision auditable and let approval consumption detect a later policy change. A newer expired policy is a denial rather than silently reviving older rules.
**Slice:** Slice 4

## 2026-08-18: Webhook Rejection Audit Routing
**Decision:** Invalid-signature and tampered-body rejections write a structured log entry only,
with a generated correlation ID, rejection reason, remote IP when available, and a SHA-256 hash
of the raw body. They create no `AuditEvent`. Stale timestamp, duplicate event, and
tenant-mismatch rejections write tenant-scoped `AuditEvent` rows after signature verification.
**Alternatives considered:** A dedicated system/audit tenant for pre-verification rejections.
**Rationale:** A fabricated tenant would require a fake `Tenant` row to satisfy `AuditEvent` FK
constraints, undermining the tenant-isolation guarantees established in Patches v1.1 and v1.2.
Before verification no tenant can be trusted or reliably parsed; structured logs are the honest
record. Once verification succeeds, the signed tenant identity supports a complete audit trail.
**Slice:** Slice 5

## 2026-08-18: Mock Provider Shared Signing Secret
**Decision:** Use one `PROVIDER_WEBHOOK_SECRET` for the local mock provider.
**Alternatives considered:** Per-tenant or per-integration secret storage and rotation.
**Rationale:** A shared secret is intentional testbed simplification. Production credentials
would be scoped and rotated per integration or tenant; this testbed still validates raw-body
integrity, timestamp freshness, tenant/payment binding, and duplicate handling.
**Slice:** Slice 5

## 2026-08-19: MCP Tenant Binding
**Decision:** Bind each local stdio MCP server process to `MCP_TENANT_ID`; no MCP tool accepts a
tenant ID argument.
**Alternatives considered:** Trust a tenant ID supplied by the agent as a tool argument, or add
production authentication to the local testbed.
**Rationale:** Agent-supplied arguments are untrusted. Process configuration gives the testbed a
simple, explicit tenant boundary without pretending to implement production identity claims.
Tests inject the configured tenant only through the server environment.
**Slice:** Slice 6

## 2026-08-18: Slice 4 Idempotency Collision Contract
**Decision:** A same-payload replay returns the original decision. A key reused with a different payload returns HTTP 409 with that unchanged original decision and records an `IDEMPOTENCY_KEY_REPLAYED` audit event.
**Alternatives considered:** Re-evaluate the second payload, overwrite the request, or roll back the collision audit while raising an exception inside the transaction.
**Rationale:** Idempotency is tenant-scoped and must never turn one authorisation into another. Returning after the transaction commits preserves the forensic audit record.
**Slice:** Slice 4

## 2026-08-18: Slice 4 One-Time Approval Consumption
**Decision:** `APPROVAL_REQUIRED -> AUTHORIZED` requires an approval ID. The state machine checks tenant, payment request, expiry, and latest policy version, then conditionally updates `consumed_at IS NULL` in the same transaction as the state transition.
**Alternatives considered:** `assert approval_id`, an application-only consumed flag, or consuming approval outside the transition transaction.
**Rationale:** Assertions disappear under `python -O`; a raised domain error and audit record do not. The conditional update remains safe if competing authorization attempts race.
**Slice:** Slice 4

## 2026-08-18: Slice 4 Property-Test Floor
**Decision:** Extract pure policy-rule evaluation from the database adapter and require at least 25 collected Slice 4 tests, including Hypothesis-generated rule combinations.
**Alternatives considered:** Only example-based route tests or property tests coupled to a live database.
**Rationale:** The pure core makes precedence and boundary invariants cheap to generate at scale, while route tests continue to cover tenancy, persistence, and transactions. Slice 4 now has 25 example-based cases plus two properties running 300 examples each.
**Slice:** Slice 4

## 2026-08-19: Tenant Relationship Integrity Hardening
**Decision:** Add composite foreign keys to every tenant-scoped child-to-parent relationship,
supporting child-side indexes, and `UNIQUE (tenant_id, version)` on `SpendingPolicy`.
**Alternatives considered:** Rely only on application query filters, or introduce PostgreSQL
row-level security before the adversarial suite.
**Rationale:** Tenant-filtered queries remain required, but composite foreign keys make an
accidental cross-tenant parent reference impossible to persist. The current model intentionally
uses one ordered policy-version timeline per tenant. Row-level security remains a possible
production-hardening extension, not a substitute for these explicit integrity constraints.
**Slice:** Hardening checkpoint before Slice 7

## 2026-08-19: Unavailable Merchant Request Handling
**Decision:** A payment request whose merchant is unavailable in the trusted tenant returns
`403 CROSS_TENANT_ACCESS_DENIED`, creates no payment request, decision, or payment, and writes a
tenant-scoped rejection audit event.
**Alternatives considered:** Persist a denied request, return a generic not-found response, or
perform an unscoped lookup to distinguish a nonexistent merchant from another tenant's merchant.
**Rationale:** The controlled safety testbed benefits from an explicit isolation reason code.
The tenant-filtered merchant lookup avoids exposing another tenant's record while the database
constraints independently reject any accidental cross-tenant relationship.
**Slice:** Hardening checkpoint before Slice 7

## 2026-08-21: TrustGate Read-Only Catalog Surface
**Decision:** Add a tenant-scoped synthetic catalog and expose only an active-item `list_catalog`
MCP tool for the TrustGate upgrade.
**Alternatives considered:** Let the agent provide merchant and amount values, or expose a generic
provider/order-creation MCP tool.
**Rationale:** The catalog is the future source of truth for merchant, integer price, currency,
and quantity limits. The read-only tool begins the agentic commerce flow without granting any
provider, approval, tenant-selection, or payment-state authority.
**Slice:** TrustGate Phase 1

## 2026-08-21: TrustGate Catalog-Derived Purchase Requests
**Decision:** Accept catalog purchase intent as `actor_id`, `sku`, `quantity`, `purpose`, and an
idempotency key. Derive merchant, amount, currency, and order reference from a tenant-scoped active
catalog item, and persist that catalog snapshot with the request.
**Alternatives considered:** Permit the agent to supply a merchant or amount and validate it later,
or make the public catalog endpoint claim MCP provenance.
**Rationale:** Server-derived facts remove the most important agent-controlled payment parameters.
Composite tenant foreign keys prevent a persisted catalog reference from crossing tenants. The public
endpoint records `API`; only the MCP helper records `MCP_AGENT`, keeping audit provenance truthful.
**Slice:** TrustGate Phase 1

## 2026-08-21: TrustGate Approval and Spend Authority Hardening
**Decision:** Bind MCP actor identity to server configuration, make MCP approval requests audit-only,
and require a separate approver token plus configured approver identity to grant an approval. Reserve
per-actor UTC daily budget atomically for both `ALLOW` and `REQUIRE_APPROVAL` decisions.
**Alternatives considered:** Agent-supplied actor or approver labels, counting only initial `ALLOW`
decisions, or a read-then-write daily-spend check.
**Rationale:** Labels supplied by an agent do not establish identity. A conditional PostgreSQL upsert
is the budget concurrency boundary, preventing two requests from jointly exceeding the same limit.
Pending approval reserves budget so approved high-value requests cannot bypass daily controls.
**Slice:** TrustGate authority hardening before provider integration

## 2026-08-21: Local-Only Testbed Exposure
**Decision:** Bind Docker service ports to `127.0.0.1`, load ignored local `.env` configuration rather
than `.env.example`, and disable the legacy raw payment-request route unless explicitly enabled for
the test harness.
**Alternatives considered:** Expose predictable demo credentials with the API, or leave the legacy
route available alongside the catalog path.
**Rationale:** The project uses testbed identity rather than production authentication. It must stay
local-only; the TrustGate demo path is catalog-derived and the legacy route exists only for prior-slice
regression coverage.
**Slice:** TrustGate authority hardening before provider integration

## 2026-08-21: Immutable Catalog Snapshot and Webhook Size Limit
**Decision:** Persist catalog SKU, catalog name, merchant display name, quantity, purpose, derived
amount, and currency with every catalog payment request; reject provider webhook bodies over 64 KiB.
**Alternatives considered:** Resolve display values from the mutable live catalog at checkout time, or
accept arbitrary-sized raw webhook input before signature validation.
**Rationale:** Checkout authority must refer to the exact request that was evaluated, not a later
catalog edit. The size limit bounds unauthenticated request handling while preserving raw-byte HMAC
verification for accepted payloads.
**Slice:** TrustGate authority hardening before provider integration

## 2026-08-22: One-Time Checkout Authority
**Decision:** Issue one 15-minute checkout authority only for an `AUTHORIZED` catalog request whose
latest policy version remains current and unexpired. Bind it to the payment, optional consumed human
approval, policy version, and a SHA-256 hash of the immutable purchase snapshot.
**Alternatives considered:** Create provider orders directly from payment requests, trust browser
payment identifiers, or recreate authority after it is used or expires.
**Rationale:** A provider order needs a single, replay-resistant permission record. The authority
route locks the request/payment, re-checks policy drift, and is idempotent only while the same unused
authority remains valid. A human approval now atomically consumes its approval and authorizes payment.
**Slice:** Checkout authority before Razorpay Test Mode adapter

## 2026-08-23: Checkout Authority Concurrency and Policy Integrity
**Decision:** Serialize policy publication and checkout-authority issuance on the tenant row;
enforce one active approval per payment request with a partial unique index; make published
spending-policy rows immutable with a PostgreSQL trigger; and consume a checkout authority through
one locked, transactional helper before any provider order can be created.
**Alternatives considered:** Rely on application conventions for immutability, perform separate
read-then-write checks, or mark an authority used after the provider call.
**Rationale:** These controls make the safety boundary database-enforced where possible. A policy
cannot drift between the authority check and issuance, duplicate active approvals cannot be
persisted, and a replay or concurrent provider attempt cannot consume the same authority twice.
The authority claim is intentionally fail-closed: an infrastructure failure after claim requires
explicit recovery rather than risking a duplicate provider order.
**Slice:** Checkout authority hardening before Razorpay Test Mode adapter

## 2026-08-23: Razorpay Test Mode Checkout Boundary
**Decision:** Create Razorpay orders only from a consumed, tenant-bound checkout authority and
persist the returned provider order ID, amount, currency, receipt, and payment binding. Verify
browser callback signatures with `hmac.compare_digest` over the provider order ID stored by the
server, but do not treat a browser callback as proof of captured funds.
**Alternatives considered:** Let the client provide order facts, trust its returned order ID, or
transition the payment from the callback alone.
**Rationale:** The adapter exposes only the public Test Mode key ID and server-derived order data.
The deterministic authority-derived receipt gives the provider a second idempotency boundary. A
later signed provider webhook remains the authority for payment-state progression.
**Slice:** Razorpay Test Mode adapter

## 2026-08-23: Local PostgreSQL IPv4 Defaults
**Decision:** Use `127.0.0.1` rather than `localhost` for host-side PostgreSQL defaults in local
configuration, test fixtures, API fallback configuration, and Alembic configuration.
**Alternatives considered:** Retain `localhost` and rely on platform-specific IPv6 fallback.
**Rationale:** Docker Compose intentionally binds PostgreSQL only to `127.0.0.1`. On the Windows
development environment, `localhost` resolves to IPv6 first and an async psycopg connection can
stall instead of reaching the IPv4-only bind. An explicit loopback address makes the clean-clone
path deterministic while the containers continue to use the internal `postgres` hostname.
**Slice:** M0 clean-clone reliability
