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
harness would report every scenario as passing under optimization while verifying nothing; the
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

## 2026-08-25: A Payment Identifier Is Not an Event Identity
**Decision:** Derive the provider-event deduplication key from Razorpay's `X-Razorpay-Event-Id`
header when present, falling back to `razorpay:{event_type}:{payment_id}`.
**Alternatives considered:** Keep deduplicating on the payment identifier alone; hash the raw
payload; drop deduplication and rely on the state machine.
**Rationale:** Razorpay reports `payment.authorized` and `payment.captured` for one payment under
the same payment identifier. Deduplicating on it rejected the capture as a replay of the
authorization and stranded the payment in `PROVIDER_PENDING`, so the lifecycle could never
complete. The header is preferred because it is stable across retries of the same event, which is
the case deduplication exists for; the documentation does not guarantee it, so the fallback pairs
event type with payment: distinct per lifecycle step, identical for a genuine redelivery. Hashing
the raw payload was rejected because a provider may vary incidental fields between retries, which
would defeat deduplication precisely when it is needed.
**Why the suite missed it:** the test helper minted a fresh payment identifier on every call, so a
two-event sequence looked valid while never testing whether both events could coexist. The tests
now build events for a named payment, and one asserts the authorized and captured steps of the
same payment are both accepted in order.
**Note:** deduplication is defence in depth. The state machine independently refuses a repeated
transition, so a missed duplicate cannot advance a payment twice.
**Slice:** M3 Razorpay Test Mode flow

## 2026-08-26: Duplicate Prevention Under Concurrency, Pagination, and Ordering
**Decision:** Lock the pending provider-order intent with `FOR UPDATE` across the whole
reconcile-then-create sequence. Paginate the receipt search and fail closed with
`RAZORPAY_RECEIPT_SEARCH_INCOMPLETE` when it cannot reach the end of the order history. Refuse an
out-of-order provider event and rely on provider redelivery, with a test proving the retry
recovers.
**Alternatives considered:** Rely on the unique constraint alone for concurrency; read only the
first page of orders; buffer out-of-order events and replay them once predecessors arrive.
**Rationale:** Two retries of one pending intent both found no matching provider order and both
created one, because nothing serialised them. The unique constraint on `(tenant_id,
checkout_authority_id)` cannot help: both callers operate on the same existing row. The lock is
held across the provider call so the second caller waits and then observes the confirmed row.

Reading one page treated a matching receipt further back in history as absent, which would license
creating a second order for a purchase that already has one. An incomplete search now refuses
rather than reporting absence, because "not found in the part I looked at" is not "not found".

Buffering out-of-order events would add a queue and a replay path to solve a problem the provider
already solves by redelivering. Refusing is sound only if the retry works, so that path is tested
rather than assumed. It depends on a detail worth stating: the provider event row is written inside
the same nested transaction as the transition, so a refused event leaves no deduplication entry and
its redelivery is processed rather than dismissed as a replay.
**Slice:** M3 hardening after external review

## 2026-08-26: Evidence Follows the Purchase, Not One Correlation
**Decision:** Gather a receipt's audit trail by the identifiers naming the purchase - request,
payment, authority, and provider order - in addition to the authorization decision's correlation.
**Alternatives considered:** Keep gathering by the decision's correlation; thread one correlation
through the entire lifecycle.
**Rationale:** Issuing and consuming an authority, creating a provider order, and verifying a
callback or webhook each run under their own correlation, so gathering by the decision's
correlation produced a receipt that stopped at authorization and omitted the lifecycle it exists to
evidence. Against real data the trail grew from one entry to nine across six correlations. Threading
a single correlation through every step would tie together work that legitimately happens in
separate requests, days apart, and would lose the ability to trace one request's handling. A test
asserts a second purchase's identifiers never appear in the first purchase's trail.
**Slice:** M4 evidence receipt hardening after external review

## 2026-08-26: Untrusted Text Must Not Reach a Script Block Unescaped
**Decision:** Serialise the checkout options through a translate table that escapes `<`, `>`, `&`,
and the line and paragraph separators to unicode form, and serve the page under a per-request
nonce Content-Security-Policy.
**Alternatives considered:** Rely on `json.dumps`; strip the offending characters; move the options
into a data attribute and parse them.
**Rationale:** `json.dumps` emits `</script>` verbatim, so a catalog name carrying it closed the
script element and let whatever followed execute. Catalog text is the untrusted content this
project exists to contain, which makes the browser the last place to relax about it. Escaping to
unicode form leaves the value byte-identical to JavaScript, so nothing is stripped or altered and
the page still shows exactly what the server derived. The policy is defence in depth rather than
the fix: text that somehow reached the document as markup still has no way to execute. Styles keep
`unsafe-inline` because Razorpay Checkout injects its own, and tightening that would break the
payment flow without closing the hole being defended.
**Slice:** M3 hardening after external review

## 2026-08-26: A Failed Provider Attempt Is Evidence, Not a Verdict
**Decision:** Record `payment.failed` as a provider event and an audit entry without moving the
payment. The aggregate payment becomes terminal only through expiry, cancellation, or a capture.
**Alternatives considered:** Keep mapping `payment.failed` to a terminal `FAILED`; make `FAILED`
non-terminal by allowing a transition back.
**Rationale:** Razorpay documents `payment.failed` followed by `payment.captured`, and a UPI retry
produces exactly that, sometimes under a different payment identifier. Treating the first failure
as terminal did two wrong things at once: it released the reserved daily budget for a purchase that
might still complete, and it left the payment in a state with no legal successor, so the real
capture that followed was refused. Allowing a transition out of `FAILED` would weaken a terminal
state that other paths rely on; leaving the payment where it is keeps the state machine's
guarantees intact while preserving the attempt as evidence. `FAILED` remains reachable for an
explicit operational outcome rather than a single provider attempt.
**Slice:** M3 hardening after external review

