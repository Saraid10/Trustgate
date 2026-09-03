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
registered in the Razorpay dashboard. **That run happened on 3 September 2026 and is preserved as
`m3-provider-delivered-webhook.json`** — see below.

The provider order this lifecycle advances (`order_TU34VP9nc4a3k5`) *was* created by the real
Razorpay Test Mode API, so the order half of the flow is provider-originated even though the
webhook half is not.

## `m3-provider-delivered-webhook.json`

**Provenance:** a full Test Mode purchase on 3 September 2026, paid through Razorpay's own checkout
page by netbanking, with `payment.authorized` and `payment.captured` **delivered by Razorpay** to a
Cloudflare tunnel pointed at this application. Nothing in this lifecycle was constructed locally.

**Establishes:** end-to-end provider-originated delivery. Razorpay created the order
(`order_TXeS2utUCdrkse`), a human paid it, and Razorpay reported both lifecycle steps to a URL
registered in its own dashboard. The signature verified over raw bytes, the reported amount matched
the server-derived order, and the state machine carried the payment `AUTHORIZED` →
`PROVIDER_PENDING` → `CAPTURED` for ₹399.00.

**How the two artifacts can be told apart, from the data rather than from this note.** The stored
`provider_event_id` records which. Razorpay sends an `X-Razorpay-Event-Id` header and
`webhook_event_identity` prefers it, so provider-delivered events are keyed
`razorpay:TXeYRRXpsQj929`. Nothing local sends that header, so locally signed events fall back to
`razorpay:<event>:<payment id>` — which is exactly the shape the August artifact carries.

**Does not establish:** anything about Live Mode. This is Test Mode throughout, and
`RAZORPAY_NOT_TEST_MODE` refuses a live key outright.
