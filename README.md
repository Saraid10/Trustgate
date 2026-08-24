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
| A11b | Cross-tenant object access | Every tenant-scoped lookup filters by the trusted tenant. A known tenant cannot read or act on another tenant's request, payment, or authority on any surface. | `test_a11b_checkout_authority_route_refuses_another_tenants_request`<br>`test_a11b_razorpay_route_refuses_another_tenants_authority`<br>`test_a11b_mcp_refuses_another_tenants_payment` |
| A15 | Unauthorized capture via MCP | No tool reachable by the agent can authorize, capture, refund, or call a provider. Proven by exercising every exposed tool, not by inspecting tool names. | `test_a15_every_exposed_mcp_tool_grants_no_payment_authority`<br>`test_a15_mcp_exposes_no_provider_or_authorization_tool` |
<!-- attack-matrix:end -->

The remaining Tier A scenarios (A3-A14) are not yet implemented.

## Current Scope

TrustGate uses only synthetic tenants, merchants, and INR prices. It is a local safety testbed, not
a payment processor, compliance product, legal-consent system, fraud model, or Live Mode payment
integration.

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
