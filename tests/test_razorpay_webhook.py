"""The Razorpay webhook is the only path that may report a payment outcome.

A browser callback proves that a client returned with matching identifiers, nothing more. This
endpoint is what a captured state is allowed to rest on, so it verifies the signature over the
exact bytes received, checks the reported amount against the amount the server derived, and
refuses to advance a payment on anything it did not verify.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from fixtures import FixtureData
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app import app
from api.database import get_session
from models.domain import (
    AuditEvent,
    CheckoutAuthority,
    Payment,
    PaymentRequest,
    ProviderEvent,
    RazorpayOrder,
)

# Synthetic values. The linter rule that flags these exists to catch real credentials, and
# suppressing it file-wide would weaken that check for the rest of the suite.
WEBHOOK_SECRET = "test-webhook-secret"  # noqa: S105
ORDER_AMOUNT = 39_900


@pytest_asyncio.fixture
async def client(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_public")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "test-secret")

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def order(async_session: AsyncSession, seeded_fixture_data: FixtureData) -> RazorpayOrder:
    """A provider order bound to an authorized payment, as the order route would leave it."""

    request = PaymentRequest(
        id=uuid4(),
        tenant_id=seeded_fixture_data.tenant_a.id,
        actor_id=seeded_fixture_data.tenant_a_actor_one,
        merchant_id=seeded_fixture_data.tenant_a_allowed_merchant.id,
        amount_minor=ORDER_AMOUNT,
        currency="INR",
        order_ref=f"order-{uuid4()}",
        idempotency_key=str(uuid4()),
    )
    async_session.add(request)
    await async_session.flush()
    payment = Payment(
        id=uuid4(),
        tenant_id=seeded_fixture_data.tenant_a.id,
        payment_request_id=request.id,
        state="AUTHORIZED",
        authorized_amount_minor=ORDER_AMOUNT,
        captured_amount_minor=0,
        refunded_amount_minor=0,
    )
    async_session.add(payment)
    await async_session.flush()
    authority = CheckoutAuthority(
        id=uuid4(),
        tenant_id=seeded_fixture_data.tenant_a.id,
        payment_request_id=request.id,
        payment_id=payment.id,
        approval_id=None,
        policy_version=seeded_fixture_data.tenant_a_policy.version,
        snapshot_hash="b" * 64,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
        # Postgres stamps created_at from its own clock, so used_at must come from the same one.
        # A host-clock timestamp here fails `used_at >= created_at` whenever the container clock
        # drifts ahead, which under Docker Desktop it does.
        used_at=func.now(),
    )
    async_session.add(authority)
    await async_session.flush()
    provider_order = RazorpayOrder(
        id=uuid4(),
        tenant_id=seeded_fixture_data.tenant_a.id,
        checkout_authority_id=authority.id,
        payment_id=payment.id,
        razorpay_order_id=f"order_{uuid4().hex[:14]}",
        provider_state="CONFIRMED",
        receipt=f"tg_{authority.id.hex}",
        amount_minor=ORDER_AMOUNT,
        currency="INR",
    )
    async_session.add(provider_order)
    await async_session.flush()
    return provider_order


# A signed event with no timestamp is refused, so the builders stamp "now" by default and keep
# that distinguishable from a test deliberately sending no timestamp at all.
_UNSET_TIMESTAMP: Any = object()


def _body(
    order_id: str,
    *,
    event: str = "payment.captured",
    amount: int = ORDER_AMOUNT,
    created_at: int | None = _UNSET_TIMESTAMP,
) -> bytes:
    return json.dumps(
        {
            "entity": "event",
            "event": event,
            "created_at": int(datetime.now(UTC).timestamp())
            if created_at is _UNSET_TIMESTAMP
            else created_at,
            "contains": ["payment"],
            "payload": {
                "payment": {
                    "entity": {
                        "id": f"pay_{uuid4().hex[:14]}",
                        "order_id": order_id,
                        "amount": amount,
                        "currency": "INR",
                        "status": "captured",
                    }
                }
            },
        },
        separators=(",", ":"),
    ).encode()


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _post(client: AsyncClient, body: bytes, signature: str) -> object:
    return await client.post(
        "/api/v1/razorpay/webhook",
        content=body,
        headers={"X-Razorpay-Signature": signature, "Content-Type": "application/json"},
    )


async def test_signed_events_walk_the_payment_through_its_legal_states(
    client: AsyncClient, async_session: AsyncSession, order: RazorpayOrder
) -> None:
    """Capture is not reachable directly from AUTHORIZED; the provider authorizes first.

    The state machine refuses the shortcut, so a webhook cannot jump a payment straight to
    captured even with a valid signature.
    """

    authorized = _body(order.razorpay_order_id, event="payment.authorized")
    first = await _post(client, authorized, _sign(authorized))
    # Read the value, not the row: the identity map hands back the same object later, so holding
    # the instance would show the final state rather than the intermediate one.
    mid_state = await async_session.scalar(
        select(Payment.state).where(Payment.id == order.payment_id)
    )

    captured = _body(order.razorpay_order_id, event="payment.captured")
    second = await _post(client, captured, _sign(captured))
    final = await async_session.scalar(select(Payment).where(Payment.id == order.payment_id))

    assert first.status_code == 202
    assert mid_state == "PROVIDER_PENDING"
    assert second.status_code == 202
    assert final is not None and final.state == "CAPTURED"
    assert final.captured_amount_minor == ORDER_AMOUNT


async def test_a_capture_event_cannot_skip_the_authorized_state(
    client: AsyncClient, async_session: AsyncSession, order: RazorpayOrder
) -> None:
    body = _body(order.razorpay_order_id, event="payment.captured")

    response = await _post(client, body, _sign(body))

    payment = await async_session.scalar(select(Payment).where(Payment.id == order.payment_id))
    assert response.status_code == 409
    assert payment is not None and payment.state == "AUTHORIZED"
    assert payment.captured_amount_minor == 0


async def test_a_forged_signature_changes_nothing(
    client: AsyncClient, async_session: AsyncSession, order: RazorpayOrder
) -> None:
    body = _body(order.razorpay_order_id)

    forged = _sign(body, secret="attacker-secret")  # noqa: S106
    response = await _post(client, body, forged)

    payment = await async_session.scalar(select(Payment).where(Payment.id == order.payment_id))
    events = list(
        await async_session.scalars(
            select(ProviderEvent).where(ProviderEvent.payment_id == order.payment_id)
        )
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "RAZORPAY_WEBHOOK_SIGNATURE_INVALID"
    assert payment is not None and payment.state == "AUTHORIZED"
    assert events == []


async def test_a_tampered_body_changes_nothing(
    client: AsyncClient, async_session: AsyncSession, order: RazorpayOrder
) -> None:
    """The signature covers the exact bytes, so any mutation invalidates it."""

    body = _body(order.razorpay_order_id)
    signature = _sign(body)
    tampered = body.replace(b'"amount":39900', b'"amount":1')

    response = await _post(client, tampered, signature)

    payment = await async_session.scalar(select(Payment).where(Payment.id == order.payment_id))
    assert response.status_code == 400
    assert payment is not None and payment.state == "AUTHORIZED"


async def test_a_signed_event_reporting_the_wrong_amount_is_refused(
    client: AsyncClient, async_session: AsyncSession, order: RazorpayOrder
) -> None:
    """A valid signature proves origin, not that the amount matches what was authorized."""

    body = _body(order.razorpay_order_id, amount=1)

    response = await _post(client, body, _sign(body))

    payment = await async_session.scalar(select(Payment).where(Payment.id == order.payment_id))
    assert response.status_code == 409
    assert response.json()["detail"] == "RAZORPAY_WEBHOOK_AMOUNT_MISMATCH"
    assert payment is not None and payment.state == "AUTHORIZED"
    assert payment.captured_amount_minor == 0


async def test_a_duplicate_event_does_not_transition_twice(
    client: AsyncClient, async_session: AsyncSession, order: RazorpayOrder
) -> None:
    body = _body(order.razorpay_order_id, event="payment.authorized")
    signature = _sign(body)

    first = await _post(client, body, signature)
    second = await _post(client, body, signature)

    events = list(
        await async_session.scalars(
            select(ProviderEvent).where(ProviderEvent.payment_id == order.payment_id)
        )
    )
    assert first.status_code == 202
    assert second.status_code == 409
    assert len(events) == 1


async def test_an_event_for_an_unknown_order_is_not_found(
    client: AsyncClient, order: RazorpayOrder
) -> None:
    body = _body(f"order_{uuid4().hex[:14]}")

    response = await _post(client, body, _sign(body))

    assert response.status_code == 404
    assert response.json()["detail"] == "RAZORPAY_ORDER_NOT_FOUND"


async def test_an_unhandled_event_type_is_acknowledged_without_changing_state(
    client: AsyncClient, async_session: AsyncSession, order: RazorpayOrder
) -> None:
    """Acknowledge so Razorpay stops retrying, but advance nothing."""

    body = _body(order.razorpay_order_id, event="order.paid")

    response = await _post(client, body, _sign(body))

    payment = await async_session.scalar(select(Payment).where(Payment.id == order.payment_id))
    assert response.status_code == 202
    assert response.json()["detail"] == "RAZORPAY_WEBHOOK_IGNORED"
    assert payment is not None and payment.state == "AUTHORIZED"


async def test_an_oversized_body_is_rejected_before_verification(
    client: AsyncClient, order: RazorpayOrder
) -> None:
    oversized = b"x" * (64 * 1024 + 1)

    response = await _post(client, oversized, _sign(oversized))

    assert response.status_code == 413
    assert response.json()["detail"] == "RAZORPAY_WEBHOOK_BODY_TOO_LARGE"


async def test_a_missing_signature_header_is_refused(
    client: AsyncClient, order: RazorpayOrder
) -> None:
    body = _body(order.razorpay_order_id)

    response = await client.post(
        "/api/v1/razorpay/webhook", content=body, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "RAZORPAY_WEBHOOK_SIGNATURE_INVALID"


def _body_for_payment(
    order_id: str,
    payment_id: str,
    *,
    event: str,
    amount: int = ORDER_AMOUNT,
    created_at: int | None = _UNSET_TIMESTAMP,
) -> bytes:
    """Build an event for a specific payment id.

    Razorpay reports the authorized and captured events for one payment under the same payment
    identifier. The generic helper mints a fresh id per call, which made a sequence look valid
    while hiding whether the two events could actually coexist.
    """

    return json.dumps(
        {
            "entity": "event",
            "event": event,
            "created_at": int(datetime.now(UTC).timestamp())
            if created_at is _UNSET_TIMESTAMP
            else created_at,
            "contains": ["payment"],
            "payload": {
                "payment": {
                    "entity": {
                        "id": payment_id,
                        "order_id": order_id,
                        "amount": amount,
                        "currency": "INR",
                        "status": "captured",
                    }
                }
            },
        },
        separators=(",", ":"),
    ).encode()


async def test_capture_follows_authorization_for_the_same_payment_id(
    client: AsyncClient, async_session: AsyncSession, order: RazorpayOrder
) -> None:
    """The real lifecycle: one payment, two events, one identifier.

    Deduplicating on the payment id alone would reject the capture as a replay of the
    authorization and strand the payment in PROVIDER_PENDING.
    """

    payment_id = f"pay_{uuid4().hex[:14]}"
    authorized = _body_for_payment(order.razorpay_order_id, payment_id, event="payment.authorized")
    captured = _body_for_payment(order.razorpay_order_id, payment_id, event="payment.captured")

    first = await _post(client, authorized, _sign(authorized))
    mid_state = await async_session.scalar(
        select(Payment.state).where(Payment.id == order.payment_id)
    )
    second = await _post(client, captured, _sign(captured))
    final = await async_session.scalar(select(Payment).where(Payment.id == order.payment_id))

    assert first.status_code == 202
    assert mid_state == "PROVIDER_PENDING"
    assert second.status_code == 202, f"capture rejected: {second.json()}"
    assert final is not None and final.state == "CAPTURED"
    assert final.captured_amount_minor == ORDER_AMOUNT


async def test_a_genuine_redelivery_of_one_event_is_still_deduplicated(
    client: AsyncClient, async_session: AsyncSession, order: RazorpayOrder
) -> None:
    """Separating the lifecycle steps must not weaken replay protection.

    The same event redelivered carries the same event type and payment id, so it resolves to the
    same identity and is refused.
    """

    payment_id = f"pay_{uuid4().hex[:14]}"
    body = _body_for_payment(order.razorpay_order_id, payment_id, event="payment.authorized")
    signature = _sign(body)

    first = await _post(client, body, signature)
    second = await _post(client, body, signature)

    events = list(
        await async_session.scalars(
            select(ProviderEvent).where(ProviderEvent.payment_id == order.payment_id)
        )
    )
    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["detail"] == "RAZORPAY_WEBHOOK_DUPLICATE_EVENT"
    assert len(events) == 1


async def test_the_provider_event_header_identity_is_preferred_when_present(
    client: AsyncClient, async_session: AsyncSession, order: RazorpayOrder
) -> None:
    """Razorpay's own event id is stable across retries, which is what dedupe is for."""

    payment_id = f"pay_{uuid4().hex[:14]}"
    body = _body_for_payment(order.razorpay_order_id, payment_id, event="payment.authorized")
    headers = {
        "X-Razorpay-Signature": _sign(body),
        "X-Razorpay-Event-Id": "evt_stable_across_retries",
        "Content-Type": "application/json",
    }

    first = await client.post("/api/v1/razorpay/webhook", content=body, headers=headers)
    second = await client.post("/api/v1/razorpay/webhook", content=body, headers=headers)

    stored = await async_session.scalar(
        select(ProviderEvent.provider_event_id).where(ProviderEvent.payment_id == order.payment_id)
    )
    assert first.status_code == 202
    assert second.status_code == 409
    assert stored == "razorpay:evt_stable_across_retries"


