from __future__ import annotations

import hashlib
import hmac
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fixtures import FixtureData
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app import app
from api.database import get_session
from api.routes import razorpay
from models.domain import Payment, RazorpayOrder


@pytest_asyncio.fixture
async def razorpay_client(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("TRUSTGATE_API_ACTOR_ID", "razorpay-api-actor")
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_public")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "test-secret")

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def _issue_authority(client: AsyncClient, data: FixtureData) -> UUID:
    headers = {"X-Tenant-Id": str(data.tenant_a.id)}
    created = await client.post(
        "/api/v1/catalog-payment-requests",
        json={
            "sku": "CLOUD-STARTER",
            "quantity": 1,
            "purpose": "Provision a safe test environment.",
            "idempotency_key": str(uuid4()),
        },
        headers=headers,
    )
    authority = await client.post(
        f"/api/v1/checkout-authorities/{created.json()['payment_request_id']}", headers=headers
    )
    assert created.status_code == 201
    assert authority.status_code == 200
    return UUID(authority.json()["checkout_authority_id"])


@pytest.mark.asyncio
async def test_order_uses_authority_snapshot_and_is_idempotent(
    razorpay_client: AsyncClient,
    async_session: AsyncSession,
    seeded_fixture_data: FixtureData,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, object] = {}

    async def fake_order(**kwargs: object) -> str:
        sent.update(kwargs)
        return "order_test_123"

    monkeypatch.setattr(razorpay, "_create_razorpay_order", fake_order)
    authority_id = await _issue_authority(razorpay_client, seeded_fixture_data)
    headers = {"X-Tenant-Id": str(seeded_fixture_data.tenant_a.id)}
    first = await razorpay_client.post(
        f"/api/v1/razorpay/checkout-authorities/{authority_id}/orders", headers=headers
    )
    second = await razorpay_client.post(
        f"/api/v1/razorpay/checkout-authorities/{authority_id}/orders", headers=headers
    )
    stored = await async_session.scalar(
        select(RazorpayOrder).where(RazorpayOrder.checkout_authority_id == authority_id)
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert sent["amount_minor"] == 39_900
    assert sent["currency"] == "INR"
    assert stored is not None
    assert stored.receipt == f"tg_{authority_id.hex}"


@pytest.mark.asyncio
async def test_callback_uses_server_stored_order_and_does_not_transition_payment(
    razorpay_client: AsyncClient,
    async_session: AsyncSession,
    seeded_fixture_data: FixtureData,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_order(**kwargs: object) -> str:
        return "order_test_callback"

    monkeypatch.setattr(razorpay, "_create_razorpay_order", fake_order)
    authority_id = await _issue_authority(razorpay_client, seeded_fixture_data)
    headers = {"X-Tenant-Id": str(seeded_fixture_data.tenant_a.id)}
    created = await razorpay_client.post(
        f"/api/v1/razorpay/checkout-authorities/{authority_id}/orders", headers=headers
    )
    payment_id = await async_session.scalar(
        select(RazorpayOrder.payment_id).where(
            RazorpayOrder.checkout_authority_id == authority_id
        )
    )
    signature = hmac.new(
        b"test-secret", b"order_test_callback|pay_test_456", hashlib.sha256
    ).hexdigest()
    accepted = await razorpay_client.post(
        "/api/v1/razorpay/callback",
        json={
            "razorpay_order_id": "order_test_callback",
            "razorpay_payment_id": "pay_test_456",
            "razorpay_signature": signature,
        },
    )
    rejected = await razorpay_client.post(
        "/api/v1/razorpay/callback",
        json={
            "razorpay_order_id": "order_test_callback",
            "razorpay_payment_id": "pay_test_456",
            "razorpay_signature": "forged",
        },
    )
    payment = await async_session.scalar(select(Payment).where(Payment.id == payment_id))

    assert created.status_code == 200
    assert accepted.status_code == 202
    assert rejected.status_code == 400
    assert payment is not None
    assert payment.state == "AUTHORIZED"