## 2026-08-26: The Receipt Claims Traceability, Not Tamper-Evidence
**Decision:** Describe the evidence receipt as a traceable, tenant-scoped record and state plainly
that it is not tamper-evident.
**Alternatives considered:** Keep the tamper-evident wording; add a hash chain now.
**Rationale:** The receipt is assembled from live rows at read time and is neither hashed nor
signed, so it reflects the database as it stands rather than proving what it held earlier. Claiming
tamper-evidence would assert a property the artifact does not have, which is precisely the kind of
overstatement this project's evidence discipline exists to prevent. A signed or hash-chained
snapshot would earn the stronger word and is recorded as the deferred upgrade.
**Slice:** M4 evidence receipt

## 2026-08-26: Audit References Are Durable, Not Payload Conventions
**Decision:** Give every `AuditEvent` nullable, tenant-scoped foreign-key references to the payment
request, payment, checkout authority, and provider order it concerns. Build evidence trails from
those references rather than from correlation IDs or JSON payload keys.
**Alternatives considered:** Continue collecting events by a decision correlation; query arbitrary
JSON keys in each receipt; use one lifecycle-wide correlation ID.
**Rationale:** Lifecycle operations run in independent requests, so their correlations properly
differ. JSON is useful event detail but not a durable relational contract: a review-required
provider-order event with only a receipt and order list disappeared from its own evidence trail.
The new composite foreign keys make a cross-tenant reference structurally impossible. References
remain nullable only for rejections occurring before a trusted local object exists. Migration 0011
backfills historic records through tenant-scoped joins and limits the receipt fallback to the one
legacy review-required event that used it.
**Slice:** M4 evidence receipt hardening after external review

## 2026-08-26: Self-Approval Is Refused Structurally
**Decision:** Refuse an approval whose configured approver identity equals the requesting actor,
with reason `APPROVER_IS_REQUESTER`.
**Alternatives considered:** Continue relying on the agent not holding the approver token; check
only that the tokens differ.
**Rationale:** Separation of duties was real but incidental: the agent cannot approve because it
does not hold `DEMO_APPROVER_TOKEN`, not because anything refused a self-approval. A misconfigured
`DEMO_APPROVER_ID` matching the requesting actor would therefore record an approval as independent
review, and the evidence receipt would assert oversight that never happened. That is worse than no
approval, because the artifact would claim a control was exercised when it was not. The project's
scope document already listed self-approval among the blocked scenarios, so the guard makes the
claim true rather than aspirational. A second test proves the guard does not block a genuine
separate approver.
**Slice:** Security review, A5 registered in the Tier A matrix

## 2026-08-26: Untrusted Identifiers Are Refused, Not Parsed Optimistically
**Decision:** MCP tools parse agent-supplied identifiers through a helper that returns `None` on a
malformed value, and refuse it exactly as they refuse an unknown one.
**Alternatives considered:** Let `UUID()` raise; return a distinct malformed-input error.
**Rationale:** Tool arguments are untrusted input. Raising surfaced a parse error to the caller
instead of a refusal, and a distinct error would have told an agent whether a value was
badly formed or simply belonged to another tenant, which is the disclosure the tenant-scoped
lookups exist to avoid.
**Slice:** Security review

## 2026-08-26: The Test Suite Is Verified by Breaking the Code on Purpose
**Decision:** `scenarios/mutation.py` applies one deliberate break at a time to a safety-critical
line and requires the tests named as its guards to fail. It exits non-zero if any mutation
survives, restores each file in a `finally` block, and verifies the working tree against `git diff`
before reporting.
**Alternatives considered:** Trust the passing suite; adopt a general mutation-testing tool such as
`mutmut` or `cosmic-ray`; measure line coverage instead.
**Rationale:** A passing suite says the code behaves as written. It does not say the tests would
object if the code stopped doing something important, and those are different claims. This project
has evidence for the gap: request-scoped sessions never committed and 146 tests passed, and an
external reviewer found one P0 and five P1 defects that reading the code had not surfaced. Coverage
would not have caught any of them, because every one of those lines was executed — just never
asserted about. A general mutation tool generates thousands of mutants across the whole tree and
reports a percentage; the interesting question here is not a score but whether each named safety
invariant has a test that actually depends on it. Enumerating the invariants keeps the output an
answer rather than a metric, and it is short enough to read in an interview.
**Slice:** A — verification of the verification

## 2026-08-26: A Locked Read Must Overwrite the Identity Map
**Decision:** All row locking goes through `models.locking.locked()`, which pairs
`with_for_update()` with `execution_options(populate_existing=True)`.
**Alternatives considered:** Add `populate_existing` only at the state machine, where the defect was
found; expire objects before locking; drop `expire_on_commit=False`.
**Rationale:** `SELECT ... FOR UPDATE` through the ORM acquires the lock correctly and then discards
the row Postgres returned if that object is already in the session's identity map. A caller that
waits on the lock therefore waits for real and then decides from the state it read before waiting,
which is exactly the state the lock existed to hide. In `transition()` this permitted two callers
to authorize one payment: the second blocked as designed, received the committed `AUTHORIZED` row,
kept its cached `CREATED`, and moved the payment again. `expire_on_commit=False` removed the only
thing that would otherwise have refreshed it.