async def test_an_early_capture_is_refused_and_succeeds_once_authorization_arrives(
    client: AsyncClient, async_session: AsyncSession, order: RazorpayOrder
) -> None:
    """Razorpay does not guarantee delivery order, and this system relies on its retries.

    A capture arriving before its authorization is refused with a conflict rather than applied out
    of order. Razorpay redelivers anything it does not consider delivered, so the same event
    succeeds once the predecessor lands. This test pins that recovery path, because relying on
    retry behavior is only sound if the retry actually works.
    """

    payment_id = f"pay_{uuid4().hex[:14]}"
    captured = _body_for_payment(order.razorpay_order_id, payment_id, event="payment.captured")
    authorized = _body_for_payment(order.razorpay_order_id, payment_id, event="payment.authorized")

    early = await _post(client, captured, _sign(captured))
    state_after_early = await async_session.scalar(
        select(Payment.state).where(Payment.id == order.payment_id)
    )

    accepted = await _post(client, authorized, _sign(authorized))
    redelivered = await _post(client, captured, _sign(captured))
    final = await async_session.scalar(select(Payment).where(Payment.id == order.payment_id))

    assert early.status_code == 409, "an out-of-order capture must not be applied"
    assert state_after_early == "AUTHORIZED", "the early capture changed state"
    assert accepted.status_code == 202
    assert redelivered.status_code == 202, f"the retry did not recover: {redelivered.json()}"
    assert final is not None and final.state == "CAPTURED"
    assert final.captured_amount_minor == ORDER_AMOUNT


