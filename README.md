# TrustGate

TrustGate is a synthetic-data, Razorpay Test Mode demonstration of an AI buyer that can propose a
catalog purchase without gaining authority to rewrite the merchant, amount, currency, approval, or
provider outcome. The agent proposes; TrustGate independently authorizes and records evidence.

## What It Proves

- Catalog SKU and quantity are the only purchase facts an agent can influence.
- Tenant-scoped policy, human approval, a one-time checkout authority, and verified provider events
  bound every payment action.
- Unsafe attempts are rejected before a provider order can be created and leave an auditable trace.

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
