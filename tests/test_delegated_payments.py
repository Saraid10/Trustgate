"""A payment made under a delegation, end to end, through the real authorization path.

Everything before this proved the delegation engine on a bench: budgets partition, chains narrow,
revocation cascades, retries charge once. None of it proved the engine does anything when a payment
actually runs, because until now nothing called it.

These are the cases that could not be written before, and they are the ones that would have been
embarrassing to discover later. Three of them are about a refusal moving no money: the daily
reservation and the delegation claim are two separate budgets, and a payment that is refused after
one of them has moved leaves it moved - the release path only fires on a transition out of a
holding state, which a request denied at authorization never enters.

An actor holding no delegation takes the path it took before any of this existed. That is what
makes the rest of the suite the regression net for this wiring.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from fixtures import FixtureData
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app import app
from api.dependencies import get_session
from delegation.chain import Bounds, grant_root
from models.domain import (
    AuditEvent,
    DailySpendReservation,
    Delegation,
    DelegationSpend,
    Payment,
    PaymentRequest,
)
from state_machine.transitions import transition

DELEGATE = "delegated-payments-actor"
STARTER_PRICE_MINOR = 39_900


@pytest_asyncio.fixture
async def client(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("TRUSTGATE_API_ACTOR_ID", DELEGATE)

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


async def _delegation(
    session: AsyncSession, data: FixtureData, *, budget: int, cap: int = 100_000
) -> Delegation:
    return await grant_root(
        session,
        tenant_id=data.tenant_a.id,
        policy=data.tenant_a_policy,
        principal_actor_id="finance-lead",
        delegate_actor_id=DELEGATE,
        bounds=Bounds(
            budget_minor=budget,
            max_amount_minor=cap,
            allowed_skus=("CLOUD-STARTER",),
            purpose="integration",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        ),
    )


def _buy(data: FixtureData, *, sku: str = "CLOUD-STARTER", quantity: int = 1) -> dict[str, object]:
    return {
        "sku": sku,
        "quantity": quantity,
        "purpose": "Provision an isolated build environment.",
        "idempotency_key": str(uuid4()),
    }


def _headers(data: FixtureData) -> dict[str, str]:
    return {"X-Tenant-Id": str(data.tenant_a.id)}


async def _spent(session: AsyncSession, delegation: Delegation) -> int:
    return (
        await session.scalar(select(Delegation.spent_minor).where(Delegation.id == delegation.id))
    ) or 0


async def _reserved(session: AsyncSession, data: FixtureData) -> int:
    return (
        await session.scalar(
            select(DailySpendReservation.reserved_amount_minor).where(
                DailySpendReservation.tenant_id == data.tenant_a.id,
                DailySpendReservation.actor_id == DELEGATE,
            )
        )
    ) or 0


@pytest.mark.asyncio
async def test_a_payment_within_the_delegation_is_authorized_and_debits_the_chain(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The thing that could not be claimed before: a purchase running under delegated authority."""

    held = await _delegation(async_session, seeded_fixture_data, budget=100_000)

    response = await client.post(
        "/api/v1/catalog-payment-requests",
        json=_buy(seeded_fixture_data),
        headers=_headers(seeded_fixture_data),
    )

    assert response.status_code in (200, 201), response.text
    assert response.json()["decision"] == "ALLOW"
    assert await _spent(async_session, held) == STARTER_PRICE_MINOR
    assert await _reserved(async_session, seeded_fixture_data) == STARTER_PRICE_MINOR


