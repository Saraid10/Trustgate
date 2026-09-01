# Limitations

TrustGate is a safety testbed. It demonstrates that an AI agent can propose a purchase without
gaining authority to decide the merchant, amount, currency, approval, or provider outcome — and it
demonstrates that against a real payment provider in Test Mode, with synthetic data.

It is not a payment processor, and several things a production system would need are deliberately
absent. This page names every one of them, including the ones that are unflattering. A limitation
you have to discover is worse than one that is written down.

---

## What this is not

- **Not a payment processor.** Razorpay moves the money; TrustGate decides whether it should be
  asked to.
- **Not a compliance product.** No PCI DSS, RBI, NPCI, or SOC 2 claim is made or implied.
- **Not a fraud model.** Nothing here scores risk or detects anomalies.
- **Not a legal-consent system.** Approval here is a separation-of-duties control, not consent
  capture.
- **Not Live Mode.** Test Mode only. `.env.example` says a `rzp_live_` key must never be placed in
  this repository, and nothing in the codebase is designed for one.

---

## Identity and authentication

**Tenant identity is an `X-Tenant-Id` header, and that is not authentication.**

`api/dependencies.py` resolves a tenant by looking up the header value. Anyone who can reach the API
and knows a tenant's UUID is that tenant. There is no signature, no session, no key exchange.

This is deliberate and it is the largest single gap between this testbed and a production system.
Every tenant-scoping test in the suite proves that one tenant cannot reach another *given* correctly
resolved identity; none of them prove the identity itself is trustworthy, because it is not.

**The demonstration console carries the tenant id in the URL path.**

Browsers cannot set headers, so `/console/{tenant_id}` takes it from the path. That is the same
testbed-grade identity moved, not weakened — but a URL is more exposed than a header: it lands in
browser history, and it appears on screen in a recording. The console is therefore off unless
`ENABLE_CONSOLE=true`, and every tenant in this project is synthetic.

**The approver is a single shared token.**

`DEMO_APPROVER_TOKEN` and `DEMO_APPROVER_ID` are one identity for the whole deployment. Separation
of duties is genuinely enforced — the server refuses an approval whose approver matches the
requester — but "a different person approved this" is only as true as the token's distribution,
which in a testbed is not very.

---

## Provider integration

**One webhook secret for all tenants.**

`RAZORPAY_WEBHOOK_SECRET` is a single environment value. A real multi-tenant system would hold a
secret per tenant, so that one tenant's compromised secret could not forge another's events. Here,
one secret verifies everything.

**Webhook freshness is bounded generously, not tightly.**

Events older than `RAZORPAY_WEBHOOK_MAX_AGE_SECONDS` (default 24 hours) are refused. That window is
wide because a provider retries failed deliveries, and rejecting a late retry loses a real payment
outcome — a worse failure than the replay it would prevent, given that duplicate delivery is
already refused exactly by the unique index on provider event identity. This project has not
verified Razorpay's documented retry schedule, so the default is conservative rather than tuned.

**Fail-closed authority consumption requires a human after an infrastructure failure.**

If the process dies between claiming a checkout authority and recording the provider order, the
retry path asks Razorpay what actually happened. If the answer is ambiguous — more than one order
matching the receipt — the intent is marked `NEEDS_REVIEW` and every further attempt is refused with
`RAZORPAY_DUPLICATE_ORDERS_FOR_RECEIPT`. Nothing resolves that automatically. That is the correct
trade for money, and it is still an operational burden with no tooling behind it.

**Razorpay provides no order-level idempotency.** The recovery path above exists because a naive
retry could otherwise create a second order for one authorization.

---

## Evidence

**Receipts are traceable, not tamper-evident.**

An evidence receipt is assembled from live database rows at read time. It is neither hashed nor
signed. It shows what the database says *now*, and it cannot prove what the database said an hour
ago. Anyone with write access to the database can change what a receipt reports, and the receipt
will not know.

Calling it tamper-evident would claim a property it does not have. A signed snapshot or a hash chain
would provide one, and that work is deferred (below).

**Audit payloads are not exposed in receipts.** The receipt shows event kinds and correlation
identifiers; the payloads stay in the audit store, because they can carry internal detail.

---

## Operational gaps

These were found by an audit of the whole repository and are documented rather than fixed, because
each belongs to a deployment posture this testbed does not claim to have.

**No rate limiting anywhere.** The approver token and the internal admin token are compared in
constant time, so there is no timing leak — but nothing slows down repeated attempts.

**No request body size limit outside the webhook.** The Razorpay webhook route caps bodies at 64 KB.
Every other POST route relies on Pydantic field limits, which bound the parsed values and not the
bytes read.