Fixing only the state machine would have left twelve other lock sites carrying the same trap,
including the single-use guards on `authority.used_at`, `approval.consumed_at`, and
`RazorpayOrder.provider_state`. Those are safe today only because no earlier line in the same
request happens to load those rows first — an accident of call order, not a guarantee, and not
something a reviewer can be expected to re-derive on every diff. Routing every lock through one
helper makes the pairing structural, and it matches the project's standing rule of enforcing an
invariant at the lowest layer that can hold it.
**Slice:** A — found by the mutation suite

## 2026-08-26: The Locking Rule Is Enforced Against the Source, Not Remembered
**Decision:** `tests/test_locking_discipline.py` asserts that `models/locking.py` is the only source
file containing `.with_for_update(`, and that the helper still pairs the lock with
`populate_existing`. A third test fails when a new package is added that the scan does not cover.
**Alternatives considered:** A comment on the helper; a code-review convention; a ruff custom rule.
**Rationale:** The defect this prevents is invisible at the call site. The code reads as correct,
the lock is genuinely taken, and whether the staleness is reachable depends on what some other line
in the same request loaded earlier. A convention fails silently the first time someone writes the
obvious thing, and the failure is a double authorization rather than a test error. A source-level
assertion is a blunt instrument, but it is checkable, it names the reason in its failure message,
and it fails at the moment the rule is broken rather than months later under concurrency. The
third test earned its place immediately by catching two packages omitted from the scan list when it
was first written.
**Slice:** A — verification of the verification

## 2026-08-26: A Signed Webhook Is Bounded in Time, Generously
**Decision:** A Razorpay webhook is refused if its signed `created_at` is older than
`RAZORPAY_WEBHOOK_MAX_AGE_SECONDS` (default 24 hours), more than five minutes in the future, or
absent. The check runs after signature verification and before any lookup.
**Alternatives considered:** No freshness check, relying on provider event dedupe alone; a tight
Stripe-style five-minute tolerance; reading the timestamp from a header.
**Rationale:** A signature proves Razorpay produced the event. It does not prove Razorpay produced
it recently, so without a bound a captured event is a permanent credential and anything able to
replay bytes from a log, a proxy, or an old environment holds one indefinitely.

The window cannot be tightened freely, and this is the part worth stating plainly. A provider
retries a webhook it could not deliver, and rejecting a late retry drops a real payment outcome:
money moved and the system never learns. That failure is strictly worse than the replay this
bounds, because duplicate delivery is already refused exactly and permanently by the unique index
on `provider_event_id`. The freshness window is therefore defence in depth over a dedupe that is
already correct, and it is set generously on purpose. Copying a five-minute tolerance from a
provider with different retry behavior would have traded a real availability failure for a
marginal security gain. This project has not verified Razorpay's documented retry schedule, so the
default is conservative and configurable rather than presented as tuned.

The tolerances are deliberately asymmetric. Backward tolerance is generous because retries are
legitimate; forward tolerance is five minutes because clock skew explains minutes and nothing
legitimate explains an event dated further ahead. Accepting a post-dated event would let a
signature carry validity past the window this exists to impose.

The timestamp is read from the signed body rather than a header, because a header sits outside the
HMAC and anything able to replay the event could rewrite it. A missing timestamp is refused with
its own reason code rather than as a malformed body, so an operator can tell a provider payload
change apart from an attack.
**Slice:** B - A14, the one Tier A scenario with no existing enforcement

## 2026-08-26: The Attack Matrix Is Completed by Registering What Already Held
**Decision:** All eleven remaining Tier A scenarios are implemented and registered. Ten of them
tested enforcement that already existed; only A14 required new code.
**Alternatives considered:** Implement only the scenarios that would find new defects; leave the
already-enforced ones documented as covered by unit tests.
**Rationale:** An unregistered defence is one nobody can point at, and more importantly one nobody
notices losing. The A9 transition table, the A6 raw-byte HMAC, and the A13 policy-version check
were all correct before this slice and none of them had a test framed as the attack they defeat.
Framing matters here beyond presentation: a scenario asserts the attack was refused, that no
provider order was created, and that no payment gained authority, which is three claims where a
unit test made one.

Writing them also corrected two claims the build plan had made loosely. A3 was listed as "currency
derives server-side"; the agent surface has no currency field at all, and the one route that
accepts one is disabled by default, which is a stronger and differently shaped defence than the
plan described. A10 was listed as "double refund"; there is no refund path anywhere in the project,
so the honest scenario asserts that no surface can initiate a refund - checked against the live
route table and tool list rather than against memory - alongside the ledger invariant that would
apply if one were added.

A13 registers a control case that succeeds. A rejection test proves nothing unless the thing being
rejected would otherwise have worked, and an authority built wrong would satisfy every rejection
assertion while testing nothing.
**Slice:** B - Tier A completion

