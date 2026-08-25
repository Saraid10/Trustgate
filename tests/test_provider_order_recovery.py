"""Recovering a provider order call that never completed.

Consuming a checkout authority commits before the provider is contacted. A failure between those
two points once left the authority burned with no order and no record that one had been attempted,
so the purchase could neither proceed nor be retried.

Razorpay offers no idempotency for order creation. Verified against Test Mode on 2026-08-25: two
creates carrying the same receipt produced two distinct orders, and an idempotency-key header did
not deduplicate either. A blind retry would therefore charge twice, which is exactly what the
authority mechanism exists to prevent. Recovery consults the provider first.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fixtures import FixtureData
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.routes.razorpay import _reconcile_intent
from models.domain import (
    AuditEvent,
    CheckoutAuthority,
    Payment,
    PaymentRequest,
    RazorpayOrder,
)

ORDER_AMOUNT = 39_900


async def _pending_intent(
    session: AsyncSession, data: FixtureData, *, receipt: str
) -> RazorpayOrder:
    """An intent recorded before a provider call that never completed."""

    request = PaymentRequest(
        id=uuid4(),
        tenant_id=data.tenant_a.id,
        actor_id=data.tenant_a_actor_one,
        merchant_id=data.tenant_a_allowed_merchant.id,
        amount_minor=ORDER_AMOUNT,
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
        state="AUTHORIZED",
        authorized_amount_minor=ORDER_AMOUNT,
        captured_amount_minor=0,
        refunded_amount_minor=0,
    )
    session.add(payment)
    await session.flush()
    authority = CheckoutAuthority(
        id=uuid4(),
        tenant_id=data.tenant_a.id,
        payment_request_id=request.id,
        payment_id=payment.id,
        approval_id=None,
        policy_version=data.tenant_a_policy.version,
        snapshot_hash="c" * 64,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
        used_at=datetime.now(UTC),
    )
    session.add(authority)
    await session.flush()
    intent = RazorpayOrder(
        id=uuid4(),
        tenant_id=data.tenant_a.id,
        checkout_authority_id=authority.id,
        payment_id=payment.id,
        razorpay_order_id=None,
        provider_state="PENDING",
        receipt=receipt,
        amount_minor=ORDER_AMOUNT,
        currency="INR",
    )
    session.add(intent)
    await session.flush()
    return intent


async def _audit_kinds(session: AsyncSession, correlation_id: object) -> list[str]:
    return list(
        await session.scalars(
            select(AuditEvent.event_kind).where(AuditEvent.correlation_id == correlation_id)
        )
    )


async def test_a_pending_intent_can_be_persisted_without_an_order_id(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The row that makes recovery possible at all."""

    intent = await _pending_intent(async_session, seeded_fixture_data, receipt="tg_pending_ok")

    assert intent.razorpay_order_id is None
    assert intent.provider_state == "PENDING"


async def test_reconciliation_adopts_the_order_the_provider_already_has(
    async_session: AsyncSession, seeded_fixture_data: FixtureData, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The crash happened after the provider created the order. Adopt it; never create a second."""

    intent = await _pending_intent(async_session, seeded_fixture_data, receipt="tg_adopt")
    correlation_id = uuid4()

    async def one_match(**_: object) -> list[str]:
        return ["order_alreadyexists1"]

    monkeypatch.setattr("api.routes.razorpay._find_orders_by_receipt", one_match)

    resolved = await _reconcile_intent(
        async_session,
        intent=intent,
        key_id="rzp_test_public",
        key_secret="secret",  # noqa: S106
        correlation_id=correlation_id,
    )

    assert resolved is not None
    assert resolved.razorpay_order_id == "order_alreadyexists1"
    assert resolved.provider_state == "CONFIRMED"
    assert resolved.reconciled_at is not None
    assert "razorpay_order_reconciled" in await _audit_kinds(async_session, correlation_id)


async def test_reconciliation_reports_no_order_so_creation_may_proceed(
    async_session: AsyncSession, seeded_fixture_data: FixtureData, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The crash happened before the provider created anything. Creating now is safe."""

    intent = await _pending_intent(async_session, seeded_fixture_data, receipt="tg_none")

    async def no_match(**_: object) -> list[str]:
        return []

    monkeypatch.setattr("api.routes.razorpay._find_orders_by_receipt", no_match)

    resolved = await _reconcile_intent(
        async_session,
        intent=intent,
        key_id="rzp_test_public",
        key_secret="secret",  # noqa: S106
        correlation_id=uuid4(),
    )

    assert resolved is None
    assert intent.provider_state == "PENDING"
    assert intent.razorpay_order_id is None


async def test_duplicate_orders_for_one_receipt_are_escalated_not_guessed(
    async_session: AsyncSession, seeded_fixture_data: FixtureData, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two orders share the receipt. Choosing between them is not a silent decision.

    This state is reachable in practice: the provider permits duplicate receipts, so an earlier
    unguarded retry could have produced exactly this.
    """

    intent = await _pending_intent(async_session, seeded_fixture_data, receipt="tg_dupes")
    correlation_id = uuid4()

    async def two_matches(**_: object) -> list[str]:
        return ["order_firstduplicate", "order_secondduplicate"]

    monkeypatch.setattr("api.routes.razorpay._find_orders_by_receipt", two_matches)

    with pytest.raises(Exception) as caught:
        await _reconcile_intent(
            async_session,
            intent=intent,
            key_id="rzp_test_public",
            key_secret="secret",  # noqa: S106
            correlation_id=correlation_id,
        )

    assert "RAZORPAY_DUPLICATE_ORDERS_FOR_RECEIPT" in str(caught.value)
    assert intent.provider_state == "NEEDS_REVIEW"
    assert intent.razorpay_order_id is None
    assert "razorpay_order_needs_review" in await _audit_kinds(async_session, correlation_id)


async def test_a_confirmed_row_must_carry_an_order_id(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The database refuses a confirmed order with nothing to confirm."""

    intent = await _pending_intent(async_session, seeded_fixture_data, receipt="tg_badstate")
    intent.provider_state = "CONFIRMED"

    with pytest.raises(Exception) as caught:
        await async_session.flush()

    assert "ck_razorpay_order_state_matches_identifier" in str(caught.value)
