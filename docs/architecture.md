# Architecture

## Slice 1 Foundation

The project starts as a local-only FastAPI service with PostgreSQL 16 in Docker Compose.
The initial Slice 1 API exposed only unauthenticated `GET /health`. Slices 2-5 add the domain,
policy, approval, and webhook layers incrementally; MCP and scenario behavior remain deferred.

The intended final shape is:

- Main FastAPI app for policy decisions, approvals, payment status, audit reads, and provider webhook receiving.
- Separate mock provider app that emits signed webhook callbacks into the main app.
- FastMCP stdio server exposing only safe agent-facing tools.
- PostgreSQL persistence with tenant-scoped tables and database-enforced constraints.
- Deterministic scenario suite for protocol/lifecycle attacks and one reasoning-layer reproduction.

## Trust Boundaries

- Tenant identity is testbed-only via `X-Tenant-Id`, then resolved server-side.
- Request bodies are not trusted for tenant identity.
- Webhooks are untrusted until HMAC verification over raw body bytes succeeds.
- MCP tools are untrusted agent-facing inputs and never receive provider secrets.

## Tenant Relationship Integrity

- Tenant-scoped ID lookups remain explicitly filtered by the tenant resolved from the trusted
  request dependency; database constraints complement rather than replace that application rule.
- Composite foreign keys bind each `PaymentRequest` to its tenant's `Merchant`; each `Approval`,
  `AuthorizationDecision`, and `Payment` to its tenant's `PaymentRequest`; and each
  `ProviderEvent` to its tenant's `Payment`.
- Supporting child-side composite indexes make those relationship checks and tenant-scoped
  lookups efficient. `SpendingPolicy` versions are unique within a tenant, giving each tenant a
  single ordered policy timeline.
- If a payment-request caller supplies a merchant that is not available in its trusted tenant,
  the API returns `403 CROSS_TENANT_ACCESS_DENIED`, creates no payment records, and records a
  tenant-scoped rejection audit event. The handler never resolves another tenant's merchant to
  produce this response.

## Slice 5 Provider Webhooks

- The separate mock-provider service creates compact JSON webhook envelopes and signs the exact
  bytes with HMAC-SHA256 before posting to the main API.
- The signed body contains `event_id`, `event_type`, `tenant_id`, `payment_id`, and UTC
  `occurred_at`. The wire `event_id` is persisted as `ProviderEvent.provider_event_id`.
- The receiver verifies bytes before parsing, rejects timestamps outside a five-minute window,
  confirms the signed tenant matches the tenant-scoped payment lookup, deduplicates events, and
  performs state changes only through `transition()`.
- The mock provider intentionally uses one shared local secret. Production would use rotated
  credentials per integration or tenant; that complexity adds no safety signal to this testbed.

## Slice 6 MCP Interface

- The local FastMCP server uses stdio and exposes exactly four tools: request creation, policy
  evaluation, approval request, and payment-status read.
- Each MCP process is bound to one trusted `MCP_TENANT_ID` environment value. Tool arguments do
  not contain tenant identity, provider credentials, signing keys, or webhook signatures.
- The server delegates to the existing policy, approval, and tenant-filtered persistence paths.
  It does not register authorization, capture, or provider-calling tools.
