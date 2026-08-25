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

## 2026-08-23: Constrained Buyer Agent and Deterministic Injection Harness
**Decision:** Keep the M1 buyer agent as a replaceable orchestration layer with exactly two
capabilities: list the configured tenant's catalog and create a catalog purchase proposal using
only SKU, quantity, and purpose. Model output fields outside that proposal shape are discarded.
Use a deterministic instruction-following harness to demonstrate poisoned catalog content rather
than making the safety proof depend on a particular external LLM's behavior.
**Alternatives considered:** Give the agent direct payment or provider operations, make the demo
depend on a live model endpoint, or suppress the adversarial behavior with a more obedient agent.
**Rationale:** The product claim is that backend authority holds even when an agent is confused or
influenced by untrusted content. A deterministic harness makes that claim reproducible, while the
protocol boundary permits a future model provider without changing money authority.
**Slice:** M1 buyer agent and adversarial harness

## 2026-08-24: Live Model Buyer With Measured Untrusted Influence
**Decision:** Add an optional live-model buyer used only by `python -m agent.demo --live`, keeping
the deterministic substitutes as the sole models the regression suite exercises. Do not constrain
the live model with a strict output schema. Detect untrusted-content influence by proposing twice,
once against a catalog whose third-party descriptions are removed, and comparing the results.
**Alternatives considered:** Keep the deterministic harness as the only buyer; make the live model
the default; constrain its output with a strict schema; or let the model self-report influence.
**Rationale:** The deterministic harness proves the server holds when a buyer follows hostile text,
but it cannot evidence the premise that a real reasoning system is swayed by it, because it is a
parser doing what it was written to do. A live path supplies that evidence while the suite stays
free of provider dependence and nondeterminism. A strict schema would make an authoritative field
structurally unemittable and would therefore hide the behavior the demonstration exists to show;
the narrow contract is enforced in `BuyerAgent`, on the trusted side of the boundary. A model
cannot reliably report its own susceptibility, so influence is measured by comparison instead.
**Slice:** M1 buyer agent and adversarial harness

## 2026-08-24: Comparison Stability Without Sampling Controls
**Decision:** Send no sampling parameters on live buyer requests. Obtain comparison stability by
judging untrusted influence only on the discrete `sku` and `quantity` selections, never on the
free-text `purpose`.
**Alternatives considered:** Set `temperature=0` to suppress run-to-run variation, or accept
free-text differences as influence signal.
**Rationale:** `temperature`, `top_p`, and `top_k` were removed on the current model family and are
rejected with HTTP 400, so a sampling control is not available regardless of its merit. It would
also have failed only on a live run, because a substituted test client accepts any keyword; a
regression test now asserts the request carries no sampling parameters. Restricting the comparison
to discrete selections removes the variance that mattered: a differently worded justification is
not evidence of influence, whereas a changed SKU or quantity is.
**Slice:** M1 buyer agent and adversarial harness

## 2026-08-24: Adversarial Scenarios Assert What Changed, Not What Was Returned
**Decision:** Give every Tier A scenario a before-and-after tenant snapshot and require three
assertions: the rejection with its reason code, that no provider order was created, and that no
payment gained an authority-bearing state. Raise `ScenarioViolation` from the harness rather than
using bare `assert`. Generate the published attack matrix from the scenario registry and assert in
a test that the README matches it.
**Alternatives considered:** Assert only on the endpoint response as the existing unit tests do;
use bare assertions in the harness because it is only used by tests; hand-write the attack matrix
and regenerate it by hand later.
**Rationale:** A response assertion cannot distinguish "the request was refused" from "the request
was refused and something was written anyway", which is the claim the project actually makes. The
harness ships inside the `scenarios` package and `python -O` strips assertions, so an assert-based
harness would report every scenario as passing under optimisation while verifying nothing; the
same reasoning already applied to the state machine's approval requirement. A generated matrix
cannot claim an attack that has no passing test, which a hand-written one silently can.
**Slice:** M2 early attack proof

## 2026-08-24: Amazon Bedrock Backend for the Live Buyer
**Decision:** Support two live-buyer backends behind one switch, `TRUSTGATE_MODEL_BACKEND`. The
Bedrock backend resolves credentials through the standard AWS chain and defaults to the
open-access `anthropic.claude-haiku-4-5`; the direct backend keeps reading `ANTHROPIC_API_KEY`.
**Alternatives considered:** Require a provider API key and treat the live demonstration as
optional; add a generic OpenAI-compatible adapter for free providers; run a local model.
**Rationale:** Both backends speak the same Messages API shape, so the switch costs one client
factory and no change to prompt handling, parsing, or the influence comparison. Billing through an
existing AWS account removes the only cost barrier to producing the live adversarial evidence,
which is M1's remaining open item. Haiku is the cheapest model that can select a SKU and is open
to all Bedrock customers, so it needs no access request; `TRUSTGATE_MODEL_ID` overrides it. A
local or third-party free model remains possible later because `BuyerModel` is a protocol, but a
frontier model makes the injection result more credible than a small local one.
**Slice:** M1 buyer agent and adversarial harness