async def test_one_payment_with_distinct_event_ids_is_processed_twice(
    client: AsyncClient, async_session: AsyncSession, order: RazorpayOrder
) -> None:
    """Distinct provider event ids on one payment are distinct events, not a replay."""

    payment_id = f"pay_{uuid4().hex[:14]}"
    steps = [
        ("payment.authorized", "evt_authorized_001", "PROVIDER_PENDING"),
        ("payment.captured", "evt_captured_002", "CAPTURED"),
    ]

    for event, event_id, expected_state in steps:
        body = _body_for_payment(order.razorpay_order_id, payment_id, event=event)
        response = await client.post(
            "/api/v1/razorpay/webhook",
            content=body,
            headers={
                "X-Razorpay-Signature": _sign(body),
                "X-Razorpay-Event-Id": event_id,
                "Content-Type": "application/json",
            },
        )
        state = await async_session.scalar(
            select(Payment.state).where(Payment.id == order.payment_id)
        )
        assert response.status_code == 202, f"{event} rejected: {response.json()}"
        assert state == expected_state

    stored = list(
        await async_session.scalars(
            select(ProviderEvent.provider_event_id).where(
                ProviderEvent.payment_id == order.payment_id
            )
        )
    )
    assert sorted(stored) == ["razorpay:evt_authorized_001", "razorpay:evt_captured_002"]


