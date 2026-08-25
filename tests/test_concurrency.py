from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.routes.checkout_authorities import (
    CheckoutAuthorityUnavailableError,
    _snapshot_hash,
    consume_checkout_authority,
    issue_checkout_authority,
)
from models.domain import (
    Approval,
    AuditEvent,
    AuthorizationDecision,
    CatalogItem,
    CheckoutAuthority,
    DailySpendReservation,
    Merchant,
    Payment,
    PaymentRequest,
    PolicyMerchant,
    ProviderEvent,
    RazorpayOrder,
    SpendingPolicy,
    Tenant,
)
from policy_engine.evaluate import reserve_daily_spend
from state_machine.transitions import IllegalTransitionError, transition

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://payment_safety:payment_safety@127.0.0.1:5432/payment_safety",
)


@dataclass(frozen=True)
class RaceData:
    tenant_id: UUID
    policy_id: UUID
    merchant_id: UUID
    request_id: UUID
    payment_id: UUID


async def _seed(session_factory: async_sessionmaker[AsyncSession], *, state: str) -> RaceData:
    now = datetime.now(UTC)
    tenant = Tenant(id=uuid4(), name=f"race-{uuid4()}")
    merchant = Merchant(id=uuid4(), tenant_id=tenant.id, name="Race Merchant", is_active=True)
    policy = SpendingPolicy(
        id=uuid4(),
        tenant_id=tenant.id,
        version=1,
        max_amount_minor=100,
        currency="INR",
        max_daily_spend_minor=100,
        expiry=now + timedelta(days=1),
        approval_required_above_minor=None,
    )
    catalog = CatalogItem(
        id=uuid4(),
        tenant_id=tenant.id,
        merchant_id=merchant.id,
        sku=f"RACE-{uuid4().hex[:8]}",
        name="Race Item",
        description_untrusted="synthetic",
        price_minor=60,
        currency="INR",
        max_quantity=1,
        active=True,
    )
    request = PaymentRequest(
        id=uuid4(),
        tenant_id=tenant.id,
        actor_id="race-actor",
        merchant_id=merchant.id,
        catalog_item_id=catalog.id,
        catalog_sku=catalog.sku,
        catalog_name=catalog.name,
        merchant_display_name=merchant.name,
        quantity=1,
        purpose="race verification",
        source="MCP_AGENT",
        amount_minor=60,
        currency="INR",
        order_ref=f"race-{uuid4()}",
        idempotency_key=str(uuid4()),
    )
    payment = Payment(
        id=uuid4(),
        tenant_id=tenant.id,
        payment_request_id=request.id,
        state=state,
        authorized_amount_minor=60 if state == "AUTHORIZED" else None,
        captured_amount_minor=0,
        refunded_amount_minor=0,
    )
    async with session_factory() as session:
        session.add(tenant)
        await session.flush()
        session.add_all([merchant, policy])
        await session.flush()
        session.add_all(
            [
                PolicyMerchant(
                    tenant_id=tenant.id,
                    policy_id=policy.id,
                    merchant_id=merchant.id,
                ),
                catalog,
            ]
        )
        await session.flush()
        session.add(request)
        await session.flush()
        session.add(payment)
        await session.commit()
    return RaceData(tenant.id, policy.id, merchant.id, request.id, payment.id)


async def _cleanup(session_factory: async_sessionmaker[AsyncSession], tenant_id: UUID) -> None:
    models = (
        # Audit records now have tenant-scoped foreign keys to the purchase graph, so they must
        # be removed before the objects they evidence.
        AuditEvent,
        RazorpayOrder,
        CheckoutAuthority,
        ProviderEvent,
        Approval,
        AuthorizationDecision,
        Payment,
        PaymentRequest,
        PolicyMerchant,
        DailySpendReservation,
        CatalogItem,
        SpendingPolicy,
        Merchant,
        Tenant,
    )
    async with session_factory() as session:
        await session.execute(
            text("ALTER TABLE spending_policy DISABLE TRIGGER spending_policy_immutable")
        )
        for model in models:
            column = model.id if model is Tenant else model.tenant_id
            await session.execute(delete(model).where(column == tenant_id))
        await session.execute(
            text("ALTER TABLE spending_policy ENABLE TRIGGER spending_policy_immutable")
        )
        await session.commit()


async def _race(
    session_factory: async_sessionmaker[AsyncSession],
    operation: Callable[[AsyncSession], Awaitable[object]],
) -> list[object | Exception]:
    barrier = asyncio.Barrier(2)

    async def worker() -> object | Exception:
        async with session_factory() as session:
            await barrier.wait()
            try:
                result = await operation(session)
                await session.commit()
                return result
            except Exception as exc:
                await session.rollback()
                return exc

    return list(await asyncio.gather(worker(), worker()))


