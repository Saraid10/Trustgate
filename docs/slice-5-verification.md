# Slice 5 Verification

## Scope

Implemented a separate mock-provider service, exact-byte HMAC-SHA256 signing, and the main API
webhook receiver at `POST /api/v1/webhooks/provider-events`.

## Security Cross-Check

- The receiver reads `Request.body()` and verifies `X-Provider-Signature` with
  `hmac.compare_digest` before any JSON parsing.
- The signed wire envelope has `event_id`, `event_type`, `tenant_id`, `payment_id`, and UTC
  `occurred_at`; `event_id` is explicitly stored in `ProviderEvent.provider_event_id`.
- A five-minute inclusive freshness boundary is enforced after a successful signature check.
- The payment query contains both signed `payment_id` and signed `tenant_id`; it never fetches
  a payment by ID and checks tenant ownership afterward.
- Valid events use only `transition()`: `AUTHORIZED -> PROVIDER_PENDING`, then provider pending
  to captured or failed, with full mock-provider captures/refunds derived from stored amounts.
- Invalid signature and malformed/tampered pre-attribution bodies produce a structured log with
  a generated correlation ID and raw-body SHA-256 only, never raw attacker-controlled bytes and
  never a fabricated tenant audit record.
- Authenticated stale, duplicate, and tenant-mismatch rejections create real tenant-scoped audit
  records. Accepted events persist their raw payload, signature, and `processed_at`.

## Test Coverage

`tests/test_webhooks.py` has 10 cases, including a 200-example Hypothesis property for
raw-byte signature integrity. It covers valid transition processing, mock-provider delivery,
forged and tampered signatures, stale timestamps, signed tenant mismatch, duplicates,
out-of-order capture, and the full authorized/captured/refunded lifecycle.

## Verification

```text
Host
ruff check .                                                    PASS
mypy api models schemas state_machine policy_engine mock_provider PASS
pytest tests/test_webhooks.py -v                                PASS (10 tests)

Docker
mock provider -> API signed synthetic callback                  PASS (delivery_status 409 tenant mismatch)
docker compose exec -T api python -m pytest tests/test_webhooks.py -q PASS (10 tests)
GET http://localhost:8000/health                                200 {"status":"ok"}
GET http://localhost:8001/openapi.json                          200
```

The Docker pytest invocation emits only a non-functional cache-directory permission warning.