async def test_a_failed_attempt_then_capture_on_the_same_payment_succeeds(
    client: AsyncClient, async_session: AsyncSession, order: RazorpayOrder
) -> None:
    """Razorpay documents payment.failed followed by payment.captured.

    Treating the first failure as terminal left the payment with no legal successor, so the real
    capture that followed was refused.
    """

    payment_id = f"pay_{uuid4().hex[:14]}"
    for event in ("payment.authorized", "payment.failed", "payment.captured"):
        b = _body_for_payment(order.razorpay_order_id, payment_id, event=event)
        response = await client.post(
            "/api/v1/razorpay/webhook",
            content=b,
            headers={
                "X-Razorpay-Signature": _sign(b),
                "X-Razorpay-Event-Id": f"evt_{event}",
                "Content-Type": "application/json",
            },
        )
        assert response.status_code == 202, f"{event} rejected: {response.json()}"

    final = await async_session.scalar(select(Payment).where(Payment.id == order.payment_id))
    assert final is not None and final.state == "CAPTURED"
    assert final.captured_amount_minor == ORDER_AMOUNT


async def test_a_retry_under_a_new_payment_id_still_captures(
    client: AsyncClient, async_session: AsyncSession, order: RazorpayOrder
) -> None:
    """A UPI retry produces a different payment identifier for the same order."""

    failed_attempt = f"pay_{uuid4().hex[:14]}"
    retry = f"pay_{uuid4().hex[:14]}"

    steps = [
        (failed_attempt, "payment.authorized"),
        (failed_attempt, "payment.failed"),
        (retry, "payment.captured"),
    ]
    for payment_id, event in steps:
        b = _body_for_payment(order.razorpay_order_id, payment_id, event=event)
        response = await _post(client, b, _sign(b))
        assert response.status_code == 202, f"{event} rejected: {response.json()}"

    final = await async_session.scalar(select(Payment).where(Payment.id == order.payment_id))
    assert final is not None and final.state == "CAPTURED"


