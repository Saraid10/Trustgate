# Architecture

**Current as of 2026-08-25.** 27 commits, 152 tests passing, `mypy --strict` clean across 37
source files.

TrustGate is an authorization layer that sits between an AI buyer and payment execution. The agent
may propose; an independent server-side authority decides; the decision is evidenced.

---

## The layers

```
┌──────────────────────────────────────────────────────────────────────┐
│  agent/            Buyer agent — thin, untrusted, replaceable        │
│                    Proposes sku / quantity / purpose. Nothing else.  │
└──────────────────────────────────────────────────────────────────────┘
                              │  the narrow MCP contract
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│  mcp_server/       Five tools. No authorize, capture, or provider    │
│                    tool exists. Tenant and actor come from process   │
│                    configuration, never from tool arguments.         │
└──────────────────────────────────────────────────────────────────────┘
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│  THE AUTHORIZATION CORE — this is the product                        │
│                                                                      │
│  policy_engine/    Pure rule evaluation + atomic daily budget        │
│  state_machine/    Row-locked lifecycle transitions                  │
│  api/routes/       Catalog derivation, approval, checkout authority  │
│  models/           Invariants enforced as database constraints       │
└──────────────────────────────────────────────────────────────────────┘
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Provider execution     Razorpay Test Mode  ·  mock provider         │
└──────────────────────────────────────────────────────────────────────┘
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│  api/routes/evidence.py   Receipt: proposed / derived / provider     │
└──────────────────────────────────────────────────────────────────────┘
```

**Delete the agent and the authorization core is unchanged and still correct.** That property is
the thesis. If the agent were central, the project would be arguing against itself.

---

## Where each decision is enforced

The governing rule is **enforce at the lowest layer that can hold it**: database constraint over
transaction, transaction over application code, application code over convention.

| Invariant | Enforced by | Layer |
|---|---|---|
| No cross-tenant parent reference | Composite foreign keys on `(tenant_id, id)` | Database |
| One active approval per request | Partial unique index where `consumed_at IS NULL` | Database |
| Published policies are immutable | PostgreSQL trigger | Database |
| Valid payment states only | `CHECK` constraint on the state column | Database |
| Amounts never negative | `CHECK` constraints | Database |
| One provider order per authority | Unique constraint on `(tenant_id, checkout_authority_id)` | Database |
| Two requests cannot jointly exceed the daily limit | Conditional upsert with a `WHERE` predicate | Transaction |
| An approval is consumed once | Conditional update inside the transition transaction | Transaction |
| An authority is claimed once | `FOR UPDATE` on the authority row | Transaction |
| Policy cannot drift mid-issuance | `FOR UPDATE` on the tenant row | Transaction |
| Amount and merchant derive from the catalog | Schema `extra="forbid"` plus server lookup | Application |
| Webhook authenticity | HMAC over raw bytes before parsing | Application |

---

## Trust boundaries

- **Tenant identity** is testbed-only via `X-Tenant-Id`, resolved server-side. Request bodies are
  never trusted for tenant identity.
- **Agent input** is untrusted. The agent influences `sku`, `quantity`, and `purpose` and nothing
  else. `quantity` is the only lever on amount, and it is bounded by the catalog item's maximum.
- **Catalog descriptions** are third-party content, stored in `description_untrusted`, and reach
  the model verbatim. They are product information, never instructions.
- **Webhooks** are untrusted until HMAC verification over raw body bytes succeeds.
- **Browser callbacks** are never payment proof. Only verified provider events progress state.
- **Provider credentials** never reach the agent or any MCP tool.

---

## What is built

### Complete

**M0 — Clean-clone reliability.** The `localhost`/IPv6 hang fixed, `pytest-timeout` added, four
PostgreSQL multi-session concurrency races backfilled for boundaries that were previously proven
only sequentially.

**M1 — Buyer agent and adversarial harness.** A narrow agent over the MCP contract, deterministic
substitutes for the suite, and three live model backends behind one switch
(`TRUSTGATE_MODEL_BACKEND`): Anthropic direct, Amazon Bedrock, and Groq. Untrusted influence is
*measured* by proposing twice — once against a description-free catalog — rather than
self-reported. Live evidence preserved under `docs/evidence/`.

