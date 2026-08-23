from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models.domain import (
    Approval,
    AuditEvent,
    AuthorizationDecision,
    CatalogItem,
    DailySpendReservation,
    Merchant,
    Payment,
    PaymentRequest,
    PolicyMerchant,
    ProviderEvent,
    SpendingPolicy,
    Tenant,
)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://payment_safety:payment_safety@127.0.0.1:5432/payment_safety",
)


@dataclass(frozen=True)
class FixtureData:
    tenant_a: Tenant
    tenant_b: Tenant
    tenant_a_actor_one: str
    tenant_a_actor_two: str
    tenant_b_actor_one: str
    tenant_b_actor_two: str
    tenant_a_allowed_merchant: Merchant
    tenant_a_blocked_merchant: Merchant
    tenant_b_allowed_merchant: Merchant
    tenant_a_catalog_starter: CatalogItem
    tenant_a_catalog_team: CatalogItem
    tenant_b_catalog_private: CatalogItem
    tenant_a_policy: SpendingPolicy
    tenant_b_policy: SpendingPolicy
    payment_request: PaymentRequest
    unconsumed_approval: Approval
    consumed_approval: Approval
    payment: Payment
    webhook_signing_secrets: dict[UUID, str]


@pytest_asyncio.fixture
async def async_session() -> AsyncSession:
    engine = create_async_engine(DATABASE_URL)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session_factory = async_sessionmaker(bind=connection, expire_on_commit=False)
        async with session_factory() as session:
            yield session
        await transaction.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded_fixture_data(async_session: AsyncSession) -> FixtureData:
    """Provide the deterministic tenant, policy, approval, and webhook test data."""

    now = datetime.now(UTC)
    tenant_a = Tenant(id=uuid4(), name=f"tenant-a-{uuid4()}")
    tenant_b = Tenant(id=uuid4(), name=f"tenant-b-{uuid4()}")

    tenant_a_allowed_merchant = Merchant(
        id=uuid4(), tenant_id=tenant_a.id, name="A Allowed One", is_active=True
    )
    tenant_a_allowed_merchant_two = Merchant(
        id=uuid4(), tenant_id=tenant_a.id, name="A Allowed Two", is_active=True
    )
    tenant_a_blocked_merchant = Merchant(
        id=uuid4(), tenant_id=tenant_a.id, name="A Blocked", is_active=True
    )
    tenant_b_allowed_merchant = Merchant(
        id=uuid4(), tenant_id=tenant_b.id, name="B Allowed One", is_active=True
    )
    tenant_b_allowed_merchant_two = Merchant(
        id=uuid4(), tenant_id=tenant_b.id, name="B Allowed Two", is_active=True
    )
    tenant_b_blocked_merchant = Merchant(
        id=uuid4(), tenant_id=tenant_b.id, name="B Blocked", is_active=True
    )

    tenant_a_policy = SpendingPolicy(
        id=uuid4(),
        tenant_id=tenant_a.id,
        version=1,
        max_amount_minor=100_000,
        currency="INR",
        max_daily_spend_minor=200_000,
        expiry=now + timedelta(days=30),
        approval_required_above_minor=50_000,
    )
    tenant_b_policy = SpendingPolicy(
        id=uuid4(),
        tenant_id=tenant_b.id,
        version=1,
        max_amount_minor=100_000,
        currency="INR",
        max_daily_spend_minor=200_000,
        expiry=now + timedelta(days=30),
        approval_required_above_minor=50_000,
    )
    tenant_a_catalog_starter = CatalogItem(
        id=uuid4(),
        tenant_id=tenant_a.id,
        merchant_id=tenant_a_allowed_merchant.id,
        sku="CLOUD-STARTER",
        name="Cloud Starter",
        description_untrusted="Synthetic cloud-credit package for the TrustGate demo.",
        price_minor=39_900,
        currency="INR",
        max_quantity=1,
        active=True,
    )
    tenant_a_catalog_team = CatalogItem(
        id=uuid4(),
        tenant_id=tenant_a.id,
        merchant_id=tenant_a_allowed_merchant.id,
        sku="CLOUD-TEAM",
        name="Cloud Team",
        description_untrusted="Synthetic higher-value cloud-credit package for approval demos.",
        price_minor=60_000,
        currency="INR",
        max_quantity=2,
        active=True,
    )
    tenant_b_catalog_private = CatalogItem(
        id=uuid4(),
        tenant_id=tenant_b.id,
        merchant_id=tenant_b_allowed_merchant.id,
        sku="PRIVATE-COMPUTE",
        name="Private Compute",
        description_untrusted="Synthetic tenant B catalog item.",
        price_minor=99_900,
        currency="INR",
        max_quantity=1,
        active=True,
    )
    payment_request = PaymentRequest(
        id=uuid4(),
        tenant_id=tenant_a.id,
        actor_id="tenant-a-actor-one",
        merchant_id=tenant_a_allowed_merchant.id,
        amount_minor=10_000,
        currency="INR",
        order_ref=f"order-{uuid4()}",
        idempotency_key=str(uuid4()),
    )
    unconsumed_approval = Approval(
        id=uuid4(),
        tenant_id=tenant_a.id,
        payment_request_id=payment_request.id,
        policy_version=tenant_a_policy.version,
        granted_by="human-a",
        expires_at=now + timedelta(days=1),
    )
    consumed_approval = Approval(
        id=uuid4(),
        tenant_id=tenant_a.id,
        payment_request_id=payment_request.id,
        policy_version=tenant_a_policy.version,
        granted_by="human-a",
        expires_at=now + timedelta(days=1),
        consumed_at=now,
    )
    payment = Payment(
        id=uuid4(),
        tenant_id=tenant_a.id,
        payment_request_id=payment_request.id,
        state="CREATED",
        authorized_amount_minor=None,
        captured_amount_minor=0,
        refunded_amount_minor=0,
    )
    decision = AuthorizationDecision(
        id=uuid4(),
        tenant_id=tenant_a.id,
        payment_request_id=payment_request.id,
        decision="ALLOW",
        reasons=[],
        policy_version=tenant_a_policy.version,
        correlation_id=uuid4(),
    )
    daily_reservation = DailySpendReservation(
        tenant_id=tenant_a.id,
        actor_id="tenant-a-actor-one",
        spend_date=now.date(),
        reserved_amount_minor=10_000,
    )
    provider_event = ProviderEvent(
        id=uuid4(),
        tenant_id=tenant_a.id,
        provider_event_id=f"event-{uuid4()}",
        event_type="payment.authorized",
        payment_id=payment.id,
        raw_payload=b'{"kind":"fixture"}',
        signature="fixture-signature",
    )
    audit_event = AuditEvent(
        id=uuid4(),
        tenant_id=tenant_a.id,
        correlation_id=decision.correlation_id,
        event_kind="fixture_created",
        payload={"payment_id": str(payment.id)},
    )

    async_session.add_all([tenant_a, tenant_b])
    await async_session.flush()

    async_session.add_all(
        [
            tenant_a_allowed_merchant,
            tenant_a_allowed_merchant_two,
            tenant_a_blocked_merchant,
            tenant_b_allowed_merchant,
            tenant_b_allowed_merchant_two,
            tenant_b_blocked_merchant,
            tenant_a_policy,
            tenant_b_policy,
        ]
    )
    await async_session.flush()

    async_session.add_all(
        [
            tenant_a_catalog_starter,
            tenant_a_catalog_team,
            tenant_b_catalog_private,
        ]
    )
    await async_session.flush()

    async_session.add_all(
        [
            PolicyMerchant(
                tenant_id=tenant_a.id,
                policy_id=tenant_a_policy.id,
                merchant_id=tenant_a_allowed_merchant.id,
            ),
            PolicyMerchant(
                tenant_id=tenant_a.id,
                policy_id=tenant_a_policy.id,
                merchant_id=tenant_a_allowed_merchant_two.id,
            ),
            PolicyMerchant(
                tenant_id=tenant_b.id,
                policy_id=tenant_b_policy.id,
                merchant_id=tenant_b_allowed_merchant.id,
            ),
            PolicyMerchant(
                tenant_id=tenant_b.id,
                policy_id=tenant_b_policy.id,
                merchant_id=tenant_b_allowed_merchant_two.id,
            ),
            payment_request,
        ]
    )
    await async_session.flush()

    async_session.add_all(
        [
            unconsumed_approval,
            consumed_approval,
            decision,
            daily_reservation,
            payment,
            provider_event,
            audit_event,
        ]
    )
    await async_session.flush()

    return FixtureData(
        tenant_a=tenant_a,
        tenant_b=tenant_b,
        tenant_a_actor_one="tenant-a-actor-one",
        tenant_a_actor_two="tenant-a-actor-two",
        tenant_b_actor_one="tenant-b-actor-one",
        tenant_b_actor_two="tenant-b-actor-two",
        tenant_a_allowed_merchant=tenant_a_allowed_merchant,
        tenant_a_blocked_merchant=tenant_a_blocked_merchant,
        tenant_b_allowed_merchant=tenant_b_allowed_merchant,
        tenant_a_catalog_starter=tenant_a_catalog_starter,
        tenant_a_catalog_team=tenant_a_catalog_team,
        tenant_b_catalog_private=tenant_b_catalog_private,
        tenant_a_policy=tenant_a_policy,
        tenant_b_policy=tenant_b_policy,
        payment_request=payment_request,
        unconsumed_approval=unconsumed_approval,
        consumed_approval=consumed_approval,
        payment=payment,
        webhook_signing_secrets={tenant_a.id: "tenant-a-secret", tenant_b.id: "tenant-b-secret"},
    )
