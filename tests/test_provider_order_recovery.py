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

import httpx
import pytest
from fixtures import FixtureData
from sqlalchemy import func, select
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


# Captured before any patching. The factory below replaces `httpx.AsyncClient`, so calling it by
# name inside the factory would call the replacement and recurse forever.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _client_factory(transport: httpx.AsyncBaseTransport):
    """Return an AsyncClient factory bound to a mock transport, preserving keyword arguments."""

    def factory(**kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return _REAL_ASYNC_CLIENT(transport=transport, **kwargs)  # type: ignore[arg-type]

    return factory


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
        # Postgres stamps created_at from its own clock, so used_at must come from the same one.
        # A host-clock timestamp here fails `used_at >= created_at` whenever the container clock
        # drifts ahead, which under Docker Desktop it does.
        used_at=func.now(),
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

    # The failure is expected, so it is contained in a savepoint. Letting a constraint violation
    # break the outer transaction leaves the session unusable and emits a SQLAlchemy warning.
    with pytest.raises(Exception) as caught:
        async with async_session.begin_nested():
            intent.provider_state = "CONFIRMED"
            await async_session.flush()

    assert "ck_razorpay_order_state_matches_identifier" in str(caught.value)


async def test_the_receipt_search_paginates_beyond_the_first_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A match outside page one must still be found.

    Treating an older receipt as absent would let a caller create a second provider order for a
    purchase that already has one.
    """

    from api.routes import razorpay as route

    pages = [
        [{"id": f"order_filler{n}", "receipt": "tg_other"} for n in range(100)],
        [{"id": "order_onpagetwo", "receipt": "tg_deep"}],
    ]
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        skip = int(request.url.params.get("skip", 0))
        seen.append(skip)
        index = skip // 100
        return httpx.Response(200, json={"items": pages[index] if index < len(pages) else []})

    monkeypatch.setattr(route.httpx, "AsyncClient", _client_factory(httpx.MockTransport(handler)))

    found = await route._find_orders_by_receipt(
        key_id="rzp_test_public",
        key_secret="secret",  # noqa: S106
        receipt="tg_deep",
    )

    assert found == ["order_onpagetwo"]
    assert seen == [0, 100], "the search stopped before reaching the second page"


async def test_an_incomplete_receipt_search_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never report 'no match' from a search that did not finish."""

    from api.routes import razorpay as route

    def handler(request: httpx.Request) -> httpx.Response:
        # Always a full page, so the search can never conclude.
        return httpx.Response(
            200,
            json={"items": [{"id": f"order_{n}", "receipt": "tg_other"} for n in range(100)]},
        )

    monkeypatch.setattr(route.httpx, "AsyncClient", _client_factory(httpx.MockTransport(handler)))

    with pytest.raises(Exception) as caught:
        await route._find_orders_by_receipt(
            key_id="rzp_test_public",
            key_secret="secret",  # noqa: S106
            receipt="tg_never_found",
        )

    assert "RAZORPAY_RECEIPT_SEARCH_INCOMPLETE" in str(caught.value)
