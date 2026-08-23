from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fixtures import FixtureData
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from api.app import app
from api.database import get_session
from api.routes.checkout_authorities import (
    CheckoutAuthorityUnavailableError,
    consume_checkout_authority,
)
from models.domain import CheckoutAuthority, SpendingPolicy


@pytest_asyncio.fixture
async def authority_client(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("TRUSTGATE_API_ACTOR_ID", "authority-api-actor")
    monkeypatch.setenv("DEMO_APPROVER_TOKEN", "authority-approver-token")
    monkeypatch.setenv("DEMO_APPROVER_ID", "authority-human")

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def _headers(data: FixtureData) -> dict[str, str]:
    return {"X-Tenant-Id": str(data.tenant_a.id)}


async def _catalog_request(
    client: AsyncClient, data: FixtureData, *, sku: str, idempotency_key: str | None = None
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/catalog-payment-requests",
        json={
            "sku": sku,
            "quantity": 1,
            "purpose": "Provision the project build environment.",
            "idempotency_key": idempotency_key or str(uuid4()),
        },
        headers=_headers(data),
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_authority_binds_an_authorized_catalog_snapshot(
    authority_client: AsyncClient,
    async_session: AsyncSession,
    seeded_fixture_data: FixtureData,
) -> None:
    created = await _catalog_request(authority_client, seeded_fixture_data, sku="CLOUD-STARTER")
    response = await authority_client.post(
        f"/api/v1/checkout-authorities/{created['payment_request_id']}",
        headers=_headers(seeded_fixture_data),
    )
    body = response.json()
    stored = await async_session.scalar(
        select(CheckoutAuthority).where(CheckoutAuthority.id == UUID(body["checkout_authority_id"]))
    )

    assert response.status_code == 200
    assert stored is not None
    assert stored.payment_request_id == UUID(created["payment_request_id"])
    assert stored.approval_id is None
    assert len(body["snapshot_hash"]) == 64
    assert datetime.fromisoformat(body["expires_at"]) > datetime.now(UTC)


@pytest.mark.asyncio
async def test_authority_requires_a_consumed_human_approval_for_high_value_purchase(
    authority_client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    created = await _catalog_request(authority_client, seeded_fixture_data, sku="CLOUD-TEAM")
    before_approval = await authority_client.post(
        f"/api/v1/checkout-authorities/{created['payment_request_id']}",
        headers=_headers(seeded_fixture_data),
    )
    grant = await authority_client.post(
        f"/api/v1/approvals/{created['payment_request_id']}/grant",
        headers={**_headers(seeded_fixture_data), "X-Approver-Token": "authority-approver-token"},
    )
    after_approval = await authority_client.post(
        f"/api/v1/checkout-authorities/{created['payment_request_id']}",
        headers=_headers(seeded_fixture_data),
    )

    assert before_approval.status_code == 409
    assert before_approval.json()["detail"] == "CHECKOUT_AUTHORITY_PAYMENT_NOT_AUTHORIZED"
    assert grant.status_code == 200
    assert after_approval.status_code == 200


@pytest.mark.asyncio
async def test_authority_rejects_policy_drift_and_hides_other_tenants_request(
    authority_client: AsyncClient,
    async_session: AsyncSession,
    seeded_fixture_data: FixtureData,
) -> None:
    created = await _catalog_request(authority_client, seeded_fixture_data, sku="CLOUD-STARTER")
    async_session.add(
        SpendingPolicy(
            tenant_id=seeded_fixture_data.tenant_a.id,
            version=2,
            max_amount_minor=100_000,
            currency="INR",
            max_daily_spend_minor=200_000,
            expiry=datetime.now(UTC) + timedelta(days=1),
            approval_required_above_minor=50_000,
        )
    )
    await async_session.flush()
    drift = await authority_client.post(
        f"/api/v1/checkout-authorities/{created['payment_request_id']}",
        headers=_headers(seeded_fixture_data),
    )
    hidden = await authority_client.post(
        f"/api/v1/checkout-authorities/{created['payment_request_id']}",
        headers={"X-Tenant-Id": str(seeded_fixture_data.tenant_b.id)},
    )

    assert drift.status_code == 409
    assert drift.json()["detail"] == "CHECKOUT_AUTHORITY_POLICY_DRIFT"
    assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_authority_cannot_be_reissued_after_use(
    authority_client: AsyncClient,
    async_session: AsyncSession,
    seeded_fixture_data: FixtureData,
) -> None:
    created = await _catalog_request(authority_client, seeded_fixture_data, sku="CLOUD-STARTER")
    first = await authority_client.post(
        f"/api/v1/checkout-authorities/{created['payment_request_id']}",
        headers=_headers(seeded_fixture_data),
    )
    authority = await async_session.scalar(
        select(CheckoutAuthority).where(
            CheckoutAuthority.id == UUID(first.json()["checkout_authority_id"])
        )
    )
    assert authority is not None
    authority.used_at = datetime.now(UTC)
    await async_session.flush()
    replay = await authority_client.post(
        f"/api/v1/checkout-authorities/{created['payment_request_id']}",
        headers=_headers(seeded_fixture_data),
    )

    assert first.status_code == 200
    assert replay.status_code == 409
    assert replay.json()["detail"] == "CHECKOUT_AUTHORITY_UNAVAILABLE"


@pytest.mark.asyncio
async def test_authority_is_atomically_consumed_once(
    authority_client: AsyncClient,
    async_session: AsyncSession,
    seeded_fixture_data: FixtureData,
) -> None:
    created = await _catalog_request(authority_client, seeded_fixture_data, sku="CLOUD-STARTER")
    issued = await authority_client.post(
        f"/api/v1/checkout-authorities/{created['payment_request_id']}",
        headers=_headers(seeded_fixture_data),
    )
    authority_id = UUID(issued.json()["checkout_authority_id"])
    consumed = await consume_checkout_authority(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        checkout_authority_id=authority_id,
        correlation_id=uuid4(),
    )
    with pytest.raises(CheckoutAuthorityUnavailableError, match="CHECKOUT_AUTHORITY_UNAVAILABLE"):
        await consume_checkout_authority(
            async_session,
            tenant_id=seeded_fixture_data.tenant_a.id,
            checkout_authority_id=authority_id,
            correlation_id=uuid4(),
        )

    assert consumed.used_at is not None


@pytest.mark.asyncio
async def test_database_rejects_mutating_an_immutable_policy(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    with pytest.raises(ProgrammingError, match="spending_policy rows are immutable"):
        async with async_session.begin_nested():
            await async_session.execute(
                update(SpendingPolicy)
                .where(SpendingPolicy.id == seeded_fixture_data.tenant_a_policy.id)
                .values(max_amount_minor=1)
            )