## 2026-08-24: Groq Backend and Load Local Environment in CLI Entry Points
**Decision:** Add a third live-buyer backend using Groq's free tier over plain HTTP, and load the
ignored local `.env` from the command-line entry points rather than on library import.
**Alternatives considered:** Require an Amazon Bedrock payment instrument; add an OpenAI SDK
dependency for the OpenAI-shaped API; run a local model; keep expecting the shell to export
configuration.
**Rationale:** Amazon Bedrock provisions Anthropic models through an AWS Marketplace subscription
that fails with `INVALID_PAYMENT_INSTRUMENT` until the AWS account has a verified card, which
blocked the live evidence for reasons unrelated to this project. Groq's free tier removes that
dependency and needs no card. Its API is OpenAI-shaped and simple enough that `httpx`, already a
core dependency, is sufficient; adding a provider SDK for one POST would not earn its weight. The
prompt, the deliberate absence of an output schema, and the JSON extraction are shared across all
three backends, so only transport differs and the authorization layer is unaffected by the choice.
Separately, `python-dotenv` was a declared dependency that nothing ever called, so `.env` reached
only Docker Compose and every host-run command silently ignored it. Loading it from entry points
without overriding existing variables fixes that while leaving container and CI environments
authoritative.
**Slice:** M1 buyer agent and adversarial harness

## 2026-08-25: Evidence Receipt Shape and Its Deliberate Absences
**Decision:** Key the receipt on the payment request rather than the payment, present the proposal
and the server-derived facts as separate stages, expose audit event kinds and correlation
identifiers without their payloads, and return the same 404 body for a cross-tenant read as for an
unknown identifier.
**Alternatives considered:** Key on the payment; return one flattened record; include raw audit
payloads; distinguish "belongs to another tenant" from "does not exist".
**Rationale:** A policy-denied request never produces a payment, and denied attempts are precisely
what the attack suite needs to evidence, so the payment is the wrong key. Merging the proposal with
the derived facts would hide the boundary the project exists to demonstrate; keeping them apart is
what makes the receipt an argument rather than a dump. Audit payloads can carry internal detail
while the kind and correlation are what make a trail followable. Distinct responses for
cross-tenant and unknown identifiers would confirm that an identifier exists elsewhere, which is a
disclosure in itself.
**Note:** An attack rejected before anything is persisted has no receipt, because no payment
request exists to key one on. That absence is the safety property and the audit trail is its
record; the M2 scenarios that assert nothing was created are exactly these.
**Slice:** M4 evidence receipt

