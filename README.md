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
extra with `pip install -e ".[agent]"`, set `ANTHROPIC_API_KEY`, and add `--live`. This is the only
path in the project that contacts a model provider; the test suite never does.

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