## 2026-08-26: The Console Renders Rows and Cannot Create Them
**Decision:** Every console route is a GET that reads existing rows. There is no approve,
authorize, or retry control, and `tests/test_console.py` asserts against the application's live
route table that no state-changing path under `/console` exists.
**Alternatives considered:** Buttons to drive the three demo flows from the page; an approve
control so the approval flow could be completed on screen.
**Rationale:** The project's claim is that authority is held by the server and reachable only
through the checked paths. A console with an approve button would be a new path to authority, and
the claim would need an asterisk exactly where it is being demonstrated. A demonstration of a
safety property must not weaken the property.

The demo therefore drives flows from outside the console and the page witnesses them. That is a
small cost at recording time and it keeps A15's statement - that no reachable surface grants
payment authority - true without qualification.
**Slice:** D - M6 console

## 2026-08-26: The Console Is Off Unless Asked For, and Carries Tenant Identity in the Path
**Decision:** `/console` requires `ENABLE_CONSOLE=true` and takes the tenant id as a path segment.
Disabled, it answers 404.
**Alternatives considered:** Ship it always reachable; keep header identity and accept that
receipts cannot be opened in a browser; a session cookie.
**Rationale:** Two separate problems meet here. Browsers cannot set `X-Tenant-Id`, so the API's
header identity is unreachable from a browser and the receipt route could not be opened during a
demo at all - the console would show a timeline and never open a single receipt. The path carries
it instead, exactly as the checkout page already carries an order id.

That is the same testbed-grade identity the project already documents as not being production
authentication, moved rather than weakened, but it is more exposed: a tenant id in a URL lands in
browser history and appears on screen in a recording. Acceptable only because every tenant here is
synthetic, and the reason the surface is gated. A demo surface that ships reachable is one somebody
deploys by accident.

The disabled response is 404 rather than 403 so a deployment that never enabled it does not
advertise that the route exists.
**Slice:** D - M6 console

## 2026-08-26: The Timeline Links to the Receipt Instead of Redrawing It
**Decision:** The console renders a comparative timeline and links each row to the existing
receipt. It does not re-render the proposed/derived/provider stages.
**Alternatives considered:** A full three-column view inside the console; a combined page.
**Rationale:** `api.receipt.render_receipt` already lays out proposed against derived against
provider outcome, which is the three-column view the build plan asked for. A second renderer
assembling the same facts is a second opinion about what happened, and the receipt exists precisely
so that the readable record and the JSON record cannot disagree.

What was actually missing is different in shape. A receipt is deep and singular; a viewer arrives
wanting the comparison - a safe purchase, an approval-gated one, and a refused attack in one
column, where the difference is visible without reading three pages. The most important cell is on
the refused row: whether anything reached the provider, derived from whether a provider order
exists rather than from the decision, because a rejection that still created an order would not be
a rejection.
**Slice:** D - M6 console

## 2026-08-26: The Demo Opens With Our Own Code Failing
**Decision:** `python -m demo.unguarded` runs the same agent over the same poisoned catalog against
an adapter with no policy layer, and the injected instruction executes. It is committed as a
first-class demonstration with its own tests.
**Alternatives considered:** Open with the three passing flows; describe the risk in narration;
compare against an external product or MCP surface.
**Rationale:** Three clean refusals prove the system works and are weak at showing why anyone needs
it - a viewer can watch three green results and never feel the risk. Naming the risk in narration
asks for trust; running it does not.

Comparing against another product was rejected on its merits, not only because it is
sponsor-sensitive. It would depend on a surface outside this repository that changes without
notice, and it would frame someone else's product as inadequate rather than making this project's
case. The unguarded baseline demonstrates the same general problem using only code we control, and
the argument is stronger for being self-inflicted.

The comparison is deliberately narrow. TrustGate does not inspect the proposal and cleverly
identify it as hostile. `PurchaseProposal` has no field an amount or a merchant could be written
into, so those values are discarded before anything is decided. The unguarded adapter differs in
exactly one respect: it believes them. Both paths are given one identical model response so neither
can be accused of having been fed different inputs.
**Slice:** D - M6 unguarded baseline

## 2026-08-26: Deliberately Vulnerable Code Must Be Provably Inert, and Provably Vulnerable
**Decision:** The baseline imports no network client, no provider adapter, and no database session,
and charges an in-process ledger that lives for one function call. A test parses the package's
imports and fails if any of those appear. A second test asserts the baseline is still exploitable.
**Alternatives considered:** Point it at the existing mock provider; guard it behind an environment
flag; keep it uncommitted and reproduce it live during the demo.
**Rationale:** Vulnerable code in a payments repository has to be inert in a way a reader can
check. A docstring promising it cannot charge anyone is worth nothing; an import test that fails
the moment someone adds `httpx` is worth something. The mock provider was rejected for the reason
it looked attractive: anything with a base URL can be pointed somewhere else, and this has no
address at all.

The second test is the one that is easy to leave out. If someone hardens the adapter, every other
test still passes and the demo silently begins showing two identical refusals - while the pitch
goes on claiming a contrast the screen no longer shows. That is worse than deleting the
demonstration outright, because it fails without telling anyone.
**Slice:** D - M6 unguarded baseline

## 2026-08-26: Staging Resets History and Leaves Configuration Alone
**Decision:** `python -m agent.stage` clears the demo tenant's transactional rows between takes and
creates its tenant, policy, merchant, and catalog only when absent. The tenant id is fixed.
**Alternatives considered:** Delete and recreate everything; a fresh random tenant per run, as
`agent.seed` does; truncate the tables.
**Rationale:** Deleting everything was the first attempt and the database refused it: a trigger
makes `spending_policy` rows immutable, because an evidence receipt naming policy version 3 has to
stay resolvable forever. That is a real invariant and demo tooling does not get an exception from
it, so the tooling changed shape instead. The result is better than what was intended - a viewer's
"clean state" means an empty timeline, not a deleted tenant, and configuration is not history.