**The checkout page is reachable by provider order id, without authentication.** This is how hosted
checkout works — the payer opens a link — and it is scoped correctly: the tenant is derived from the
order row, and everything below it is filtered by that derived tenant. But it does mean anyone
holding an order id can see that purchase's amount, merchant, and purpose.

**No metrics, tracing, or alerting.** Structured logs exist. Nothing watches them.

**No scheduled reconciliation.** Provider state is reconciled when a retry passes through the
recovery path. Nothing sweeps periodically to catch a payment that drifted while nobody was looking.

---

## Scope of the agent

**The buyer agent is deliberately thin, and that is the argument rather than a shortcut.**

It proposes a SKU, a quantity, and a purpose. Every money-critical fact is derived server-side.
Delete the agent entirely and the authorization layer is unchanged and still correct.

**No model is called during testing.** The regression suite uses deterministic substitutes, so
safety verification does not depend on a model behaving a particular way on a particular day. Live
model runs happen only through `agent.demo --live`, against the synthetic seed catalog.

**No refund path exists.** Nothing in this project can initiate a refund — asserted against the
application's live route table, not against memory. The ledger invariant that a refund total cannot
exceed the captured amount is enforced in the state machine and would apply if one were added.

---

## Policy validity

A policy carries an absolute expiry and nothing else. There is no start date, so a policy is usable
the moment it is written and cannot be provisioned ahead of time - a finance lead cannot stage next
quarter's budget in September and have it become live in October. Everything here is written the
day it is needed.

Expiry is deliberately per-policy rather than a fixed lifetime, so nothing in the system decides how
long an authority should last. That is a business decision, and the column is where it goes. What
the system does insist on is that there is one: an authority with no end is a standing permission
that outlives whatever it was granted for.

Old versions are never deleted. An authorization recorded under version 3 has to stay resolvable
long after version 4 supersedes it, and the foreign keys enforce that as much as the intent does.
Retention and usability are separate: a superseded policy stays readable forever and can authorize
nothing.
## Delegated authority

Delegation is enforced, not signed, and that is a trade rather than an oversight.

A hop carries no cryptographic proof of itself. Its authority is re-derived from the whole chain,
against live policy, every time it is spent, which is what lets revoking one hop end every branch
below it without touching a descendant or maintaining a revocation list. The cost is that a hop
means nothing away from the system that issued it: it cannot be verified offline, handed to a third
party, or checked by a merchant. The capability-token designs make the opposite trade, buying
offline verification and giving up recall. Neither is free, and this project needed recall.

What is not attempted:

- **No cross-tenant or cross-system delegation.** A chain lives inside one tenant. Delegating to an
  agent operated by someone else is the problem the IETF attenuating-token draft and the DIF
  delegation-chain work exist for, and nothing here addresses it.
- **No agent identity.** `delegate_actor_id` is a string the caller supplies. Nothing proves the
  actor spending under a hop is the actor the hop was granted to. This is the same gap the identity
  header has everywhere else in this project, and it is the largest one.
- **Depth is bounded at 8 and the bound is arbitrary.** It exists so a chain stays enumerable and
  auditable, not because eight is a meaningful number.
- **A revoked hop is not garbage collected.** Rows stay for the audit trail, so a long-lived tenant
  accumulates dead delegations with no retention policy.
- **Budget is a single currency integer.** A chain cannot span currencies, and nothing converts.
- **A spend's `reference` has no foreign key.** Authorization passes the payment request it is
  authorizing, but `delegation_spend.reference` is still a bare uuid and nothing checks that it
  names a real one. What it no longer has to carry alone is the join: `payment_request.delegation_id`
  is a real composite foreign key, added when checkout began re-asking the chain, so walking from a
  payment to the authority it spent is a contract the database keeps. The reference remains the
  idempotency key it always was.
- **The mutation count went down by one, and the guarantee went up.** The sibling-budget
  aggregate used to be maintained in Python and covered by a mutation. It now lives in the
  `delegation_attenuates` trigger, where a mutation runner cannot reach it, and is covered by tests
  that violate it directly instead. Fewer mutations, a stronger invariant, weaker-shaped evidence -
  worth knowing rather than reading the number as a regression.
- **`purpose` is evidence, not a bound.** It travels with a hop and is frozen after grant, but no
  trigger and no spend check consults it, because free text has no narrowing relation a database
  can enforce. `allowed_skus` is what actually scopes a hop. A child may rewrite its stated purpose
  without changing anything it can spend on.
- **Revoking a hop does not return its unspent budget to its parent.** The allocation stays
  promised, so a tenant that grants and revokes repeatedly loses usable capacity until the hops
  expire and are cleaned up. Reclaiming is deliberately not attempted: a revoked hop kills its
  whole subtree, so a correct reclaim has to walk every descendant, total what they have actually
  spent, and do it without racing a spend already in flight. Conservative and lossy was preferred
  to clever and wrong, and this is the note saying so rather than leaving it to be discovered.
