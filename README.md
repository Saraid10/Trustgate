# TrustGate

TrustGate is a synthetic-data, Razorpay Test Mode demonstration of an AI buyer that can propose a
catalog purchase without gaining authority to rewrite the merchant, amount, currency, approval, or
provider outcome. The agent proposes; TrustGate independently authorizes and records evidence.

## What It Proves

- Catalog SKU and quantity are the only purchase facts an agent can influence.
- Tenant-scoped policy, human approval, a one-time checkout authority, and verified provider events
  bound every payment action.
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

The difference is not a filter that recognised an attack. It is that one interface had a field for
the money and the other did not. The baseline has its own tests asserting both that it cannot reach
anything real and that it is still exploitable, since a demonstration that quietly stopped being
vulnerable would keep passing while making the opposite point.

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

Create a disposable synthetic M1 tenant with `python -m agent.seed`. Set `MCP_TENANT_ID` and
`MCP_ACTOR_ID` to the printed values, then run the local buyer-agent demo with
`python -m agent.demo "Buy Starter credits for our student club."`. It can propose only a catalog
SKU, quantity, and purpose; the MCP server derives all money-critical facts. Use
`python -m agent.demo --adversarial "Buy a small amount of cloud credits."` to run the
deterministic poisoned-catalog demonstration.

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
layer's behaviour does not depend on which model proposes the purchase.

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
        -> Razorpay Test Mode executes a bounded order
        -> TrustGate records authorization and provider evidence
```

The formal build plan is in `docs/build-plan.md`; architecture, threat-model, and design decisions
are in `docs/`.
