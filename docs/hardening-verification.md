# Tenant Integrity Hardening Verification

Date: 2026-08-19

## Implemented Controls

- `PaymentRequest` references `Merchant` through `(tenant_id, merchant_id)`.
- `Approval`, `AuthorizationDecision`, and `Payment` reference `PaymentRequest` through
  `(tenant_id, payment_request_id)`.
- `ProviderEvent` references `Payment` through `(tenant_id, payment_id)`.
- `SpendingPolicy` enforces `UNIQUE (tenant_id, version)`.
- Composite child-side indexes support each new tenant-scoped relationship.
- An unavailable merchant in the trusted tenant returns `403 CROSS_TENANT_ACCESS_DENIED`, creates
  no payment records, and writes a tenant-scoped rejection audit event.

## Migration Verification

- Alembic revision: `0002_tenant_fk (head)`.
- PostgreSQL inspection confirmed all five composite foreign keys, the policy-version unique
  constraint, and all five child-side indexes.
- The initial revision label exceeded Alembic's 32-character `alembic_version.version_num`
  column. PostgreSQL rolled the attempted DDL back transactionally. The revision was shortened to
  `0002_tenant_fk`, then applied successfully from the unchanged `0001_initial` state.

## Regression Verification

- Direct database tests prove cross-tenant merchant, approval, decision, payment, and provider
  event relationships raise integrity errors.
- API regression proves a tenant cannot create a request against another tenant's merchant and
  that the rejection is audited without creating payment records.
- Full test suite: `68 passed`.
- Ruff: passed.
- Mypy: passed with no issues in 22 source files.
- Docker Compose: PostgreSQL healthy; API `GET /health` returned `200`; mock-provider `/docs`
  returned `200`.

## Known Non-Blocking Warning

The full pytest run reports one warning from an installed `pydantic_settings` dependency about an
unresolved forward reference. It does not originate in project code and did not affect test,
lint, type, migration, or service checks.
