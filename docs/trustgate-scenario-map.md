# TrustGate Scenario Mapping

This mapping preserves the canonical payment-safety testbed scenarios while extending them for
the Razorpay Test Mode catalog flow. TrustGate scenarios supplement rather than replace the
existing registry in `docs/execution-spec.md`.

| TrustGate scenario | Existing safety coverage | New TrustGate proof |
|---|---|---|
| Amount escalation | A1 | Catalog-derived amount and immutable purchase snapshot block altered price. |
| SKU swap | A2 | Checkout authority hash binds the approved SKU and catalog item. |
| Quantity escalation | A1 | Server derives amount from SKU price times bounded quantity. |
| Merchant substitution | A2, A11b | Tenant-scoped catalog lookup and composite FKs reject the request. |
| Tenant injection | A11a, A11b | Tenant remains server-derived for MCP and demo identities. |
| Approval replay | A5 | Approval and checkout authority are both single-use. |
| Stale authority | A4 | Expired authority cannot create a provider order. |
| Policy drift | A13 | Changed active policy version revokes pending authority and requires reevaluation. |
| Poisoned catalog content | New deterministic extension | Unsafe SKU or quantity attempt is blocked without relying on LLM behavior. |
| Forged Razorpay webhook | A6 | Raw-byte HMAC verification rejects before any state change. |
| Duplicate Razorpay webhook | A8 | Provider event ID dedupe prevents a second transition. |
| Wrong order association | A11b | Verified provider order ID must map to the tenant-scoped local order record. |
| Out-of-order provider event | A9 | State machine permits only compatible provider outcomes. |
| Direct provider bypass | A15 | No MCP or public route can create a provider order without authority. |
| Paise/subunit error | A1, A3 | Integer minor-unit catalog prices and adapter contract test prevent incorrect amount. |

## Evidence Required Per Scenario

- deterministic setup and attempted action;
- expected response and invariant;
- assertion that no unsafe provider order exists;
- assertion that no illegal payment state transition exists;
- tenant-scoped audit evidence, or structured unattributed logging before webhook verification;
- automated test name and pass result for the attack console.