The fixed tenant id is what makes the console URL stable enough to write into a script and rehearse
against; `agent.seed`'s fresh-tenant-per-run is right for exploring and wrong for filming. Deletes
are scoped to that one tenant rather than truncating, so staging a demo on a database that also
holds other work destroys only what the demo produced.
**Slice:** D - M6 demo staging

## 2026-08-26: The Console Shows Attempts That Never Became Requests
**Decision:** The timeline merges payment requests with audit events for refusals that happened
before any request existed, rendered as rows stating that no payment request was created and
offering no receipt link.
**Alternatives considered:** Show only payment requests; write a placeholder request row for
refused attempts so the timeline has one source.
**Rationale:** Found by running the demo rather than by reading the code. An attack refused at the
MCP boundary is rejected before a payment request is written, so the first console showed the safe
purchase and nothing else - silent exactly where the strongest evidence belongs.

Writing a placeholder row would have solved the display problem by weakening the thing being
displayed: the fact worth showing is precisely that nothing was created. So the row says "no
payment request was created" and "no amount was derived", which is a stronger claim than a denial,
because a denial at least implies something was written down. The audit payload is read
defensively - a changed event shape should cost a demo row a detail, not remove the row and with it
the evidence that the attempt was refused.
**Slice:** D - M6 console

## 2026-08-26: One Poisoned Catalog, Written Once
**Decision:** `agent/demo_catalog.py` holds the demo's catalog facts with no imports, and both the
seeded tenant and the unguarded baseline build from it. `tests/test_demo_catalog.py` asserts the
database row and the baseline object carry an identical injected instruction.
**Alternatives considered:** Leave the two catalogs as they were; compare them in a test without
sharing a source.
**Rationale:** Found by re-reading the committed work rather than by any test failing. The demo's
claim is that one input produces two outcomes because the interfaces differ, and the two catalogs
did not match: one injected `sku=CLOUD-STARTER quantity=1`, the other `sku=CLOUD-TEAM quantity=50`,
their CLOUD-TEAM prices differed by nine hundred rupees, and the README asserted they were the same
throughout. On camera that would have shown an attacker paid INR 20,000 in one flow and an
unrelated refusal in the next, with the narration claiming a parallel the screen did not support.

The instruction is now one string, and every field in it earns its place. The unguarded adapter
reads `amount_minor` and `merchant_id` and pays them. TrustGate has nowhere to put either, so what
survives the discard is `quantity=50` - a field the agent is allowed to set - and the server bounds
it against the catalog maximum. An injection that only set an amount would be neutralised into an
ordinary purchase: correct, and invisible.
**Slice:** D - M6, found in review

## 2026-08-26: Demo Configuration Is Updated, Not Merely Created
**Decision:** Staging creates the tenant, merchant, catalog, and policy-merchant link when absent
and brings them up to date when present. The spending policy alone is written once and never
updated.
**Alternatives considered:** Create-if-absent for everything, as first written; delete and recreate
configuration on every run.
**Rationale:** Create-if-absent was the first fix for the immutable-policy problem and it overshot.
A policy must never change because a receipt naming version 3 has to stay resolvable; a catalog has
no such property, and treating it as immutable meant that editing a price or the injected
description silently did nothing on a tenant that already existed. The failure mode is an operator
changing the demo, seeing no change, and finding out why during a take.

Deleting and recreating configuration was not available: the policy trigger refuses it, which is
the constraint that started this. So the rule is now stated where it belongs - immutable rows are
written once, mutable ones are kept current - rather than applied uniformly because one row
happened to need it.
**Slice:** D - M6, found in review

## 2026-08-26: The Approval Is Granted by a Separate Command Holding a Separate Token
**Decision:** `python -m agent.approve` completes the demo's approval flow over the same HTTP route
an operator would use, carrying `X-Approver-Token`, under an identity read from `DEMO_APPROVER_ID`.
It finds the pending purchase itself rather than taking a copied identifier.
**Alternatives considered:** Have the buyer agent approve its own request once it sees
`APPROVAL_REQUIRED`; add an approve button to the console; leave the flow undemonstrated and
describe it in narration.
**Rationale:** The middle flow could be described and not shown - the agent reaches
`APPROVAL_REQUIRED` and stops, and nothing carried it further. Letting the agent approve itself
would have made the demo shorter and the separation of duties fictional, and the server would
refuse it anyway: an approval whose approver matches the requester is rejected as
`APPROVER_IS_REQUESTER`. A console button was rejected for the same reason the console has no
buttons at all.

Finding the pending purchase by state rather than by a pasted identifier removes one thing to
fumble on camera and keeps the two commands independent. Refusals are reported as sentences with
the server's reason code, because a stack trace mid-take is a worse failure than the one it
describes.
**Slice:** D - M6 approval flow

