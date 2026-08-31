"""Revoking a delegation after the payment it authorized, but before the money moves.

Authorization consults a chain once. Checkout happens afterwards - a separate request, minutes
later - and until now nothing asked the chain a second time. So a human could revoke an agent's
authority, watch the API confirm it, and still have the provider called under the permission they
had just withdrawn. Every hop was correct; nobody was re-reading them.

That gap is invisible from inside the delegation engine, because the engine was never wrong. It is
only visible from the payment path, which is why these tests live here and not beside `spend`.

Two gates, deliberately asymmetric. Issuing an authority refuses *and* cancels the payment, which
returns the daily reservation and the delegated budget through the one path that already knows
which states a payment dies in. Consuming one only refuses: every write inside consume is undone by
the rollback that carries its raise out of `get_session`, and that rollback is the property that
makes a crash mid-consume fail closed. Trading it for a budget release would be trading a guarantee
about money for a guarantee about bookkeeping.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fixtures import FixtureData
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app import app
from api.dependencies import get_session
from api.routes.checkout_authorities import (
    CheckoutAuthorityUnavailableError,
    consume_checkout_authority,
)
from delegation.chain import Bounds, DelegationRefused, grant, grant_root, revoke
from models.domain import (
    DailySpendReservation,
    Delegation,
    Payment,
    PaymentRequest,
)

DELEGATE = "checkout-delegation-actor"
MIDDLE = "checkout-delegation-team-lead"
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


def _headers(data: FixtureData) -> dict[str, str]:
    return {"X-Tenant-Id": str(data.tenant_a.id)}


def _bounds(*, budget: int, cap: int = 100_000, hours: int = 24) -> Bounds:
    return Bounds(
        budget_minor=budget,
        max_amount_minor=cap,
        allowed_skus=("CLOUD-STARTER",),
        purpose="checkout integration",
        expires_at=datetime.now(UTC) + timedelta(hours=hours),
    )


async def _root(
    session: AsyncSession, data: FixtureData, *, delegate: str = DELEGATE, budget: int = 200_000
) -> Delegation:
    return await grant_root(
        session,
        tenant_id=data.tenant_a.id,
        policy=data.tenant_a_policy,
        principal_actor_id="finance-lead",
        delegate_actor_id=delegate,
        bounds=_bounds(budget=budget),
    )


async def _buy(client: AsyncClient, data: FixtureData) -> dict[str, object]:
    """Authorize a purchase the ordinary way, so the chain is consulted exactly as it is in life."""

    response = await client.post(
        "/api/v1/catalog-payment-requests",
        json={
            "sku": "CLOUD-STARTER",
            "quantity": 1,
            "purpose": "Provision an isolated build environment.",
            "idempotency_key": str(uuid4()),
        },
        headers=_headers(data),
    )
    assert response.status_code == 201, response.text
    assert response.json()["decision"] == "ALLOW"
    body: dict[str, object] = response.json()
    return body


async def _issue(client: AsyncClient, data: FixtureData, request_id: object) -> Response:
    return await client.post(f"/api/v1/checkout-authorities/{request_id}", headers=_headers(data))


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


async def _state(session: AsyncSession, request_id: UUID) -> str | None:
    return await session.scalar(
        select(Payment.state).where(Payment.payment_request_id == request_id)
    )


# --- the link the whole thing hangs off ----------------------------------------------------


@pytest.mark.asyncio
async def test_the_request_records_the_chain_it_actually_spent(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    held = await _root(async_session, seeded_fixture_data)

    created = await _buy(client, seeded_fixture_data)

    stored = await async_session.scalar(
        select(PaymentRequest.delegation_id).where(
            PaymentRequest.id == UUID(str(created["payment_request_id"]))
        )
    )
    assert stored == held.id


@pytest.mark.asyncio
async def test_a_request_that_spent_no_delegation_records_none(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The regression net. An actor holding nothing takes the path it took before any of this."""

    created = await _buy(client, seeded_fixture_data)

    stored = await async_session.scalar(
        select(PaymentRequest.delegation_id).where(
            PaymentRequest.id == UUID(str(created["payment_request_id"]))
        )
    )
    assert stored is None


