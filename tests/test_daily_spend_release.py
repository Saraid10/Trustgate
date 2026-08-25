"""Reserved budget must be returned when a request can no longer spend it.

Reserving on `REQUIRE_APPROVAL` stops an approved high-value request from bypassing the daily
limit. Without a matching release, an agent acting entirely within its permitted contract can
exhaust an actor's day by requesting approvals nobody grants: a denial of service that needs no
forged amount and no escaped tenant scope.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fixtures import FixtureData
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.domain import DailySpendReservation, Payment, PaymentRequest
from policy_engine.evaluate import release_daily_spend, reserve_daily_spend
from state_machine.transitions import transition


async def _reserved(session: AsyncSession, data: FixtureData, actor: str) -> int:
    value = await session.scalar(
        select(DailySpendReservation.reserved_amount_minor).where(
            DailySpendReservation.tenant_id == data.tenant_a.id,
            DailySpendReservation.actor_id == actor,
            DailySpendReservation.spend_date == datetime.now(UTC).date(),
        )
    )
    return int(value or 0)


async def _request_with_payment(
    session: AsyncSession, data: FixtureData, *, actor: str, amount: int, state: str
) -> Payment:
    request = PaymentRequest(
        id=uuid4(),
        tenant_id=data.tenant_a.id,
        actor_id=actor,
        merchant_id=data.tenant_a_allowed_merchant.id,
        amount_minor=amount,
        currency="INR",
        order_ref=f"order-{uuid4()}",
        idempotency_key=str(uuid4()),
    )
    session.add(request)
    await session.flush()
    payment = Payment(
        id=uuid4(),
        tenant_id=data.tenant_a.id,
        payment_request_id=request.id,
        state=state,
        captured_amount_minor=0,
        refunded_amount_minor=0,
    )
    session.add(payment)
    await session.flush()
    return payment


async def test_a_denied_payment_returns_its_reservation(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    actor = f"release-denied-{uuid4()}"
    payment = await _request_with_payment(
        async_session, seeded_fixture_data, actor=actor, amount=60_000, state="APPROVAL_REQUIRED"
    )
    await reserve_daily_spend(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        actor_id=actor,
        amount_minor=60_000,
        policy_version=seeded_fixture_data.tenant_a_policy.version,
    )
    assert await _reserved(async_session, seeded_fixture_data, actor) == 60_000

    await transition(
        async_session,
        payment,
        "DENIED",
        reason="approval abandoned",
        correlation_id=uuid4(),
    )

    assert await _reserved(async_session, seeded_fixture_data, actor) == 0


async def test_an_expired_payment_returns_its_reservation(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    actor = f"release-expired-{uuid4()}"
    payment = await _request_with_payment(
        async_session, seeded_fixture_data, actor=actor, amount=40_000, state="APPROVAL_REQUIRED"
    )
    await reserve_daily_spend(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        actor_id=actor,
        amount_minor=40_000,
        policy_version=seeded_fixture_data.tenant_a_policy.version,
    )

    await transition(
        async_session,
        payment,
        "EXPIRED",
        reason="authority window elapsed",
        correlation_id=uuid4(),
    )

    assert await _reserved(async_session, seeded_fixture_data, actor) == 0


async def test_abandoned_approvals_no_longer_exhaust_the_day(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The attack: request approvals nobody grants until the actor's budget is gone."""

    actor = f"release-exhaustion-{uuid4()}"
    for _ in range(3):
        payment = await _request_with_payment(
            async_session,
            seeded_fixture_data,
            actor=actor,
            amount=60_000,
            state="APPROVAL_REQUIRED",
        )
        await reserve_daily_spend(
            async_session,
            tenant_id=seeded_fixture_data.tenant_a.id,
            actor_id=actor,
            amount_minor=60_000,
            policy_version=seeded_fixture_data.tenant_a_policy.version,
        )
        await transition(
            async_session,
            payment,
            "DENIED",
            reason="approval abandoned",
            correlation_id=uuid4(),
        )

    assert await _reserved(async_session, seeded_fixture_data, actor) == 0


async def test_a_captured_payment_keeps_its_reservation(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Captured money was spent; returning its budget would let the limit be spent twice."""

    actor = f"release-captured-{uuid4()}"
    payment = await _request_with_payment(
        async_session, seeded_fixture_data, actor=actor, amount=30_000, state="PROVIDER_PENDING"
    )
    payment.authorized_amount_minor = 30_000
    payment.captured_amount_minor = 30_000
    await async_session.flush()
    await reserve_daily_spend(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        actor_id=actor,
        amount_minor=30_000,
        policy_version=seeded_fixture_data.tenant_a_policy.version,
    )

    await transition(
        async_session,
        payment,
        "CAPTURED",
        reason="provider captured",
        correlation_id=uuid4(),
    )

    assert await _reserved(async_session, seeded_fixture_data, actor) == 30_000


async def test_a_double_release_cannot_create_budget(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Releasing more than was reserved must floor at zero, never go negative."""

    actor = f"release-floor-{uuid4()}"
    await reserve_daily_spend(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        actor_id=actor,
        amount_minor=10_000,
        policy_version=seeded_fixture_data.tenant_a_policy.version,
    )
    today = datetime.now(UTC).date()

    for _ in range(3):
        await release_daily_spend(
            async_session,
            tenant_id=seeded_fixture_data.tenant_a.id,
            actor_id=actor,
            amount_minor=10_000,
            spend_date=today,
        )

    assert await _reserved(async_session, seeded_fixture_data, actor) == 0


async def test_a_request_denied_without_reserving_cannot_refund_budget(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """A denial that never reserved must not hand budget back.

    Budget is reserved only for ALLOW and REQUIRE_APPROVAL, which leave the payment in AUTHORIZED
    or APPROVAL_REQUIRED. A request denied outright stays in CREATED. Releasing on that transition
    would let an agent manufacture budget by making requests it knew would be refused.
    """

    actor = f"release-unreserved-{uuid4()}"
    await reserve_daily_spend(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        actor_id=actor,
        amount_minor=50_000,
        policy_version=seeded_fixture_data.tenant_a_policy.version,
    )
    unreserved = await _request_with_payment(
        async_session, seeded_fixture_data, actor=actor, amount=10_000, state="CREATED"
    )

    await transition(
        async_session,
        unreserved,
        "DENIED",
        reason="daily limit exceeded",
        correlation_id=uuid4(),
    )

    assert await _reserved(async_session, seeded_fixture_data, actor) == 50_000