**M2 — Early attack proof.** A before-and-after snapshot harness and Tier A scenarios A1, A2,
A11b, and A15. Every scenario proves three things: the rejection with its reason code, that no
provider order was created, and that no payment gained authority. The harness raises
`ScenarioViolation` rather than asserting, so it survives `python -O`, and it has self-tests that
feed it a successful attack and require it to object.

**M3 — Razorpay Test Mode.** Order creation from a consumed authority proven end to end against
the real provider. Replay returns the same order rather than creating a second.

**M4 — Evidence receipt (JSON).** `GET /api/v1/payment-requests/{id}/evidence`, keyed on the
request so denied attempts are evidenced too. Cross-tenant reads are byte-identical to unknown
identifiers.

### Remaining

| Milestone | Scope |
|---|---|
| **M3 completion** | Razorpay webhook with raw-byte HMAC; Standard Checkout page |
| **M4 completion** | HTML receipt rendering the same data |
| **M5** | Tier A A3–A14, each emitting a receipt |
| **M6** | Three-flow console and demo recording |
| **M7** | Positioning, limitations, submission |

---

## Verification architecture

The most important thing learned building this: **a passing suite is not evidence that the system
works.** Three real defects were found by running the system, none by adding to it.

| Defect | How it hid | Found by |
|---|---|---|
| Request-scoped sessions never committed — the API returned `201 ALLOW` and persisted nothing | Tests inject a session already inside a transaction and assert within it, so saved and unsaved look identical | Running the Razorpay flow end to end for the first time |
| Daily budget was reserved and never released, letting a compliant agent lock out an actor for a day | No test exercised abandonment | Auditing the running system against its own decision log |
| The budget fix then refunded budget never reserved, letting an agent manufacture it | — | An existing policy test |

This shapes the verification layers:

- **Unit and route tests** — fast, isolated, rolled back. Cannot observe persistence.
- **`tests/test_session_lifecycle.py`** — bypasses the fixture and drives the session dependency
  directly against PostgreSQL. Confirmed to fail when the defect is reintroduced.
- **Concurrency races** — separate committed sessions, because a sequential "call it twice" test
  passes even when a `FOR UPDATE` is missing.
- **Scenario harness** — asserts what *changed*, not what was returned. Self-tested so it cannot
  pass vacuously.
- **Optimized-mode smoke tests** — `python -O` strips assertions, so safety checks must observe
  real runtime behaviour.
- **Live runs** — the only layer that has actually caught anything structural.

### Planned additions

1. **Live conformance in CI** — the real path against a real database, so the session-commit class
   of defect is caught the day it lands.
2. **Mutation testing on the safety core** — remove a `FOR UPDATE`, drop a tenant filter, delete a
   `CHECK`, and require the suite to fail. A test that has never failed proves nothing; this proves
   the whole suite at once.
3. **Property-based sequences** — for any ordering of reserve, release, and transition, reserved
   budget is never negative and never exceeds the daily limit. Exactly the class of bug the budget
   fix introduced and examples missed.

---

## Deliberate simplifications

Named here rather than discovered by a reviewer.

- `X-Tenant-Id` is testbed identity, not production authentication.
- One shared provider webhook secret; production would scope and rotate per tenant.
- One shared Razorpay key across tenants, so tenant isolation does not extend to the provider.
- The Razorpay callback lookup is not tenant-scoped. The callback is unauthenticated by nature and
  the provider order ID carries a global unique constraint, but this is inconsistent with the
  project's own rule that tenant-scoped lookups filter by tenant.
- No rate limiting or body-size limit outside the webhook route's 64 KiB cap.
- Authority consumption is fail-closed: an infrastructure failure after the claim requires manual
  recovery rather than risking a duplicate provider order.
- Evidence receipts are tamper-evident demo artifacts, assembled at read time and not signed. They
  are not legally non-repudiable records.
- Refunds do not release daily budget, so a refund cannot reopen a spent day.
