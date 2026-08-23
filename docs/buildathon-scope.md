# Razorpay TrustGate Buildathon Scope

## Positioning

TrustGate is a synthetic-data, Razorpay Test Mode demonstration of an AI commerce agent whose
payment actions remain bounded by independently enforced backend authority.

The AI may propose a catalog purchase. It cannot set tenant identity, merchant identity, final
amount, currency, policy result, approval state, provider state, or provider credentials.

## Demonstrated Claim

TrustGate evaluates a published set of adversarial scenarios against an AI-driven catalog
purchase workflow. In those scenarios, tenant-scoped policy, human approval, checkout authority,
and verified provider events block actions outside the approved authority before a Razorpay Test
Mode order is created.

## In Scope

- Synthetic tenants, demo identities, merchants, catalog items, and INR prices.
- A local AI commerce agent using a narrow MCP tool surface.
- Server-derived SKU price, currency, and merchant binding.
- Approval and checkout authority bound to an immutable purchase snapshot.
- Razorpay Test Mode Orders, Standard Checkout, server-side payment-signature verification, and
  signed Razorpay webhook handling.
- Existing mock provider for deterministic adverse-event and offline-demo coverage.
- Automated attack evidence and a guided audit console.

## Explicit Non-Goals

- Live money, Live Mode API keys, real customer data, card data, bank data, or UPI data.
- PCI DSS, RBI, NPCI, SOC 2, or production-readiness claims.
- Remote MCP OAuth, KYC, fraud scoring, autonomous web browsing, general prompt-injection
  prevention, or unrestricted agent purchasing.
- Multiple independently versioned policy families. TrustGate v1 retains one ordered policy
  version timeline per tenant.

## Non-Negotiable Controls

1. The agent never receives provider secrets or a tenant-selection parameter.
2. Catalog lookup derives the final merchant, integer amount, and currency server-side.
3. A checkout authority binds tenant, request, policy version, purchase snapshot, approval where
   required, expiry, and one-time use.
4. A provider order can be created only for a valid authority in `AUTHORIZED` state.
5. Browser completion is not payment proof; server-side verification and verified provider events
   are authoritative.
6. Mock-provider tests remain the deterministic safety harness even after Razorpay is added.

## Delivery Gates

1. Catalog path: the agent cannot supply an authoritative amount or merchant.
2. Authority path: self-approval, replay, expiry, snapshot change, and policy drift are blocked.
3. Provider path: exactly one Test Mode order per authority; no unsafe request creates an order.
4. Evidence path: every public attack result links to an automated test and audit trace.
5. Demo path: safe order, approval-required order, and blocked attack work from a clean setup.
