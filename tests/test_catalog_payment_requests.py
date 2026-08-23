from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from fixtures import FixtureData
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app import app
from api.database import get_session
from models.domain import AuditEvent, PaymentRequest


@pytest_asyncio.fixture
async def catalog_client(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("TRUSTGATE_API_ACTOR_ID", "catalog-api-test-actor")

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def _headers(data: FixtureData) -> dict[str, str]:
    return {"X-Tenant-Id": str(data.tenant_a.id)}


def _payload(data: FixtureData, **overrides: object) -> dict[str, object]:
    return {
        "sku": "CLOUD-STARTER",
        "quantity": 1,
        "purpose": "Provision an isolated build environment.",
        "idempotency_key": str(uuid4()),
        **overrides,
    }


@pytest.mark.asyncio
async def test_catalog_request_derives_all_payment_facts(
    catalog_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    response = await catalog_client.post(
        "/api/v1/catalog-payment-requests",
        json=_payload(seeded_fixture_data),
        headers=_headers(seeded_fixture_data),
    )
    body = response.json()
    stored = await async_session.scalar(
        select(PaymentRequest).where(PaymentRequest.id == body["payment_request_id"])
    )

    assert response.status_code == 201
    assert body["decision"] == "ALLOW"
    assert body["sku"] == "CLOUD-STARTER"
    assert body["merchant_display_name"] == "A Allowed One"
    assert body["amount_minor"] == 39_900
    assert body["currency"] == "INR"
    assert stored is not None
    assert stored.catalog_item_id == seeded_fixture_data.tenant_a_catalog_starter.id
    assert stored.catalog_sku == "CLOUD-STARTER"
    assert stored.catalog_name == "Cloud Starter"
    assert stored.merchant_display_name == "A Allowed One"
    assert stored.merchant_id == seeded_fixture_data.tenant_a_allowed_merchant.id
    assert stored.amount_minor == 39_900
    assert stored.quantity == 1
    assert stored.source == "API"
    assert stored.actor_id == "catalog-api-test-actor"


@pytest.mark.asyncio
async def test_catalog_endpoint_rejects_agent_supplied_payment_facts(
    catalog_client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    response = await catalog_client.post(
        "/api/v1/catalog-payment-requests",
        json=_payload(
            seeded_fixture_data,
            merchant_id=str(seeded_fixture_data.tenant_b_allowed_merchant.id),
            amount_minor=1,
            currency="USD",
        ),
        headers=_headers(seeded_fixture_data),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_catalog_request_cannot_read_another_tenants_sku(
    catalog_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    response = await catalog_client.post(
        "/api/v1/catalog-payment-requests",
        json=_payload(seeded_fixture_data, sku=seeded_fixture_data.tenant_b_catalog_private.sku),
        headers=_headers(seeded_fixture_data),
    )
    audit = await async_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_kind == "catalog_purchase_rejected")
        .order_by(AuditEvent.created_at.desc())
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "CATALOG_ITEM_NOT_AVAILABLE"
    assert audit is not None and audit.payload["reason"] == "CATALOG_ITEM_NOT_AVAILABLE"


@pytest.mark.asyncio
async def test_catalog_request_enforces_the_server_owned_quantity_limit(
    catalog_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    response = await catalog_client.post(
        "/api/v1/catalog-payment-requests",
        json=_payload(seeded_fixture_data, quantity=2),
        headers=_headers(seeded_fixture_data),
    )
    audit = await async_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_kind == "catalog_purchase_rejected")
        .order_by(AuditEvent.created_at.desc())
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "QUANTITY_EXCEEDS_LIMIT"
    assert audit is not None and audit.payload["reason"] == "QUANTITY_EXCEEDS_LIMIT"


@pytest.mark.asyncio
async def test_catalog_idempotency_includes_the_original_purpose(
    catalog_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    idempotency_key = str(uuid4())
    first_payload = _payload(seeded_fixture_data, idempotency_key=idempotency_key)
    first = await catalog_client.post(
        "/api/v1/catalog-payment-requests",
        json=first_payload,
        headers=_headers(seeded_fixture_data),
    )
    replay = await catalog_client.post(
        "/api/v1/catalog-payment-requests",
        json=first_payload,
        headers=_headers(seeded_fixture_data),
    )
    conflict = await catalog_client.post(
        "/api/v1/catalog-payment-requests",
        json={**first_payload, "purpose": "A different business justification."},
        headers=_headers(seeded_fixture_data),
    )
    audit_count = await async_session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.event_kind == "idempotency_key_collision")
    )

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json()["payment_request_id"] == first.json()["payment_request_id"]
    assert conflict.status_code == 409
    assert audit_count == 1
