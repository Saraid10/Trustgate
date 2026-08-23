# M2 Verification - Early Attack Proof

**Date:** 2026-08-24

## Invariants Proven

1. A refused attack creates no provider order.
2. A refused attack advances no payment into a state only policy, human approval, a consumed
   checkout authority, or a verified provider event can grant.
3. The amount is derived from the catalog price and a server-bounded quantity; quantity is the
   only amount lever an agent holds and it cannot exceed the item's maximum.
4. A merchant outside the tenant is unreachable, and a merchant inside the tenant that the active
   policy does not allow cannot be paid.
5. A known tenant cannot read or act on another tenant's request, payment, or authority on the
   checkout-authority route, the Razorpay order route, or the MCP surface.
6. No tool the MCP server exposes grants payment authority, proven by exercising every tool.
7. The published attack matrix cannot claim an attack that has no passing test.

## Why This Slice Exists

Three of the four attacks already had partial coverage. Existing tests asserted the endpoint
response and, where relevant, the audit event. None asserted that the refused request changed
nothing, which is the claim the project actually makes. A response assertion cannot distinguish
"the request was refused" from "the request was refused and something was written anyway".

## Implemented Surface

- `scenarios/tier_a/harness.py` captures a tenant-scoped snapshot of payment states, payment
  requests, provider orders, and consumed authorities, and compares it across an attack.
- The harness raises `ScenarioViolation` rather than using bare `assert`. It is shipped library
  code and `python -O` strips assertions; an assert-based harness reports every scenario as
  passing under optimisation while verifying nothing. The same reasoning applied to the state
  machine's approval requirement in Slice 4.
- `scenarios/tier_a/__init__.py` is the registry. `scenarios/report.py` renders the matrix.
- `tests/test_scenario_harness.py` feeds the harness snapshots of a successful attack and requires
  it to object, so the scenarios cannot pass vacuously.
- The five existing rejection tests gained the same assertions. Existing assertions were kept
  rather than replaced, so the harness is additive and coverage did not shrink.

## Defects Found While Building

- The first draft of the Razorpay cross-tenant scenario used a route path that does not exist.
  It returned 404 and passed a loose status assertion while proving nothing. Corrected to
  `/api/v1/razorpay/checkout-authorities/{id}/orders` with an exact 409 and reason-code assertion.
- That corrected scenario then still used a random authority identifier, so it proved only that
  unknown identifiers are refused rather than that one tenant cannot consume another's authority.
  It now seeds a genuinely valid, unconsumed tenant A authority, snapshots both tenants, asserts
  the owner's authority remains unconsumed, and asserts the owner is refused for a *different*
  reason — which is what makes the tenant filter the only possible cause of tenant B's refusal.
- The raw amount-field scenario asserted only the 422 status. It now pins which fields were
  rejected, so an unrelated validation failure introduced later cannot satisfy it.
- The MCP cross-tenant scenario initially skipped when fixtures provided no tenant B payment. A
  skipped adversarial test would let the published matrix claim coverage that never ran, so the
  scenario now seeds its own tenant B payment.
- The `scenarios` package was not in mypy's checked packages and was therefore unchecked when
  first written.

## Commands and Results

| Gate | Result |
|---|---|
| Full suite | 123 passed |
| `ruff check .` | Passed |
| `ruff format --check .` | Passed, 81 files |
| `mypy --strict` | Passed, 36 source files |
| Harness under `python -O` | 8 passed; assertions survive optimisation |
| README matrix drift test | Passed |

No migration was added in this slice, so the migration round-trip gate is unchanged from M0.

## Scope

A1, A2, A11b, and A15 only. A3-A14 remain unimplemented and the README states this explicitly.
Several of the remaining scenarios are more useful once the evidence receipt exists, so they are
scheduled for M5.