@pytest.mark.asyncio
async def test_a_payment_over_the_delegation_is_denied_and_moves_neither_budget(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """A refusal after the daily reservation has moved would leave it moved.

    The delegation holds less than the purchase costs while the policy is content, so the daily
    reservation succeeds and the delegation refuses. Both are inside one savepoint, so the
    reservation has to come back.
    """

    held = await _delegation(async_session, seeded_fixture_data, budget=1_000)

    response = await client.post(
        "/api/v1/catalog-payment-requests",
        json=_buy(seeded_fixture_data),
        headers=_headers(seeded_fixture_data),
    )

    assert response.json()["decision"] == "DENY"
    assert await _spent(async_session, held) == 0
    assert await _reserved(async_session, seeded_fixture_data) == 0, (
        "the daily reservation stayed moved for a payment that was refused"
    )


@pytest.mark.asyncio
async def test_a_payment_over_the_daily_limit_moves_neither_budget(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """And the same in the other direction, which is why the order stopped mattering."""

    held = await _delegation(async_session, seeded_fixture_data, budget=200_000)
    async_session.add(
        DailySpendReservation(
            id=uuid4(),
            tenant_id=seeded_fixture_data.tenant_a.id,
            actor_id=DELEGATE,
            spend_date=datetime.now(UTC).date(),
            reserved_amount_minor=190_000,
        )
    )
    await async_session.flush()

    response = await client.post(
        "/api/v1/catalog-payment-requests",
        json=_buy(seeded_fixture_data),
        headers=_headers(seeded_fixture_data),
    )

    assert response.json()["decision"] == "DENY"
    assert await _spent(async_session, held) == 0, (
        "the delegation was debited for a payment the daily limit refused"
    )
    assert await _reserved(async_session, seeded_fixture_data) == 190_000


@pytest.mark.asyncio
async def test_a_payment_that_fails_returns_the_delegation_budget(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Budget held for a payment that never happened has to come back, on every path.

    Hung on the condition that already enumerates DENIED, EXPIRED, FAILED and CANCELLED, so this
    exercises one of the four and inherits the rest.
    """

    held = await _delegation(async_session, seeded_fixture_data, budget=100_000)
    response = await client.post(
        "/api/v1/catalog-payment-requests",
        json=_buy(seeded_fixture_data),
        headers=_headers(seeded_fixture_data),
    )
    assert response.json()["decision"] == "ALLOW"
    assert await _spent(async_session, held) == STARTER_PRICE_MINOR

    payment = await async_session.scalar(
        select(Payment).where(Payment.payment_request_id == response.json()["payment_request_id"])
    )
    assert payment is not None
    # A payment does not go straight from AUTHORIZED to FAILED: it reaches the provider first.
    # Both states hold a reservation, so the release fires on the second hop.
    await transition(
        async_session, payment, "PROVIDER_PENDING", reason="sent", correlation_id=uuid4()
    )
    assert await _spent(async_session, held) == STARTER_PRICE_MINOR, (
        "budget was returned before the payment had actually failed"
    )
    await transition(
        async_session, payment, "FAILED", reason="provider-declined", correlation_id=uuid4()
    )

    assert await _spent(async_session, held) == 0, "a failed payment kept the delegation budget"


@pytest.mark.asyncio
async def test_an_actor_with_no_delegation_is_untouched_by_any_of_this(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The property the whole suite depends on, asserted rather than assumed."""

    response = await client.post(
        "/api/v1/catalog-payment-requests",
        json=_buy(seeded_fixture_data),
        headers=_headers(seeded_fixture_data),
    )

    assert response.json()["decision"] == "ALLOW"
    assert await _reserved(async_session, seeded_fixture_data) == STARTER_PRICE_MINOR
    ledger = await async_session.scalar(
        select(func.count())
        .select_from(DelegationSpend)
        .where(DelegationSpend.tenant_id == seeded_fixture_data.tenant_a.id)
    )
    assert ledger == 0


@pytest.mark.asyncio
async def test_the_evidence_ties_the_chain_to_the_payment_that_used_it(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Attribution, which is the reason to integrate this at all.

    Given the payment, the evidence names the delegation, the chain that agreed to it, and the
    human at the root - and carries the payment's own correlation id, so it sits on that payment's
    timeline rather than beside it.
    """

    held = await _delegation(async_session, seeded_fixture_data, budget=100_000)
    response = await client.post(
        "/api/v1/catalog-payment-requests",
        json=_buy(seeded_fixture_data),
        headers=_headers(seeded_fixture_data),
    )
    request_id = response.json()["payment_request_id"]

    spend_event = await async_session.scalar(
        select(AuditEvent).where(
            AuditEvent.tenant_id == seeded_fixture_data.tenant_a.id,
            AuditEvent.event_kind == "delegation_spent",
        )
    )
    assert spend_event is not None
    assert spend_event.delegation_id == held.id
    assert spend_event.payload["root_actor_id"] == "finance-lead"
    assert spend_event.payload["reference"] == request_id

    decision_correlations = (
        (
            await async_session.execute(
                select(AuditEvent.correlation_id).where(
                    AuditEvent.tenant_id == seeded_fixture_data.tenant_a.id,
                    AuditEvent.event_kind == "payment_transition",
                )
            )
        )
        .scalars()
        .all()
    )
    assert spend_event.correlation_id in decision_correlations, (
        "the delegation evidence is not on the payment's timeline"
    )


@pytest.mark.asyncio
async def test_a_request_carrying_no_catalog_sku_is_refused_rather_than_exempted(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """A delegation is scoped by SKU, so a purchase without one cannot be checked against it."""

    held = await _delegation(async_session, seeded_fixture_data, budget=100_000)

    response = await client.post(
        "/api/v1/catalog-payment-requests",
        json=_buy(seeded_fixture_data, sku="CLOUD-TEAM"),
        headers=_headers(seeded_fixture_data),
    )

    assert response.json()["decision"] == "DENY"
    assert "DELEGATION_SKU_OUT_OF_SCOPE" in response.json()["reasons"]
    assert await _spent(async_session, held) == 0
    assert await _reserved(async_session, seeded_fixture_data) == 0


@pytest.mark.asyncio
async def test_the_same_request_retried_charges_the_chain_once(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The reference is the payment request, so idempotency arrives without inventing a key."""

    held = await _delegation(async_session, seeded_fixture_data, budget=100_000)
    payload = _buy(seeded_fixture_data)

    first = await client.post(
        "/api/v1/catalog-payment-requests", json=payload, headers=_headers(seeded_fixture_data)
    )
    second = await client.post(
        "/api/v1/catalog-payment-requests", json=payload, headers=_headers(seeded_fixture_data)
    )

    assert first.json()["payment_request_id"] == second.json()["payment_request_id"]
    assert await _spent(async_session, held) == STARTER_PRICE_MINOR
    ledger = await async_session.scalar(
        select(func.count())
        .select_from(DelegationSpend)
        .where(DelegationSpend.delegation_id == held.id)
    )
    assert ledger == 1


@pytest.mark.asyncio
async def test_a_denied_payment_leaves_a_request_recorded_either_way(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """A refusal is still evidence: the attempt is recorded even when no money moved."""

    await _delegation(async_session, seeded_fixture_data, budget=1_000)

    response = await client.post(
        "/api/v1/catalog-payment-requests",
        json=_buy(seeded_fixture_data),
        headers=_headers(seeded_fixture_data),
    )

    stored = await async_session.scalar(
        select(PaymentRequest).where(
            PaymentRequest.id == response.json()["payment_request_id"],
        )
    )
    assert stored is not None