## 2026-08-26: A Count That Is Not Tenant-Scoped Asserts the Wrong Thing
**Decision:** `test_mcp_requests_approval_for_an_approval_required_payment` counts approvals for
its own tenant rather than for the whole database.
**Alternatives considered:** Delete the demo tenant's committed rows so the global count matched
again; assert a before-and-after delta.
**Rationale:** The test broke the first time real demo data was committed, which is the tell: it
was asserting something about every tenant while claiming to test one. Making the database tidy
enough for the old assertion to pass would have preserved the flaw and hidden it again - and the
same assertion would have passed happily while this tenant gained an approval and another lost one.

Found by running the demo, not by reading the test.
**Slice:** D - M6, found in review

## 2026-08-26: A Refusal and an Unpaid Authorization Are Different Facts
**Decision:** The console renders three outcomes rather than two: refused before a request existed,
authorized with nothing sent to the provider, and an order the provider actually holds. The tally
counts the middle state separately, and an approval turns a row green only once a human has
granted it.
**Alternatives considered:** Leave the wording; drop the middle state by only showing rows that
reached the provider.
**Rationale:** A refusal and an authorization awaiting checkout both rendered as "Nothing reached
Razorpay", which made a working purchase indistinguishable from a blocked one. Worse, it
contradicted the sentence the demo needs to say while pointing at the row: that the agent obtained
an authorization and did not obtain the ability to pay. That gap is the design, not a missing step,
so it needs its own words rather than borrowing a refusal's.

The row colour for an approval now follows whether a human granted it rather than which state the
payment reached. An approval is a human decision and the payment moving is its consequence;
inferring the decision from a state that several paths can produce says less and can be wrong.
**Slice:** D - M6 console

## 2026-08-26: Findings From a Whole-Repository Audit
**Decision:** Three defects fixed: the merged console timeline is bounded once rather than twice,
both console pages carry a strict Content-Security-Policy with `no-referrer`, and the timeline
reads its related rows in a constant number of queries. Three further findings are documented
rather than fixed, with reasons.
**Alternatives considered:** Fixing everything found; documenting everything found.
**Rationale:** Audited for committed secrets, unscoped tenant queries, non-constant-time secret
comparisons, unescaped HTML interpolation, assertions in shipped code that `python -O` would strip,
resource limits, and dependency currency. Secrets, tenant scoping, crypto, escaping, and assertions
came back clean; the scan for tenant-scoped selects without a tenant filter returned exactly one
hit, and that one derives its tenant from the row it found and scopes everything downstream to it.

The three fixed defects were real. Payment requests and boundary refusals were each capped at the
timeline limit and then merged, so a busy tenant could render twice the page anyone asked for. The
checkout page set a CSP while the two pages that actually render third-party catalog text set
nothing - the wrong way round, since escaping is what those pages depend on and a policy is what
should stand behind it. `no-referrer` was added for a reason specific to this design: the tenant id
is in the path, so any outbound navigation would put it in a Referer header. And the timeline cost
a round trip per column per row, which was bounded and still the wrong shape to leave in a
repository people are invited to read.

Documented and not fixed: no rate limiting on token-protected routes, no request body limit outside
the webhook, and a checkout page reachable by provider order id without authentication. The last is
how hosted checkout works and is deliberate. The first two are real and belong to a deployment
posture this testbed does not claim to have; naming them in `docs/limitations.md` is honest, and
building a rate limiter here would be inventing a production concern the project explicitly says it
does not address.
**Slice:** Full-repository audit

## 2026-08-26: The Demo Can Now Reach the Provider
**Decision:** `python -m agent.checkout` issues a checkout authority and creates the provider order
it permits, then prints the payment URL. Both calls go over HTTP with the tenant header, using the
operator routes that already existed.
**Alternatives considered:** Have the buyer agent do it; add a button to the console; leave the demo
ending at `AUTHORIZED`.
**Rationale:** The demo stopped at `AUTHORIZED`, which is the interesting state and not a finished
story, and the console's "reached the provider" counter therefore read zero for every row - a screen
on which a working purchase and a blocked one looked equally inert.

The agent does not do this, and that is the point being demonstrated: it obtained an authorization
and did not obtain the ability to pay. Wiring these calls into the buyer would have handed it the
second thing while the narration claimed it only had the first. A console button was rejected for
the reason the console has no buttons.

It selects a purchase that is authorized and has no provider order yet. The server refuses a second
order for one authority, but a demo that asks for one and gets refused on camera is worse than one
that does not ask.
**Slice:** D - M6 checkout

## 2026-08-26: A Fixture Must Not Race the Database Clock
**Decision:** Test fixtures set `CheckoutAuthority.used_at` with `func.now()` rather than
`datetime.now(UTC)`, and `tests/test_fixture_discipline.py` fails on any reintroduction.
**Alternatives considered:** Retry the flaky test; widen the CHECK constraint to tolerate skew;
synchronise the container clock.
**Rationale:** One intermittent failure in a concurrency test, reproduced on the second of
twenty-five full runs, turned out to have nothing to do with concurrency. `created_at` is
`server_default=func.now()`, stamped by Postgres from Postgres's clock, and the constraint
`used_at >= created_at` therefore held only while two machines agreed about the time. They did not:
the Docker Desktop container clock measured 854ms ahead of the Windows host while this was being
diagnosed, and it drifts.

Seven test files carried the fault. Widening the constraint was rejected outright - it is a real
invariant about a real ordering, and loosening a production rule to accommodate a test fixture is
the wrong direction. Synchronising the clock fixes one machine and not CI, not a teammate's, and
not the next one.

