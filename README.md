# TrustGate

TrustGate is a synthetic-data, Razorpay Test Mode testbed for bounded agent spending. An AI buyer
proposes a catalog purchase and never gains authority to rewrite the merchant, amount, currency,
approval, or provider outcome. The agent proposes; TrustGate independently authorizes and records
evidence.

The part worth looking at is not that it refuses things. It is that the refusals are **verified
rather than asserted**. Every invariant in the mutation registry below is deleted on purpose, one
at a time, and a test has to fail. That is how an unguarded policy-expiry check was found here
after two clean audits: 302 tests passed with the check removed.

The registry is targeted, not exhaustive, and the distinction is worth keeping. It covers guards
that live in Python. Guards that live in the database - triggers, check constraints, partial unique
indexes - are proven instead by tests that violate them directly, because a mutation runner edits
source files and a trigger already applied to a schema would not notice.

## What It Proves

- Catalog SKU and quantity are the only purchase facts an agent can influence.
- Tenant-scoped policy, human approval, a one-time checkout authority, and verified provider events
  bound every payment action.
- Authority delegated onward narrows at every hop, and the hops below a node cannot, between them,
  promise more than that node holds.
- An actor holding a delegation has its purchases checked against the whole chain during
  authorization, and the budget returns if the payment never happens.
- Revoking one hop ends every branch below it without touching a descendant or recalling anything,
  including for a payment already authorized: the chain is re-asked before checkout authority is
  issued and again before it is consumed, and a payment stopped that way gives both budgets back.
- Unsafe attempts are rejected before a provider order can be created and leave an auditable trace.

## How AI Is Used Here

The buyer agent is deliberately thin, and that is the argument rather than a shortcut. It can
propose only a catalog SKU, a quantity, and a purpose; every money-critical fact is derived
server-side. Delete the agent entirely and the authorization layer is unchanged and still correct.

The engineering contribution is not a sophisticated agent. It is taking language-model failure
modes seriously — prompt injection through third-party content, instruction-following against the
operator's interest, confident wrong reasoning about money — and building a system that stays
correct when they occur.

That claim is tested both ways. `python -m agent.demo --live` runs a real model against catalog
descriptions written by third parties, including hostile ones. The same catalog is sent twice, once
with descriptions removed, so influence from untrusted content is measured by comparing the two
proposals rather than self-reported. The regression suite never calls a model provider: it uses
deterministic substitutes, so safety verification does not depend on a model behaving a particular
way on a particular day.

## Seeing the Problem

Three clean refusals prove the system works and are weak at showing why anyone needs it. The demo
therefore opens with this project's own code failing, using no network, no credentials, and no
database:

```bash
python -m demo.unguarded
```

The same agent reads the same poisoned catalog - literally the same, since both catalogs are built
from `agent/demo_catalog.py` and a test asserts the seeded row and the baseline object carry an
identical injected instruction. One model response is handed to two adapters.

The unguarded one accepts an amount and a merchant, so the injected instruction executes and it
pays INR 20,000.00 to a merchant the catalog text named, against a catalog price of INR 600.00.
The other has nowhere to put either field, because `PurchaseProposal` declares only a SKU, a
quantity, and a purpose. What survives the discard is `quantity=50`, which is a field the agent is
allowed to set - and the server bounds it against the catalog's own maximum of 2, so the attempt is
refused before a payment request is created.

The difference is not a filter that recognized an attack. It is that one interface had a field for
the money and the other did not. The baseline has its own tests asserting both that it cannot reach
anything real and that it is still exploitable, since a demonstration that quietly stopped being
vulnerable would keep passing while making the opposite point.

## Run the Demo

