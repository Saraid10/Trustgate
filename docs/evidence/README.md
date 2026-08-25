# Preserved evidence

Raw output from real runs, kept unedited. Provenance matters as much as the content, so each
artifact records how it was produced and what it does and does not establish.

## `m1-live-safe.json`, `m1-live-neutral-prompt.json`, `m1-live-adversarial.json`

**Provenance:** genuine calls to a live model provider (Groq, `openai/gpt-oss-120b`) against the
synthetic seeded catalog, 2026-08-24.

**Establishes:** a real model read hostile text in a third-party description field, and the
authorization layer bounded the outcome. Under an ordinary goal the model resisted the injection
entirely; see `docs/m1-verification.md` for why that resistance is reported as the honest headline
rather than the adversarial run.

## `m3-webhook-lifecycle.json`

**Provenance:** webhook payloads constructed locally and signed with the project's real
`RAZORPAY_WEBHOOK_SECRET`, then posted to the running application, 2026-08-25. Both events carry
the same payment identifier, which is how Razorpay reports the authorized and captured steps of one
payment.

**Establishes:** the signature verification, the event-identity derivation, and the state machine
carry a payment through `AUTHORIZED` → `PROVIDER_PENDING` → `CAPTURED`, and the evidence receipt
reflects it.

**Does not establish:** that Razorpay itself delivered these events. The payloads originate here,
not from the provider. Confirming provider-originated delivery needs a publicly reachable URL
registered in the Razorpay dashboard, and that run should be preserved separately when it happens.

The provider order this lifecycle advances (`order_TU34VP9nc4a3k5`) *was* created by the real
Razorpay Test Mode API, so the order half of the flow is provider-originated even though the
webhook half is not.