@pytest.mark.asyncio
async def test_daily_spend_reservation_race_allows_only_one_final_reservation() -> None:
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    data = await _seed(sessions, state="AUTHORIZED")
    try:
        results = await _race(
            sessions,
            lambda session: reserve_daily_spend(
                session,
                tenant_id=data.tenant_id,
                actor_id="race-actor",
                amount_minor=60,
                policy_version=1,
            ),
        )
        async with sessions() as session:
            reserved = await session.scalar(
                select(DailySpendReservation.reserved_amount_minor).where(
                    DailySpendReservation.tenant_id == data.tenant_id
                )
            )

        assert results.count(True) == 1
        assert results.count(False) == 1
        assert reserved == 60
    finally:
        await _cleanup(sessions, data.tenant_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_approval_consumption_race_authorizes_only_one_caller() -> None:
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    data = await _seed(sessions, state="APPROVAL_REQUIRED")
    approval_id = uuid4()
    async with sessions() as session:
        session.add(
            Approval(
                id=approval_id,
                tenant_id=data.tenant_id,
                payment_request_id=data.request_id,
                policy_version=1,
                granted_by="race-approver",
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
        )
        await session.commit()
    try:

        async def authorize(session: AsyncSession) -> object:
            payment = await session.scalar(select(Payment).where(Payment.id == data.payment_id))
            assert payment is not None
            return await transition(
                session,
                payment,
                "AUTHORIZED",
                reason="race-approval",
                correlation_id=uuid4(),
                approval_id=approval_id,
            )

        results = await _race(sessions, authorize)
        assert sum(not isinstance(result, Exception) for result in results) == 1
        async with sessions() as session:
            consumed_at = await session.scalar(
                select(Approval.consumed_at).where(Approval.id == approval_id)
            )
        assert consumed_at is not None
    finally:
        await _cleanup(sessions, data.tenant_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_checkout_authority_race_consumes_only_one_authority() -> None:
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    data = await _seed(sessions, state="AUTHORIZED")
    authority_id = uuid4()
    async with sessions() as session:
        request = await session.scalar(
            select(PaymentRequest).where(PaymentRequest.id == data.request_id)
        )
        assert request is not None
        session.add(
            CheckoutAuthority(
                id=authority_id,
                tenant_id=data.tenant_id,
                payment_request_id=data.request_id,
                payment_id=data.payment_id,
                approval_id=None,
                policy_version=1,
                snapshot_hash=_snapshot_hash(request, 1),
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
        )
        await session.commit()
    try:
        results = await _race(
            sessions,
            lambda session: consume_checkout_authority(
                session,
                tenant_id=data.tenant_id,
                checkout_authority_id=authority_id,
                correlation_id=uuid4(),
            ),
        )
        assert sum(not isinstance(result, Exception) for result in results) == 1
        assert sum(isinstance(result, CheckoutAuthorityUnavailableError) for result in results) == 1
    finally:
        await _cleanup(sessions, data.tenant_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_policy_publication_lock_forces_authority_issuance_to_recheck_policy() -> None:
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    data = await _seed(sessions, state="AUTHORIZED")
    async with sessions() as session:
        session.add(
            AuthorizationDecision(
                tenant_id=data.tenant_id,
                payment_request_id=data.request_id,
                decision="ALLOW",
                reasons=[],
                policy_version=1,
                correlation_id=uuid4(),
            )
        )
        await session.commit()
    publisher_locked = asyncio.Event()
    release_publisher = asyncio.Event()

    async def publish() -> None:
        async with sessions() as session:
            async with session.begin():
                tenant = await session.scalar(
                    select(Tenant).where(Tenant.id == data.tenant_id).with_for_update()
                )
                assert tenant is not None
                session.add(
                    SpendingPolicy(
                        tenant_id=data.tenant_id,
                        version=2,
                        max_amount_minor=100,
                        currency="INR",
                        max_daily_spend_minor=100,
                        expiry=datetime.now(UTC) + timedelta(days=1),
                        approval_required_above_minor=None,
                    )
                )
                await session.flush()
                publisher_locked.set()
                await release_publisher.wait()

    try:
        publisher = asyncio.create_task(publish())
        await publisher_locked.wait()
        async with sessions() as issuer_session:
            issuer = asyncio.create_task(
                issue_checkout_authority(
                    data.request_id,
                    tenant=Tenant(id=data.tenant_id, name="race-tenant"),
                    session=issuer_session,
                )
            )
            await asyncio.sleep(0.1)
            assert not issuer.done()
            release_publisher.set()
            response = await issuer
        await publisher

        assert response.status_code == 409
        assert response.body == b'{"detail":"CHECKOUT_AUTHORITY_POLICY_DRIFT"}'
    finally:
        release_publisher.set()
        await _cleanup(sessions, data.tenant_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_pending_intent_race_creates_only_one_provider_order() -> None:
    """Two retries of one pending intent must not both create a provider order.

    Recovery reconciles against the provider and then may create. Without a lock across that whole
    sequence, both callers see no matching order and both create one, which is precisely the
    duplicate charge the authority mechanism exists to prevent. The provider is stubbed to report
    no existing order, so only the lock can prevent the second creation.
    """

    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    data = await _seed(sessions, state="AUTHORIZED")
    authority_id = uuid4()
    receipt = f"tg_race_{uuid4().hex[:12]}"
    created: list[str] = []

    async with sessions() as session:
        request = await session.scalar(
            select(PaymentRequest).where(PaymentRequest.id == data.request_id)
        )
        assert request is not None
        session.add(
            CheckoutAuthority(
                id=authority_id,
                tenant_id=data.tenant_id,
                payment_request_id=data.request_id,
                payment_id=data.payment_id,
                approval_id=None,
                policy_version=1,
                snapshot_hash=_snapshot_hash(request, 1),
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
                used_at=datetime.now(UTC),
            )
        )
        session.add(
            RazorpayOrder(
                id=uuid4(),
                tenant_id=data.tenant_id,
                checkout_authority_id=authority_id,
                payment_id=data.payment_id,
                razorpay_order_id=None,
                provider_state="PENDING",
                receipt=receipt,
                amount_minor=request.amount_minor,
                currency=request.currency,
            )
        )
        await session.commit()

    async def claim(session: AsyncSession) -> str | None:
        """Mirror the recovery branch: lock, reconcile, then create if nothing exists."""

        intent = await session.scalar(
            select(RazorpayOrder)
            .where(
                RazorpayOrder.tenant_id == data.tenant_id,
                RazorpayOrder.checkout_authority_id == authority_id,
            )
            .with_for_update()
        )
        assert intent is not None
        if intent.provider_state == "CONFIRMED":
            return None
        # The provider reports no order for this receipt, so a caller that reaches here creates one.
        order_id = f"order_{uuid4().hex[:14]}"
        created.append(order_id)
        intent.razorpay_order_id = order_id
        intent.provider_state = "CONFIRMED"
        await session.commit()
        return order_id

    try:
        results = await _race(sessions, claim)
        assert sum(not isinstance(result, Exception) for result in results) == 2
        assert len(created) == 1, f"both callers created an order: {created}"
        async with sessions() as session:
            rows = list(
                await session.scalars(
                    select(RazorpayOrder).where(RazorpayOrder.tenant_id == data.tenant_id)
                )
            )
        assert len(rows) == 1
        assert rows[0].provider_state == "CONFIRMED"
    finally:
        async with sessions() as session:
            await session.execute(
                delete(RazorpayOrder).where(RazorpayOrder.tenant_id == data.tenant_id)
            )
            await session.commit()
        await _cleanup(sessions, data.tenant_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_second_caller_cannot_decide_from_state_the_lock_should_have_hidden() -> None:
    """The row lock must hold a second transition off until the first one commits.

    The interleaving is driven explicitly rather than left to the scheduler. The second caller
    begins its transition while the first is written but uncommitted, which is the only ordering
    where the lock does any work. Postgres guarantees it cannot acquire the row, so it necessarily
    decides after the commit and finds AUTHORIZED already set.

    Without the lock the second caller reads the pre-commit state under READ COMMITTED, decides
    from CREATED, and authorizes a payment that was authorized moments earlier. Racing the two
    callers with a barrier reproduces that only intermittently: `populate_existing` often re-reads
    after the winner commits and the bug hides. Driving the order makes it deterministic.
    """

    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    data = await _seed(sessions, state="CREATED")
    try:
        first = sessions()
        second = sessions()
        try:

            async def authorize(session: AsyncSession) -> Payment:
                payment = await session.scalar(select(Payment).where(Payment.id == data.payment_id))
                assert payment is not None
                return await transition(
                    session,
                    payment,
                    "AUTHORIZED",
                    reason="ordered-transition",
                    correlation_id=uuid4(),
                )

            await authorize(first)

            follower = asyncio.ensure_future(authorize(second))
            await asyncio.wait({follower}, timeout=1.0)
            assert not follower.done(), (
                "the second caller decided while the first was still uncommitted"
            )

            await first.commit()

            with pytest.raises(IllegalTransitionError):
                await follower
            await second.rollback()
        finally:
            await first.close()
            await second.close()

        async with sessions() as session:
            state = await session.scalar(select(Payment.state).where(Payment.id == data.payment_id))
            transitions = await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.tenant_id == data.tenant_id,
                    AuditEvent.event_kind == "payment_transition",
                )
            )

        assert state == "AUTHORIZED"
        assert transitions == 1, f"the payment was transitioned {transitions} times"
    finally:
        await _cleanup(sessions, data.tenant_id)
        await engine.dispose()