async def test_a_failed_attempt_is_recorded_without_moving_the_payment(
    client: AsyncClient, async_session: AsyncSession, order: RazorpayOrder
) -> None:
    """The attempt is evidence, not a verdict."""

    payment_id = f"pay_{uuid4().hex[:14]}"
    authorized = _body_for_payment(order.razorpay_order_id, payment_id, event="payment.authorized")
    await _post(client, authorized, _sign(authorized))

    failed = _body_for_payment(order.razorpay_order_id, payment_id, event="payment.failed")
    response = await _post(client, failed, _sign(failed))

    payment = await async_session.scalar(select(Payment).where(Payment.id == order.payment_id))
    kinds = list(
        await async_session.scalars(
            select(AuditEvent.event_kind).where(
                AuditEvent.event_kind == "razorpay_payment_attempt_failed"
            )
        )
    )
    events = list(
        await async_session.scalars(
            select(ProviderEvent.event_type).where(ProviderEvent.payment_id == order.payment_id)
        )
    )
    assert response.status_code == 202
    assert payment is not None and payment.state == "PROVIDER_PENDING"
    assert kinds, "the failed attempt left no audit record"
    assert "payment.failed" in events, "the failed attempt was not preserved as evidence"


async def test_an_oversized_declared_length_is_refused_before_the_body_is_read(
    client: AsyncClient, order: RazorpayOrder
) -> None:
    """Refuse on the declared size rather than buffering the payload first."""

    body = _body(order.razorpay_order_id)

    response = await client.post(
        "/api/v1/razorpay/webhook",
        content=body,
        headers={
            "X-Razorpay-Signature": _sign(body),
            "Content-Type": "application/json",
            "Content-Length": str(64 * 1024 + 1),
        },
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "RAZORPAY_WEBHOOK_BODY_TOO_LARGE"
