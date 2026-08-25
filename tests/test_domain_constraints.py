from __future__ import annotations

from uuid import uuid4

import pytest
from fixtures import FixtureData
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.domain import (
    Approval,
    AuditEvent,
    AuthorizationDecision,
    CatalogItem,
    Merchant,
    Payment,
    PaymentRequest,
    PolicyMerchant,
    ProviderEvent,
    SpendingPolicy,
)


@pytest.mark.asyncio
async def test_seeded_fixtures_insert_every_domain_entity(
    seeded_fixture_data: FixtureData,
) -> None:
    assert seeded_fixture_data.tenant_a.id != seeded_fixture_data.tenant_b.id
    assert seeded_fixture_data.payment.tenant_id == seeded_fixture_data.tenant_a.id
    assert seeded_fixture_data.unconsumed_approval.consumed_at is None
    assert seeded_fixture_data.consumed_approval.consumed_at is not None
    assert seeded_fixture_data.webhook_signing_secrets[seeded_fixture_data.tenant_a.id]


@pytest.mark.asyncio
async def test_database_rejects_negative_amount(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    invalid_request = PaymentRequest(
        id=uuid4(),
        tenant_id=seeded_fixture_data.tenant_a.id,
        actor_id=seeded_fixture_data.tenant_a_actor_one,
        merchant_id=seeded_fixture_data.tenant_a_allowed_merchant.id,
        amount_minor=-1,
        currency="INR",
        order_ref="negative-amount",
        idempotency_key=str(uuid4()),
    )

    with pytest.raises(IntegrityError):
        async with async_session.begin_nested():
            async_session.add(invalid_request)
            await async_session.flush()


@pytest.mark.asyncio
async def test_database_rejects_missing_tenant_id(async_session: AsyncSession) -> None:
    missing_tenant = Merchant(id=uuid4(), name="missing-tenant", is_active=True)

    with pytest.raises(IntegrityError):
        async with async_session.begin_nested():
            async_session.add(missing_tenant)
            await async_session.flush()


@pytest.mark.asyncio
async def test_policy_merchant_cannot_cross_tenant_boundary(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    invalid_pairing = PolicyMerchant(
        tenant_id=seeded_fixture_data.tenant_a.id,
        policy_id=seeded_fixture_data.tenant_a_policy.id,
        merchant_id=seeded_fixture_data.tenant_b_allowed_merchant.id,
    )

    with pytest.raises(IntegrityError):
        async with async_session.begin_nested():
            async_session.add(invalid_pairing)
            await async_session.flush()


@pytest.mark.asyncio
async def test_payment_request_cannot_reference_a_cross_tenant_merchant(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    invalid_request = PaymentRequest(
        tenant_id=seeded_fixture_data.tenant_a.id,
        actor_id=seeded_fixture_data.tenant_a_actor_one,
        merchant_id=seeded_fixture_data.tenant_b_allowed_merchant.id,
        amount_minor=1,
        currency="INR",
        order_ref="cross-tenant-merchant",
        idempotency_key=str(uuid4()),
    )

    with pytest.raises(IntegrityError):
        async with async_session.begin_nested():
            async_session.add(invalid_request)
            await async_session.flush()


@pytest.mark.asyncio
async def test_child_records_cannot_reference_cross_tenant_parents(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    tenant_b_request = PaymentRequest(
        tenant_id=seeded_fixture_data.tenant_b.id,
        actor_id=seeded_fixture_data.tenant_b_actor_one,
        merchant_id=seeded_fixture_data.tenant_b_allowed_merchant.id,
        amount_minor=1,
        currency="INR",
        order_ref="tenant-b-parent",
        idempotency_key=str(uuid4()),
    )
    async_session.add(tenant_b_request)
    await async_session.flush()
    tenant_b_payment = Payment(
        tenant_id=seeded_fixture_data.tenant_b.id,
        payment_request_id=tenant_b_request.id,
        state="CREATED",
        authorized_amount_minor=None,
        captured_amount_minor=0,
        refunded_amount_minor=0,
    )
    async_session.add(tenant_b_payment)
    await async_session.flush()

    invalid_children = [
        Approval(
            tenant_id=seeded_fixture_data.tenant_a.id,
            payment_request_id=tenant_b_request.id,
            policy_version=1,
            granted_by="reviewer",
            expires_at=seeded_fixture_data.unconsumed_approval.expires_at,
        ),
        AuthorizationDecision(
            tenant_id=seeded_fixture_data.tenant_a.id,
            payment_request_id=tenant_b_request.id,
            decision="DENY",
            reasons=["test"],
            policy_version=1,
            correlation_id=uuid4(),
        ),
        Payment(
            tenant_id=seeded_fixture_data.tenant_a.id,
            payment_request_id=tenant_b_request.id,
            state="CREATED",
            authorized_amount_minor=None,
            captured_amount_minor=0,
            refunded_amount_minor=0,
        ),
        ProviderEvent(
            tenant_id=seeded_fixture_data.tenant_a.id,
            provider_event_id=str(uuid4()),
            event_type="payment.authorized",
            payment_id=tenant_b_payment.id,
            raw_payload=b"{}",
            signature="test-signature",
        ),
        AuditEvent(
            tenant_id=seeded_fixture_data.tenant_a.id,
            payment_request_id=tenant_b_request.id,
            payment_id=tenant_b_payment.id,
            correlation_id=uuid4(),
            event_kind="cross_tenant_audit_reference",
            payload={},
        ),
    ]
    for invalid_child in invalid_children:
        with pytest.raises(IntegrityError):
            async with async_session.begin_nested():
                async_session.add(invalid_child)
                await async_session.flush()


@pytest.mark.asyncio
async def test_policy_version_is_unique_within_a_tenant(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    duplicate_version = SpendingPolicy(
        tenant_id=seeded_fixture_data.tenant_a.id,
        version=seeded_fixture_data.tenant_a_policy.version,
        max_amount_minor=1,
        currency="INR",
        max_daily_spend_minor=1,
        expiry=seeded_fixture_data.tenant_a_policy.expiry,
        approval_required_above_minor=None,
    )

    with pytest.raises(IntegrityError):
        async with async_session.begin_nested():
            async_session.add(duplicate_version)
            await async_session.flush()


@pytest.mark.asyncio
async def test_catalog_item_cannot_reference_a_cross_tenant_merchant(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    invalid_item = CatalogItem(
        tenant_id=seeded_fixture_data.tenant_a.id,
        merchant_id=seeded_fixture_data.tenant_b_allowed_merchant.id,
        sku="CROSS-TENANT-SKU",
        name="Invalid Catalog Item",
        description_untrusted="Synthetic invalid fixture.",
        price_minor=1,
        currency="INR",
        max_quantity=1,
        active=True,
    )

    with pytest.raises(IntegrityError):
        async with async_session.begin_nested():
            async_session.add(invalid_item)
            await async_session.flush()


@pytest.mark.asyncio
async def test_catalog_sku_is_unique_within_a_tenant(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    duplicate_sku = CatalogItem(
        tenant_id=seeded_fixture_data.tenant_a.id,
        merchant_id=seeded_fixture_data.tenant_a_allowed_merchant.id,
        sku=seeded_fixture_data.tenant_a_catalog_starter.sku,
        name="Duplicate SKU",
        description_untrusted="Synthetic invalid fixture.",
        price_minor=1,
        currency="INR",
        max_quantity=1,
        active=True,
    )

    with pytest.raises(IntegrityError):
        async with async_session.begin_nested():
            async_session.add(duplicate_sku)
            await async_session.flush()