@pytest.mark.asyncio
async def test_a_refused_request_records_no_delegation_even_though_the_actor_held_one(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """`delegation_id` records what was debited, not what the actor happened to hold.

    Recording the held chain on a refused request would hand checkout a chain to re-ask about a
    payment that never spent anything, and the release path would try to give back a debit that was
    never taken.
    """

    await _root(async_session, seeded_fixture_data, budget=1_000)

    response = await client.post(
        "/api/v1/catalog-payment-requests",
        json={
            "sku": "CLOUD-STARTER",
            "quantity": 1,
            "purpose": "Provision an isolated build environment.",
            "idempotency_key": str(uuid4()),
        },
        headers=_headers(seeded_fixture_data),
    )

    assert response.json()["decision"] == "DENY"
    stored = await async_session.scalar(
        select(PaymentRequest.delegation_id).where(
            PaymentRequest.id == UUID(str(response.json()["payment_request_id"]))
        )
    )
    assert stored is None


# --- issuing, which refuses and gives the budgets back -------------------------------------


@pytest.mark.asyncio
async def test_a_revoked_delegation_stops_the_checkout_it_had_already_authorized(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The gap this file exists for: authorized while live, revoked before the money moved."""

    held = await _root(async_session, seeded_fixture_data)
    created = await _buy(client, seeded_fixture_data)
    await revoke(async_session, tenant_id=seeded_fixture_data.tenant_a.id, delegation_id=held.id)

    response = await _issue(client, seeded_fixture_data, created["payment_request_id"])

    assert response.status_code == 409
    assert response.json()["detail"] == "DELEGATION_REVOKED"


@pytest.mark.asyncio
async def test_the_blocked_checkout_gives_back_both_budgets(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Nothing sweeps a stranded AUTHORIZED payment, so refusing without releasing strands both.

    The daily reservation and the delegated budget are held for money that will now never move.
    CANCELLED is the transition that returns them, and asserting on the budgets rather than on the
    state is deliberate: the state is the mechanism, the budgets are the promise.
    """

    held = await _root(async_session, seeded_fixture_data)
    created = await _buy(client, seeded_fixture_data)
    request_id = UUID(str(created["payment_request_id"]))
    assert await _spent(async_session, held) == STARTER_PRICE_MINOR
    assert await _reserved(async_session, seeded_fixture_data) == STARTER_PRICE_MINOR

    await revoke(async_session, tenant_id=seeded_fixture_data.tenant_a.id, delegation_id=held.id)
    await _issue(client, seeded_fixture_data, created["payment_request_id"])

    assert await _state(async_session, request_id) == "CANCELLED"
    assert await _spent(async_session, held) == 0, "the delegated budget stayed debited"
    assert await _reserved(async_session, seeded_fixture_data) == 0, (
        "the daily reservation stayed moved for a payment that will never happen"
    )


@pytest.mark.asyncio
async def test_revoking_a_parent_stops_a_child_at_checkout(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """A hop is only as good as everything above it, at checkout as much as at authorization.

    Checking only the delegation the request names would pass here: the leaf is untouched. The
    authority it stands on is not.
    """

    root = await _root(async_session, seeded_fixture_data, delegate=MIDDLE)
    await grant(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        parent_id=root.id,
        delegator_actor_id=MIDDLE,
        delegate_actor_id=DELEGATE,
        bounds=_bounds(budget=100_000, hours=12),
    )
    created = await _buy(client, seeded_fixture_data)

    await revoke(async_session, tenant_id=seeded_fixture_data.tenant_a.id, delegation_id=root.id)
    response = await _issue(client, seeded_fixture_data, created["payment_request_id"])

    assert response.status_code == 409
    assert response.json()["detail"] == "DELEGATION_REVOKED"


@pytest.mark.asyncio
async def test_an_expired_delegation_stops_the_checkout(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Revocation is a human act and expiry is not, and the money must not move either way.

    Asked of the engine rather than through the route, because there is no honest way to age a hop
    from the outside: `freeze_delegation_bounds` refuses an update to `expires_at` - correctly, and
    an earlier draft of this test discovered that by being refused - and the alternative is a real
    sleep, which buys a flake to test a comparison. `as_of` is the seam the engine already offers,
    and the revocation tests above prove the route reaches this function.
    """

    from delegation.chain import assert_chain_live

    held = await _root(async_session, seeded_fixture_data)

    with pytest.raises(DelegationRefused) as refused:
        await assert_chain_live(
            async_session,
            tenant_id=seeded_fixture_data.tenant_a.id,
            delegation_id=held.id,
            as_of=held.expires_at + timedelta(seconds=1),
        )

    assert refused.value.reason == "DELEGATION_EXPIRED"


@pytest.mark.asyncio
async def test_a_payment_holding_no_delegation_still_gets_its_authority(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    """The new gate must be invisible to every payment that was working before it existed."""

    created = await _buy(client, seeded_fixture_data)

    response = await _issue(client, seeded_fixture_data, created["payment_request_id"])

    assert response.status_code == 200, response.text
    assert response.json()["payment_request_id"] == created["payment_request_id"]


@pytest.mark.asyncio
async def test_a_live_delegation_is_no_obstacle(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """A gate that refuses everything also passes every test above it."""

    await _root(async_session, seeded_fixture_data)
    created = await _buy(client, seeded_fixture_data)

    response = await _issue(client, seeded_fixture_data, created["payment_request_id"])

    assert response.status_code == 200, response.text


# --- consuming, which only refuses ---------------------------------------------------------


@pytest.mark.asyncio
async def test_a_chain_revoked_after_its_authority_was_issued_refuses_the_consume(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The narrower window: an authority already in hand, revoked inside its fifteen minutes.

    This is the last gate before a provider order exists, so it is the one that decides whether
    revocation is a promise about money or a promise about a database row.
    """

    held = await _root(async_session, seeded_fixture_data)
    created = await _buy(client, seeded_fixture_data)
    issued = await _issue(client, seeded_fixture_data, created["payment_request_id"])
    assert issued.status_code == 200, issued.text

    await revoke(async_session, tenant_id=seeded_fixture_data.tenant_a.id, delegation_id=held.id)

    with pytest.raises(CheckoutAuthorityUnavailableError) as refused:
        await consume_checkout_authority(
            async_session,
            tenant_id=seeded_fixture_data.tenant_a.id,
            checkout_authority_id=UUID(str(issued.json()["checkout_authority_id"])),
            correlation_id=uuid4(),
        )

    assert refused.value.reason == "DELEGATION_REVOKED"


@pytest.mark.asyncio
async def test_the_refused_consume_leaves_the_authority_unused(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Fail closed means the authority survives to be refused again, not spent on a refusal."""

    from models.domain import CheckoutAuthority

    held = await _root(async_session, seeded_fixture_data)
    created = await _buy(client, seeded_fixture_data)
    issued = await _issue(client, seeded_fixture_data, created["payment_request_id"])
    authority_id = UUID(str(issued.json()["checkout_authority_id"]))
    await revoke(async_session, tenant_id=seeded_fixture_data.tenant_a.id, delegation_id=held.id)

    with pytest.raises(CheckoutAuthorityUnavailableError):
        await consume_checkout_authority(
            async_session,
            tenant_id=seeded_fixture_data.tenant_a.id,
            checkout_authority_id=authority_id,
            correlation_id=uuid4(),
        )

    used_at = await async_session.scalar(
        select(CheckoutAuthority.used_at).where(CheckoutAuthority.id == authority_id)
    )
    assert used_at is None


@pytest.mark.asyncio
async def test_a_live_chain_consumes_normally(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    await _root(async_session, seeded_fixture_data)
    created = await _buy(client, seeded_fixture_data)
    issued = await _issue(client, seeded_fixture_data, created["payment_request_id"])

    consumed = await consume_checkout_authority(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        checkout_authority_id=UUID(str(issued.json()["checkout_authority_id"])),
        correlation_id=uuid4(),
    )

    assert consumed.used_at is not None


# --- the engine's own rule, asked from here ------------------------------------------------


@pytest.mark.asyncio
async def test_asking_a_chain_that_does_not_exist_is_a_refusal_not_a_crash(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    from delegation.chain import assert_chain_live

    with pytest.raises(DelegationRefused) as refused:
        await assert_chain_live(
            async_session, tenant_id=seeded_fixture_data.tenant_a.id, delegation_id=uuid4()
        )

    assert refused.value.reason == "DELEGATION_NOT_FOUND"