Four commands take a clean database to a real Razorpay Test Mode payment. Requires the stack from
[Quickstart](#quickstart) and `ENABLE_CONSOLE=true` in `.env`.

```bash
python -m agent.stage
```

Stages a fixed demo tenant, clears the timeline, and prints the console URL plus the exact command
for every beat below. Run it between takes.

```bash
python -m demo.unguarded
```

**The problem**, in this project's own code. No policy layer, so the injected instruction executes.

```bash
python -m agent.demo "Buy Starter credits for the robotics club."
python -m agent.checkout --open
```

**A purchase that should go through.** The agent proposes a SKU and a quantity; the server derives
the price and merchant. `checkout` issues a one-time authority, creates a real provider order, and
opens the payment page — the agent does neither, which is the point.

```bash
python -m agent.demo "Buy Team credits for the robotics club."
python -m agent.approve
```

**A purchase that needs a human.** Over the approval threshold, so the agent cannot finish it.
`approve` is a separate command holding a token the agent does not have, under an identity the
server refuses if it matches the requester.

```bash
python -m agent.demo --adversarial "Buy cloud credits for the club."
```

**The attack.** The same injected instruction from the first command. The amount and merchant are
discarded because the proposal has nowhere to put them; what survives is a quantity the catalog
bounds. No payment request is created.

The read-only console at `/console/{tenant_id}` leads with the current decision — **AUTHORIZED**,
**APPROVAL REQUIRED**, or **BLOCKED** — with the reason in plain words, whether the provider may be
called at all (`Order creation allowed: Yes/No`), and the human whose delegated authority is behind
it. Below that, every attempt in three columns: what the agent proposed, what the server derived,
and what the provider actually did.

The panel is assembled from the same evidence record the receipt renders, and from the first row of
the table beneath it, so it cannot disagree with either. Reason codes are translated for display
only — the table still shows the exact stored code. The console cannot authorize, approve, or
create anything.

```bash
python -m agent.delegate
```

**Delegation.** Four hops, narrowing at each. A leaf spends inside every bound above it, is refused
a SKU outside the scope it was narrowed to, and a sibling is refused budget its parent had already
promised away. Then one hop is revoked and the branch below it dies untouched.

[`demo/script.md`](demo/script.md) is the rehearsed path with timings and what to say at each beat.

## Delegated Authority, and Why a Budget Is Not a Capability

Attenuating a capability is set intersection. A child permitted no more than its parent cannot
widen the chain however deep it runs, because sets intersect - which is what the delegation
literature, the macaroon family, and the IETF attenuating-token draft all mean by attenuation.

Money is not a set. Two children each granted exactly their parent's budget satisfy every per-edge
comparison and hold twice the parent's budget between them. Budgets add where sets intersect.

TrustGate therefore **partitions** a parent's budget rather than comparing against it. The
`delegation_attenuates` trigger takes the allocation from the parent as part of writing the child,
so the aggregate holds for anything that inserts a row rather than for callers who remember the
bookkeeping. `python -m agent.delegate` walks four hops and shows the sibling being refused.

An earlier version claimed this "survives every line of the application being wrong" while the
allocation was still maintained in Python. It did not: three children of 1,000 were written under a
parent holding 1,000 by an insert that skipped the module, and every per-edge check passed them.
The claim is true now because the trigger does the work, and
`test_siblings_written_straight_to_the_database_cannot_outgrow_their_parent` is what says so.

The mutation named `delegation-aggregate-partition` is the evidence that this is a real distinction
and not a stylistic one: delete the aggregate claim and every other delegation test still passes.

**This is wired into authorization, and the boundary that remains is narrower than it was.** A
purchase by an actor holding a delegation is checked against every hop above it before the payment
request is recorded, the delegation is debited in the same savepoint as the daily reservation, and
the budget comes back on the same condition that already returns the daily one - DENIED, EXPIRED,
FAILED, CANCELLED. An actor holding no delegation takes the path it took before any of this
existed, which is what makes the rest of the suite the regression net for the wiring.

Two budgets refusing separately is where this could have leaked, and the savepoint is why it does
not: claiming the daily reservation and then refusing on the delegation would leave it moved for a
payment that never happens, and the release path only fires on a transition out of a holding state,
which a request refused at authorization never enters. Doing the delegation first only mirrors the
leak. Either both hold or neither does.

What is still missing is a way to *create* one over HTTP. A delegation is granted and revoked from
Python today - `python -m agent.delegate` and the staging command - and there is deliberately no
tool letting an agent mint its own authority. `docs/limitations.md` has the rest, including the
largest: nothing proves the actor spending is the actor the delegation was granted to.

A second property falls out of the same design. A hop carries no signature; its authority is
re-derived from its whole chain, against live policy, every time it is spent. Revoking any link is
already the end of the branch - no recall, no revocation list, and the descendant is never written
to. The capability-token designs make the opposite trade, buying offline verification and giving up
recall. `docs/limitations.md` states what that costs here.

## Attack Matrix

Tier A adversarial scenarios. This table is generated from the scenario registry by
`python -m scenarios.report`, and a test asserts it matches, so it cannot claim an attack
that is not covered by a passing test. Every scenario proves three things: the attack is
rejected with its reason code, no provider order was created, and no payment gained
authority it did not have.

<!-- attack-matrix:start -->
| ID | Attack | Invariant proven | Tests |
|---|---|---|---|
| A1 | Amount tampering | The amount is derived from the catalog item's price and a server-bounded quantity. No agent-supplied value can change it. | `test_a1_supplied_amount_field_is_refused_at_the_boundary`<br>`test_a1_mcp_surface_has_no_amount_parameter`<br>`test_a1_quantity_cannot_be_used_to_escalate_the_amount` |
| A2 | Merchant substitution | The merchant is derived from the tenant-scoped catalog item. A merchant outside the tenant is unreachable, and one outside the active policy cannot be paid. | `test_a2_another_tenants_sku_is_not_reachable`<br>`test_a2_policy_disallowed_merchant_cannot_be_paid` |
| A3 | Currency substitution | Currency is derived from the catalog item, and the one route that accepts a currency is disabled by default and denies a mismatch against the active policy when enabled. | `test_a3_the_agent_surface_derives_currency_and_cannot_be_told_one`<br>`test_a3_the_only_currency_accepting_route_is_disabled_by_default`<br>`test_a3_an_enabled_legacy_route_still_denies_a_currency_outside_the_policy` |
| A4 | Expired or reused approval | An approval is a permission with a lifetime and a single use. Neither an expired one nor an already consumed one can authorize, and a refused approval is not burned. | `test_a4_an_expired_approval_cannot_authorize`<br>`test_a4_an_already_consumed_approval_cannot_authorize_again` |
| A5 | Self-approval | An approval cannot be granted by the identity that requested the purchase. Separation of duties is enforced, not merely expected from configuration. | `test_a5_an_approval_cannot_be_granted_by_the_requesting_actor`<br>`test_a5_a_separate_approver_can_still_grant` |
| A6 | Forged webhook signature | Provider events are authenticated by raw-byte HMAC before the body is parsed. A forged or absent signature changes nothing, however well-formed the event is. | `test_a6_a_forged_signature_is_refused`<br>`test_a6_an_unsigned_event_is_refused` |
| A7 | Tampered webhook body | The signature covers the exact bytes received, so a genuinely signed event edited in flight no longer verifies and never reaches a payment. | `test_a7_a_body_altered_after_signing_no_longer_verifies` |
| A8 | Duplicate webhook delivery | Provider event identity is stored, so a replay of an authentic, in-window event is refused by the database rather than by whichever handler happens to look. | `test_a8_a_replayed_event_does_not_transition_the_payment_twice` |
| A9 | Out-of-order provider events | Arrival order is the provider's and legality is ours. A capture cannot precede its authorization, and a terminal payment accepts no further outcome. | `test_a9_a_capture_cannot_precede_an_authorization`<br>`test_a9_a_terminal_payment_accepts_no_further_provider_outcome` |
| A10 | Double refund | No surface can initiate a refund at all, asserted against the live route table and tool list, and the ledger invariant refuses a refund total exceeding the capture. | `test_a10_no_surface_anywhere_can_initiate_a_refund`<br>`test_a10_a_refund_total_cannot_exceed_what_was_captured` |
| A11a | Unknown tenant header | A tenant that does not resolve is refused before any route body runs, and the refusal discloses nothing that would let a caller enumerate which tenants exist. | `test_a11a_an_unknown_tenant_header_is_refused`<br>`test_a11a_an_unknown_tenant_is_indistinguishable_from_a_forbidden_one` |
| A11b | Cross-tenant object access | Every tenant-scoped lookup filters by the trusted tenant. A known tenant cannot read or act on another tenant's request, payment, or authority on any surface. | `test_a11b_checkout_authority_route_refuses_another_tenants_request`<br>`test_a11b_razorpay_route_refuses_another_tenants_authority`<br>`test_a11b_mcp_refuses_another_tenants_payment` |
| A12 | Idempotency key collision | A key reused with a different purchase returns the original decision and a 409. The second purchase is never created and cannot be mistaken for one that was accepted. | `test_a12_a_reused_key_with_a_different_purchase_returns_the_first_decision` |
| A13 | Policy drift between authorization and use | An authority does not outlive the policy it was checked against, nor the purchase it was issued for. A superseding policy, an expired one, or an edited amount revokes it without burning it, and an undrifted authority still works. | `test_a13_an_authority_is_valid_until_the_policy_under_it_moves`<br>`test_a13_a_policy_published_after_authorization_revokes_the_authority`<br>`test_a13_an_amount_edited_after_authorization_breaks_the_snapshot_hash`<br>`test_a13_an_expired_policy_cannot_spend_an_authority` |
| A14 | Stale or post-dated webhook | A signature proves origin, not recency. An event outside the freshness window, dated into the future, or carrying no timestamp at all is refused before any lookup. | `test_a14_a_stale_signed_event_is_refused`<br>`test_a14_a_post_dated_event_cannot_extend_its_own_validity`<br>`test_a14_an_event_with_no_timestamp_is_refused_rather_than_exempted` |
| A15 | Unauthorized capture via MCP | No tool reachable by the agent can authorize, capture, refund, or call a provider. Proven by exercising every exposed tool, not by inspecting tool names. | `test_a15_every_exposed_mcp_tool_grants_no_payment_authority`<br>`test_a15_mcp_exposes_no_provider_or_authorization_tool` |
<!-- attack-matrix:end -->

Every Tier A scenario is implemented. `A11a` and `A11b` split tenant confusion into an
unresolvable tenant and a known tenant reaching across the boundary, because the two fail for
different reasons and only the second is an authorization question.

## What the Tests Are Worth

A passing suite says the code behaves as written. It does not say the tests would object if the
code stopped doing something important, and only the second claim matters when the subject is
money. This project has evidence for the difference: request-scoped sessions once discarded every
write while 146 tests passed, because the suite asserted inside the same transaction it wrote in.

`make mutation` breaks each safety invariant on purpose, one at a time, and requires the tests named
as its guards to fail. Every source file is restored in a `finally` block and the restoration is
checked against `git diff` before the report prints, so an interrupted run cannot leave a mutation
behind. It exits non-zero if any mutation survives.

This table is generated from the mutation registry by `python -m scenarios.report --mutations`, and
a test asserts it matches, so it cannot claim a guarded invariant that is not actually guarded.

<!-- mutation-table:start -->
| Mutation | Invariant it removes |
|---|---|
| `payment-row-lock` | A payment is locked before its state is read and changed. |
| `locked-read-freshness` | A locked read decides from the committed row, not from a cached one. |
| `locking-discipline` | Row locks are taken through the one helper that keeps them meaningful. |
| `webhook-signature-check` | A provider event is authenticated before anything is done with it. |
| `webhook-freshness-window` | A signed provider event proves origin, not recency. |
| `webhook-timestamp-required` | An event that cannot be dated cannot be bounded, so it is refused. |
| `approval-expiry` | An approval is a permission with a lifetime, not a permanent grant. |
| `authority-policy-drift` | An authority does not outlive the policy it was checked against. |
| `authority-policy-expiry` | An authority cannot be spent under a policy that has run out. |
| `authority-snapshot-binding` | An authority is bound to the exact purchase it was issued for. |
| `daily-budget-predicate` | The daily budget upsert refuses to exceed the limit. |
| `budget-release-from-state-guard` | Budget is returned only by a payment that actually reserved it. |
| `checkout-script-escaping` | Catalog text cannot terminate the checkout page's script element. |
| `request-session-commit` | A successful request commits its writes. |
| `provider-event-identity` | Lifecycle events for one payment are distinct events, not replays. |
| `self-approval-guard` | An approval cannot be granted by the requesting actor. |
| `evidence-tenant-filter` | Evidence is scoped to the tenant that asked for it. |
| `receipt-search-fail-closed` | An incomplete provider search never reports a receipt as absent. |
| `policy-expiry-denies-spending` | An expired policy cannot authorize new spending. |
| `missing-policy-fails-closed` | A tenant with no policy is denied rather than allowed by default. |
| `delegation-spend-against-allocation` | A hop cannot spend budget it has already promised to the hops below it. |
| `delegation-chain-revocation` | Revoking one hop stops every hop below it. |
| `delegation-chain-payment-cap` | A spend is bound by the narrowest per-payment cap anywhere above it. |
| `delegation-scope-narrowing` | A purpose narrowed at one hop stays narrowed at every hop below it. |
| `delegation-hop-expiry` | An expired hop stops the branch below it. |
| `delegation-positive-amount` | A spend moves budget one way; a negative amount cannot refund it. |
| `delegation-chain-locked-before-trusted` | A spend holds every hop above it, so a revoke cannot land mid-decision. |
| `delegation-spend-idempotent` | Spending twice under one reference charges the chain once. |
| `delegation-release-only-once` | A spend given back once cannot be given back again. |
| `delegation-refused-spend-releases-its-reference` | A refused spend does not burn the reference it was refused under. |
| `delegation-spend-is-evidenced` | A spend that moves budget records that it did. |
| `delegation-evidence-names-the-whole-chain` | A spend's evidence names every hop that authorized it, not just the leaf. |
| `delegation-reference-belongs-to-one-request` | A reused reference carrying different details is refused, not reported as done. |
| `authorization-claims-both-budgets-or-neither` | A payment refused after one budget moved gives it back before it is recorded. |
| `delegated-budget-returns-when-a-payment-dies` | A payment that never happens returns its delegated budget, on every path. |
| `delegation-consulted-during-authorization` | A payment by an actor holding a delegation is checked against it. |
| `delegation-chain-reads-fresh` | A chain read after a grant reports the allocation the grant just took. |
| `checkout-re-asks-the-chain-before-issuing` | A delegation revoked after authorization stops the checkout it authorized. |
| `checkout-re-asks-the-chain-before-consuming` | A chain revoked while its authority was in hand refuses the provider call. |
| `blocked-checkout-returns-both-budgets` | A checkout blocked by a dead chain strands neither budget on the payment. |
| `request-records-the-chain-it-spent` | A payment request names the delegation it debited, durably enough to re-ask. |
| `expired-hop-stops-holding-its-actors-slot` | An actor whose delegation expired can be granted another one. |
| `granting-cannot-silently-end-live-authority` | Making room for a grant never revokes a delegation that still works. |
| `revoke-does-not-describe-another-tenants-row` | A revoke that matches nothing says which nothing, without leaking the other. |
| `the-spend-joins-its-purchase` | A delegation spend is reachable from the payment request it paid for. |
| `the-release-joins-its-purchase` | A returned delegation budget is reachable from the purchase that returned it. |
| `evidence-names-the-delegated-authority` | A purchase made under a delegation says so in its evidence record. |
| `envelope-will-not-say-a-payment-may-be-made-without-authority` | An authorized purchase with no checkout authority is not allowed to pay. |
| `envelope-notices-the-chain-died-under-the-authority` | A revoked chain takes the provider action away from an issued authority. |
| `the-timeline-names-the-authority-a-purchase-ran-under` | A delegated purchase says whose authority it spent, on the timeline. |
| `the-banner-says-nothing-was-written-down` | An attack refused before a payment request exists says so, rather than blankly. |
| `reason-codes-reach-a-reader-as-sentences` | A refusal is shown in words, not as the code it is stored under. |
| `a-settled-payment-is-not-called-unauthorized` | A captured payment blocks provider action as settled, not as never authorized. |
<!-- mutation-table:end -->

The first run of this suite found a live defect. `SELECT ... FOR UPDATE` through the ORM acquires
the lock correctly and then discards the row Postgres returned, because SQLAlchemy keeps the
attributes of an object already in the session's identity map. A second caller therefore blocked on
the lock as designed, received the committed row, kept its stale copy, and authorized a payment
that had just been authorized. The lock was serialising when transitions ran, not what state they
decided from. All locking now goes through `models.locking.locked()`, and a test asserts that
helper is the only place in the source that can take a lock.

## Current Scope

TrustGate uses only synthetic tenants, merchants, and INR prices. It is a local safety testbed, not
a payment processor, compliance product, legal-consent system, fraud model, or Live Mode payment
integration.

[`docs/positioning.md`](docs/positioning.md) explains why a project like this is worth building
now, with each item of Indian payments context labelled by the evidence behind it — an NPCI
circular is cited as a circular, a press report as a press report, and a recommendation as a
recommendation. It claims no compliance with any of them.

[`docs/limitations.md`](docs/limitations.md) names every deliberate cut, including the unflattering
ones: tenant identity is a header and not authentication, one webhook secret serves all tenants,
receipts are traceable but not tamper-evident, and there is no rate limiting anywhere. A limitation
you have to discover is worse than one that is written down.

## Quickstart

Requirements: Docker Desktop and Python 3.12.

```powershell
Copy-Item .env.example .env
docker compose up -d
docker compose exec -T api python -m alembic upgrade head
docker compose exec -T api python -m pytest -q
```

The local API health check is available at `http://127.0.0.1:8000/health`.

For the demonstration, use `python -m agent.stage` — it stages a fixed tenant so the console URL
stays the same between runs, and prints every command. See [Run the Demo](#run-the-demo).

`python -m agent.seed` remains for exploration: it mints a disposable tenant with fresh
identifiers, which is right for poking at the system and wrong for anything you intend to film.

To run the same flow with a real model instead of a deterministic substitute, install the optional
extra with `pip install -e ".[agent]"` and add `--live`. Two backends are supported and both use
the same Messages API shape:

- `TRUSTGATE_MODEL_BACKEND=anthropic` (default) reads `ANTHROPIC_API_KEY`.
- `TRUSTGATE_MODEL_BACKEND=bedrock` bills against an AWS account. Set `AWS_REGION` to the region
  the credential belongs to, then either `AWS_BEARER_TOKEN_BEDROCK` (a Bedrock API key, the
  simplest path) or standard AWS credentials for SigV4 signing. Amazon Bedrock provisions
  Anthropic models through an AWS Marketplace subscription, so the AWS account also needs a valid
  payment instrument even when credits would cover the usage.
- `TRUSTGATE_MODEL_BACKEND=groq` reads `GROQ_API_KEY` and needs no payment instrument at all.

`TRUSTGATE_MODEL_ID` overrides the model on any backend. The buyer is a protocol implementation,
so the provider is a configuration choice rather than an architectural one: the authorization
layer's behavior does not depend on which model proposes the purchase.

This is the only path in the project that contacts a model provider; the test suite never does.
Run it only against the synthetic seed catalog. Its third-party descriptions are sent to the model
twice, once with descriptions removed and once intact, to measure influence. Never send real
customer, merchant, or payment data through this demonstration.

To exercise the Razorpay Test Mode adapter, set `RAZORPAY_KEY_ID` and
`RAZORPAY_KEY_SECRET` in the ignored `.env` file. Never add Test Mode or Live Mode secrets to the
repository.

## Trust Boundary

```text
AI buyer proposes SKU, quantity, and purpose
        -> TrustGate derives and authorizes money-critical facts
        -> a human takes the authorization to checkout
        -> Razorpay Test Mode executes a bounded order
        -> a signed provider event moves the payment to captured
        -> TrustGate records authorization and provider evidence
```

The agent crosses the first arrow and no other. Authorization and payment are separate steps: it
obtains the right to buy something and never obtains the ability to pay.

| Document | What it holds |
|---|---|
| [`docs/decision-log.md`](docs/decision-log.md) | Every real choice, with the alternatives rejected and why |
| [`docs/limitations.md`](docs/limitations.md) | Every deliberate cut, including the unflattering ones |
| [`docs/positioning.md`](docs/positioning.md) | Indian payments context, labelled by the evidence behind it |
| [`docs/architecture.md`](docs/architecture.md) | How the pieces fit |
| [`docs/threat-model.md`](docs/threat-model.md) | What is defended and against whom |
| [`docs/build-plan.md`](docs/build-plan.md) | The formal plan this was built against |