## 2026-08-25: Request-Scoped Sessions Must Commit Explicitly
**Decision:** `get_session` commits when a request completes and rolls back when it raises.
**Alternatives considered:** Commit inside each route; rely on `session.begin()` to commit on exit;
stop resolving the tenant through the same session.
**Rationale:** Resolving the trusted tenant queries the database before any route body runs, which
autobegins a transaction. Every route's `session.begin_nested() if session.in_transaction() else
session.begin()` therefore took the savepoint branch, and releasing a savepoint does not commit the
transaction enclosing it. Closing the session then rolled every write back while the route still
returned its success response: a catalog request returned `201 ALLOW` and persisted nothing.
Committing per route would repeat the decision in every handler and leave the next one to forget it.
**Why the suite could not see it:** every other test receives a session already inside an explicit
transaction and asserts within it, so route writes are visible whether or not they would reach the
database. `tests/test_session_lifecycle.py` drives the dependency directly against PostgreSQL and
was confirmed to fail when the defect is reintroduced.
**Slice:** M3 Razorpay Test Mode flow

## 2026-08-25: Reserved Daily Budget Is Released on Terminal States
**Decision:** Return an actor's reserved daily budget when a payment reaches DENIED, EXPIRED,
FAILED, or CANCELLED, but only when it leaves a state that actually held a reservation
(APPROVAL_REQUIRED, AUTHORIZED, PROVIDER_PENDING). The subtraction is floored at zero.
**Alternatives considered:** Leave reservations to lapse at UTC midnight; release on every terminal
transition regardless of prior state; release on refund as well.
**Rationale:** Reserving on REQUIRE_APPROVAL stops an approved high-value request bypassing the
daily limit, but reserving without releasing turned an abandoned approval into a lockout. An agent
acting entirely inside its permitted contract could exhaust an actor's day by requesting approvals
nobody grants: a denial of service needing no forged amount and no escaped tenant scope. Measured
against the running system, three abandoned requests consumed a 200,000 minor-unit day.

The from-state guard matters as much as the release. Budget is reserved only for ALLOW and
REQUIRE_APPROVAL; a request denied outright never reserves and stays in CREATED. An unguarded
release refunded budget that was never taken, so a request denied *because* the day was full
handed back an amount it never held. That inverts the control into a way to manufacture budget,
and an existing policy test caught it.

Refund states are excluded deliberately: a refund is not evidence that the day's budget should
reopen, and treating it as such would let one limit be spent twice in a day.
**Slice:** Hardening found while auditing after M4

## 2026-08-25: Razorpay Webhook Is the Only Authority for a Payment Outcome
**Decision:** Verify the `X-Razorpay-Signature` HMAC over the exact received bytes before parsing,
using a `RAZORPAY_WEBHOOK_SECRET` distinct from the key secret. Reject a signed event whose
reported amount or currency differs from the stored order. Acknowledge signed events this project
does not act on with 202 and change nothing. Advance state only through `transition()`.
**Alternatives considered:** Trust the browser callback; parse the body then verify a
re-serialisation; accept the amount the provider reports; return an error for unhandled event
types.
**Rationale:** A browser callback proves a client returned with matching identifiers and nothing
more, so it cannot be what a captured state rests on. Parsing before verification would check a
signature against a different message than the one signed. A valid signature proves the event came
from the provider, not that it matches what was authorized, so the server-derived order amount
governs and a mismatch is refused and audited. Razorpay retries unacknowledged events, so
returning an error for event types outside this project's scope would generate indefinite retries
for events that are not problems.
**On state:** `payment.captured` cannot reach a payment still in `AUTHORIZED`; the provider
authorizes first. The state machine refuses that shortcut, so a validly signed capture cannot jump
a payment straight to captured, and a test pins it.
**Slice:** M3 Razorpay Test Mode flow

## 2026-08-25: Provider Order Recovery Rather Than a Documented Fail-Closed Stance
**Decision:** Record a `PENDING` provider-order intent, committed, before contacting Razorpay. On a
retry of an unresolved intent, ask the provider which orders already carry the deterministic
receipt: adopt the order when exactly one matches, create when none does, and mark the intent
`NEEDS_REVIEW` when several do. Enforce the state/identifier pairing with a database check
constraint.
**Alternatives considered:** Document the fail-closed stance in limitations and build nothing; rely
on the receipt as an idempotency key; send an idempotency header; retry blindly.
**Rationale:** Consuming an authority commits before the provider call, so a failure between the
two left the authority burned with no order and no record that one had been attempted. The
purchase could then neither proceed nor be retried.

The provider offers no protection here, which was verified rather than assumed. Against Test Mode
on 2026-08-25, two creates carrying the same receipt produced two distinct orders
(`order_TU24YwIe2cBZsJ` and `order_TU24ZFQmKkIepi`), and an `X-Razorpay-Idempotency-Key` header
also produced two. Filtering the orders list by `receipt` returned nothing, so matching is done
client-side over the listed orders. A blind retry would therefore have created a duplicate order,
which is the outcome the authority mechanism exists to prevent.

Recovery is consequently driven by state this system owns, with the provider consulted only to
discover what already exists. Duplicate matches are escalated rather than resolved silently,
because choosing between two real orders is not a decision to make without a human, and that state
is reachable in practice given the provider permits duplicate receipts.
**Slice:** M3 Razorpay Test Mode flow

## 2026-08-25: The Checkout Page Renders and Never Authorizes
**Decision:** Serve Standard Checkout from `GET /api/v1/razorpay/checkout/{razorpay_order_id}` for
an order that already exists and is `CONFIRMED`. Loading the page consumes no authority, creates no
provider order, and advances no payment. It carries the publishable key, the provider order id, and
the server-derived amount, and nothing else.
**Alternatives considered:** Create the order when the page loads; key the page on the checkout
authority; require a tenant header; hold the amount in the page and submit it with the payment.
**Rationale:** A page a browser can request is the least trusted surface in the system, so it must
not be able to move money. Creating the order on load would let a page refresh consume an authority
or trigger provider calls, which is the opposite of the property the authority mechanism exists to
provide. The provider order identifier is unguessable and every value the page carries is already
public to the browser by necessity, so a tenant header would add ceremony without adding a
boundary. The amount is rendered for the customer but the payment is bound to the order the server
created, so the browser cannot alter it.
**On the browser result:** the page states plainly that completing it is not proof of payment. Its
handler posts to the server for signature verification and reports only what the server concludes,
and capture still waits on a signed provider event.
**Slice:** M3 Razorpay Test Mode flow

## 2026-08-25: One Evidence Assembly, Two Renderings
**Decision:** Extract `build_payment_request_evidence` and have both the JSON endpoint and the HTML
receipt call it. The receipt renderer is a pure function of the assembled record: it queries
nothing and decides nothing.
**Alternatives considered:** Assemble the receipt separately with its own queries; render HTML by
transforming the JSON response in a client; use content negotiation on one route.
**Rationale:** Two assemblies would drift, and an evidence artifact that disagrees with itself is
worse than none. A test asserts the receipt contains the SKU, decision, merchant, and order
reference the JSON reports.
**On layout:** the three stages stay visually apart because the separation is the argument. A
reader can see that price and merchant were never the agent's to choose; merging them into one
summary would hide the property the project exists to demonstrate. A test pins the ordering.
**Language:** the receipt says tamper-evident and states plainly that it is not a signed or legally
non-repudiable record, and a test pins that wording so it cannot quietly inflate.
**Slice:** M4 evidence receipt