The failure was expensive to find for a reason worth recording: it surfaced several layers from its
cause, in an assertion that reported a bare count and discarded the exception it had been handed.
The diagnostic fix landed first, and this was found immediately after.
**Slice:** Flake investigation

## 2026-08-26: An Absence Assertion Must Prove Its Search Can Find Things
**Decision:** Route scans go through `served_routes()`, which descends into included routers, and
every caller first runs `assert_route_scan_works()` against paths known to exist.
**Alternatives considered:** Fix the walk and leave the assertions as they were.
**Rationale:** Found while chasing a 404 in the demo's checkout URL. FastAPI keeps an included
router as a wrapper exposing `original_router` rather than flattening its routes into
`app.routes`, so a walk that follows only `.routes` sees five paths - the framework's own - and no
application route at all.

Three tests asserted "no route under X does Y" against that walk. All three passed by finding
nothing, including `test_a10_no_surface_anywhere_can_initiate_a_refund`, which is a registered Tier
A scenario and appears in the published attack matrix. The matrix was claiming a covered attack
whose test examined an empty list.

Fixing the walk alone would have left the same shape in place: an assertion that something is
absent is worth nothing unless the search is known to be capable of finding it. So the scan is now
verified against known-present routes before any absence is claimed, and both scans were confirmed
to fail against a deliberately added `POST /console/{tenant_id}/refund` - which neither noticed
before.
**Slice:** Found while fixing the checkout URL

## 2026-08-26: The Checkout URL Is Checked Against the Route Table
**Decision:** `agent.checkout` builds its page URL from `CHECKOUT_PAGE_PREFIX`, and a test asserts
that path is one the application actually serves.
**Alternatives considered:** Leave it as a string literal.
**Rationale:** It was built as `{base}/checkout/{order_id}` while the page lives under the razorpay
router's prefix at `/api/v1/razorpay/checkout/{order_id}`. Every test around it mocked the HTTP
layer, so nothing noticed until a browser opened the link - after a real provider order had been
created and a one-time authority spent. That is the most expensive point in the flow at which to
find a typo, and on a recording it would have been unrecoverable without restaging.
**Slice:** D - M6 checkout

## 2026-08-27: The Optimized-Mode Claim Is Now Tested, Not Just Stated
**Decision:** `make verify-optimized` and a CI step run the harness and Tier A suites under
`python -O`. Shipped packages contain no `assert` at all, enforced by
`tests/test_optimized_mode.py`.
**Alternatives considered:** Leave the reasoning in the harness docstring, as it had been for the
whole project; run the entire suite optimized rather than the safety scenarios.
**Rationale:** `scenarios/tier_a/harness.py` opens by explaining that it raises `ScenarioViolation`
rather than using `assert`, because `python -O` strips assertions and an assert-based harness would
"report every scenario as passing under optimization while verifying nothing". That reasoning is
correct, it is one of the better decisions in this project, and nothing had ever run under `-O` to
confirm it. The build plan's own Definition of Done required exactly this and it was never done.

Reintroducing the mistake settled it. Replacing the harness's five explicit raises with asserts and
running the harness suite optimized produces `DID NOT RAISE` on every violation case: under `-O`
the checks do not weaken, they disappear. Normal mode caught it too, but for the uninteresting
reason that the exception type changed.

The two remaining asserts in shipped code were type narrowings after a JSON parse, in demo tooling
rather than the authorization path. They are explicit raises now, so the rule can be absolute and
therefore checkable. Tests are excluded from that rule because pytest rewrites their assertions
into explicit raises, which survive optimization.

Only the safety scenarios run optimized rather than the whole suite: the property being verified is
that the safety checks survive, and a full second suite run for that would cost minutes on every
push to prove nothing further.
**Slice:** M0 backfill, found by auditing the build plan's Definition of Done

## 2026-08-27: An Expired Policy Could Have Spent a Checkout Authority
**Decision:** A13 gains a fourth test and the mutation suite a matching entry: an authority bound to
a policy that has since expired is refused, and deleting that condition now fails a test.
**Alternatives considered:** None. This was a hole, not a trade-off.
**Rationale:** `consume_checkout_authority` revokes an authority on four conditions - a missing
policy, a version that moved, an expired policy, and a purchase whose snapshot hash no longer
matches. Two of them had tests and mutations. The expiry did not.

Deleting `or policy.expiry <= datetime.now(UTC)` and running the full suite left all 302 tests
green. An authority issued under a policy that had since run out could have been spent, and nothing
in the project would have objected.

The new test isolates expiry deliberately: the authority is bound to the newest policy version, so
version drift cannot be what refuses it and the expiry is the only condition left. Found by
enumerating the branches of a safety check and asking which of them anything actually watches -
which is the same question the mutation suite exists to ask, applied by hand to a function whose
conditions had never been listed out.
**Slice:** Second full-repository audit

## 2026-08-27: Regulatory Context Is Labelled by the Evidence Behind It
**Decision:** `docs/positioning.md` states each Indian payments item with what kind of evidence
supports it - a primary NPCI circular, press reporting only, or a recommendation in a government
report - and claims conformance with none of them.
**Alternatives considered:** Write the parallels without labels, as the build plan's draft did;
omit the regulatory context entirely.
**Rationale:** The build plan flagged these items as coming from press coverage and required either
primary sources or an explicit "reported" marking. Searching separated them, and they turned out to
have genuinely different standing:

UPI Circle has an NPCI circular, `UPI-OC-No-201-FY-24-25`, and an addendum. It is a shipped,
documented delegation primitive whose shape - a principal granting bounded spending authority to a
delegate, enforced centrally - is the same one this project implements. That is the strongest item
on the page because it is the only one that already exists.

The Unified Agent Protocol is press reporting. No NPCI circular, specification, or press release
was found. The page says so in those words, because the people most likely to read this project are
the people most likely to know.

The human-in-the-loop threshold is a recommendation in CERT-In's Digital Threat Report 2025-26,
reported by press, with no threshold named. It resembles `approval_required_above_minor` closely,
and the page says the resemblance is structural rather than conformance.

A test enforces the labels and forbids conformance verbs near any named body. Its first version
flagged any sentence mentioning a regulator at all, which caught the page's own sourced
descriptions - a check that would have pushed the writing toward vagueness rather than accuracy.
The failure mode worth catching is a verb, not proximity.
**Slice:** E - M7 positioning

## 2026-08-28: Every Safety Branch Was Probed, Not Just the Enumerated Ones
**Decision:** Nine conditions across the policy evaluator and approval consumption were deleted one
at a time and the suite run against each. Seven were caught. The two that were not are a redundant
pair guarding one property, and removing both together fails a test.
**Alternatives considered:** Trust the mutation suite's eighteen entries; add mutations for all
nine regardless.
**Rationale:** The mutation suite guards invariants somebody thought to enumerate, so anything
never enumerated is invisible to it. That is how an unguarded policy-expiry condition survived two
audits. Walking the branches by hand asks the same question of the ones nobody listed.

The policy evaluator came back fully covered: currency, merchant, per-payment limit, daily limit,
approval threshold, and expiry each fail a test when deleted. The approval threshold is worth
naming - deleting it makes every purchase ALLOW, removing the human-in-the-loop control entirely,
and a test objects.

Approval consumption reported two unguarded branches, and that was the probe measuring the wrong
thing. `consumed_at is not None` and the atomic `rowcount != 1` claim both prevent one approval
being spent twice, so deleting either leaves the other covering it. Deleting both fails
`test_a4_an_already_consumed_approval_cannot_authorize_again`. They are kept because they do
different work: the early check gives a clear error before an update is attempted, and the atomic
claim is what holds when two callers race.

No mutations were added for the seven already-caught branches. A mutation earns its place by
guarding something a test would otherwise miss, and eighteen entries that each say something
distinct are worth more than twenty-seven where nine restate what the suite already proves.
**Slice:** Third full-repository audit

## 2026-08-28: The Intermittent Race Test Was Reading the Clock Twice
**Decision:** Pin the reservation day in
`test_daily_spend_reservation_race_allows_only_one_final_reservation`, and assert explicitly that
neither racer raised.
**Alternatives considered:** Keep hunting a failure that would not reproduce; mark the test flaky
and move on; change how production derives the spend day.
**Rationale:** The test failed roughly one run in twenty and four hypotheses had already been
falsified. Thirty-eight consecutive clean runs said more hunting was not going to find it, so the
code was read instead of run.

Each racer derives its own `spend_date` from the wall clock. Two racers straddling UTC midnight
land in different day buckets, never contend, and both reserve against a limit meant to admit one.

Production is left alone. Two requests either side of midnight each getting a fresh daily budget is
what a daily budget means, and the boundary of a day-bucketed limit is inherently fuzzy by however
long it takes to read a clock. Only the test needed to stop depending on what time it runs.

The second assertion change matters as much. `results.count(False) == 1` could not distinguish a
racer returning False from a racer raising, so a dropped connection under memory pressure would
have reported a count mismatch rather than the exception that caused it. It now names the exception.

Verified by deleting the daily-limit guard from the conditional upsert: the test still fails, so
pinning the day removed nondeterminism without removing what the test proves.
**Slice:** Third full-repository audit


## 2026-08-28: Delegation Partitions a Budget Instead of Intersecting a Capability
**Decision:** Multi-hop delegated authority in which each hop narrows its parent on every
dimension, and the budgets of the hops below a node are subtracted from it rather than compared
against it.
**Alternatives considered:** Per-edge narrowing alone, as the capability literature describes it;
signed capability tokens in the manner of macaroons, Biscuit, or the IETF attenuating-token draft;
leaving delegation out of scope.
**Rationale:** Attenuation is defined over capabilities as sets, where a child no wider than its
parent can never widen the chain because sets intersect. Money is not a set. Two children each
granted exactly the parent's budget satisfy every per-edge comparison and hold twice the parent's
budget between them. `ck_delegation_budget_partitioned` refuses the sum on the parent's own row,
and `delegation.grant` claims the allocation with a conditional update so two siblings racing for
the last of a budget cannot both find room.

The mutation named `delegation-aggregate-partition` is the evidence that this is a real distinction
rather than a stylistic one: with the aggregate claim deleted, every other delegation test still
passes.

Revocation cascades instead of propagating. A signed capability carries its own authority, so
revoking it means recalling it - hence revocation sitting on the open-problems list for that whole
family of designs. A hop here re-derives its authority from its entire chain on every spend, so
cutting a link is already the end of the branch, and the test asserts the descendant stopped
without its row ever being written to.

The cost is stated in `docs/limitations.md` rather than hidden: a hop cannot be verified offline or
away from this system. That is the mirror of what the token designs give up, and it was chosen
knowingly.
**Slice:** Delegation chains
