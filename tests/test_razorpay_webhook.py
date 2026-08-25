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
from uuid import uuid4

import pytest
import pytest_asyncio
from fixtures import FixtureData
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app import app
from api.database import get_session
from models.domain import (
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
        used_at=datetime.now(UTC),
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


def _body(order_id: str, *, event: str = "payment.captured", amount: int = ORDER_AMOUNT) -> bytes:
    return json.dumps(
        {
            "entity": "event",
            "event": event,
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