- **Granting is gated by a shared token, not by an identity.** `/api/v1/delegations` requires the
  approver token, the same one the approvals route uses, so an agent cannot mint its own authority
  and the MCP surface still offers no way to try. What the token does not do is say *which* human
  granted a chain: everyone holding it is the same principal, and it neither expires nor rotates.
  The chain records `root_actor_id` from configuration, which is a name, not a proof.
- **A refused consume does not return the budgets the payment is holding.** A chain revoked
  between issuing a checkout authority and consuming it refuses the provider call, which is the
  part that matters - no money moves. The daily reservation and the delegated budget stay held on
  an AUTHORIZED payment that nothing sweeps. Releasing them there would mean writing inside the
  function whose every write is undone by the rollback that carries its refusal out, and that
  rollback is what makes a crash mid-consume fail closed. Issuing is where a dead chain cancels the
  payment and returns both. Trading a guarantee about money for a guarantee about bookkeeping was
  not worth it, and this is the note saying so.
- **The envelope's `provider_action_allowed` is a description, not a decision.** It says whether
  the record currently shows a live, unused checkout authority over an authorized payment whose
  chain, if it has one, is still live. It is read outside a transaction and holds no locks, so an
  envelope that says ALLOWED and a `consume_checkout_authority` that refuses a moment later are
  both correct - the gate is the consume, which re-checks all of it under row locks. Treating the
  envelope as the authority would be exactly the mistake the project is about.
- **An expired delegation is finalized by the next grant, not by anything watching the clock.**
  `uq_delegation_one_live_per_actor` is partial on `revoked_at IS NULL` because an index predicate
  cannot read the time, so an aged-out hop keeps its actor's slot until someone grants again - at
  which point it is revoked and recorded as `delegation_expiry_finalized`. Nothing sweeps expired
  hops on its own, so between expiry and the next grant a hop is dead to every spend and still
  present as the actor's live row. Everything that asks about authority filters expiry in the
  query, so nothing is fooled; a report that counted rows would be.
- **A delegation is found by actor id, and an actor id is a string.** `active_delegation_for`
  matches on `delegate_actor_id`, so the chain that governs a payment is chosen by the same
  unauthenticated identity as everything else here. The enforcement is real; what it is bound to
  is not yet.
- **A request with no catalog SKU is refused outright when the actor holds a delegation.** A
  delegation is scoped by SKU, so there is nothing to check an unscoped purchase against and it
  fails closed. That is the right default and it does mean a delegated actor cannot use the
  non-catalog path at all.
- **`correlation_id` is optional and should not be.** Integration passes the correlation of the
  payment being authorized, which is what joins a delegation's evidence to the payment timeline it
  belongs to. A caller that omits it gets a fresh one and an event that is recorded but not joined.
  Optional so that existing callers did not all have to be rewritten at once; named here so the
  shortcut is not invisible.

## Deliberately deferred

Named here rather than silently dropped.

| Item | Status |
|---|---|
| **Tier B — "Branded Whisper" LLM-reasoning reproduction** | Deferred. The designated cut line, and it was cut |
| **Signed or hash-chained evidence** | Deferred. The upgrade that would make receipts genuinely tamper-evident |
| **Signed mandates** | Deferred. Upgrading the snapshot hash to a signed object |
| **Scheduled reconciliation sweep** | Deferred |
| **Risk-signal seam** | Deferred. A pluggable input where a score could tighten but never loosen authority |
| Multiple policy families per tenant | Out of scope. One ordered policy timeline per tenant |

---

## Data

Every tenant, merchant, catalog item, and price in this project is synthetic. Amounts are integer
INR minor units. No real customer, merchant, or payment data has been through this system, and the
demonstration is written so that none needs to be — including the contact details on the checkout
page, which are prefilled with obviously fake values because the demonstration is recorded.

---

## What is verified, and how

So that the limits above are read against the right baseline:

- **16 Tier A adversarial scenarios**, whose published attack matrix is generated from the
  scenario registry, with a test asserting the two match. The full suite runs on every push.
- **52 mutations** of the safety-critical code, each requiring its guarding tests to fail. A
  passing suite says the code behaves as written; this says the tests would object if it stopped.
- **Concurrency invariants tested concurrently** — races, not sequential approximations of them.
- **CI runs the same Postgres 16 as the compose file**, migrates, and runs the mutation suite on
  every push.

Two of those checks were themselves found to be broken during development and fixed: three route
scans that asserted "no route does X" while examining zero routes, and a local commit gate whose
pipeline made a failing suite look like a passing one. Both are recorded in `docs/decision-log.md`.
