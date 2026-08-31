"""The receipt says which human's authority a purchase ran under, and what it cost them.

Delegation was enforced before this and invisible afterwards. A payment could be checked against a
four-hop chain, debit a budget a finance lead had granted, and produce an evidence record that
mentioned none of it - so the accountability the chain exists to create stopped at the database.

The audit trail is the other half. `delegation_spent` and `delegation_released` carried a
`delegation_id` and a correlation and no durable link to the purchase, which meant the receipt
could not put them on the timeline without matching JSON payloads - the exact convention the
evidence builder refuses to depend on. They carry `payment_request_id` now.
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
from delegation.chain import Bounds, grant, grant_root, revoke
from models.domain import AuditEvent, Delegation, Payment
from state_machine.transitions import transition

DELEGATE = "evidence-delegation-actor"
MIDDLE = "evidence-delegation-team-lead"
PRINCIPAL = "evidence-finance-lead"
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
        purpose="evidence",
        expires_at=datetime.now(UTC) + timedelta(hours=hours),
    )


async def _root(
    session: AsyncSession, data: FixtureData, *, delegate: str = DELEGATE, budget: int = 200_000
) -> Delegation:
    return await grant_root(
        session,
        tenant_id=data.tenant_a.id,
        policy=data.tenant_a_policy,
        principal_actor_id=PRINCIPAL,
        delegate_actor_id=delegate,
        bounds=_bounds(budget=budget),
    )


async def _buy(client: AsyncClient, data: FixtureData) -> UUID:
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
    return UUID(str(response.json()["payment_request_id"]))


async def _evidence(client: AsyncClient, data: FixtureData, request_id: UUID) -> dict[str, object]:
    response = await client.get(
        f"/api/v1/payment-requests/{request_id}/evidence", headers=_headers(data)
    )
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


async def _receipt(client: AsyncClient, data: FixtureData, request_id: UUID) -> Response:
    return await client.get(
        f"/api/v1/payment-requests/{request_id}/receipt", headers=_headers(data)
    )


# --- the chain, in the record ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_evidence_names_the_human_the_authority_came_from(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The accountability claim, finally readable from the artifact rather than the database."""

    await _root(async_session, seeded_fixture_data)
    request_id = await _buy(client, seeded_fixture_data)

    delegation = (await _evidence(client, seeded_fixture_data, request_id))["delegation"]

    assert delegation is not None
    assert delegation["root_actor_id"] == PRINCIPAL
    assert delegation["spent_minor"] == STARTER_PRICE_MINOR
    assert delegation["spent_sku"] == "CLOUD-STARTER"
    assert delegation["refusal_reason"] is None


@pytest.mark.asyncio
async def test_the_evidence_shows_every_hop_root_first(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """A two-hop chain is where "who authorized this" stops having an obvious answer."""

    root = await _root(async_session, seeded_fixture_data, delegate=MIDDLE)
    await grant(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        parent_id=root.id,
        delegator_actor_id=MIDDLE,
        delegate_actor_id=DELEGATE,
        bounds=_bounds(budget=100_000, hours=12),
    )
    request_id = await _buy(client, seeded_fixture_data)

    delegation = (await _evidence(client, seeded_fixture_data, request_id))["delegation"]

    assert delegation is not None
    chain = delegation["chain"]
    assert [hop["depth"] for hop in chain] == [0, 1]
    assert chain[0]["delegate_actor_id"] == MIDDLE
    assert chain[1]["delegate_actor_id"] == DELEGATE
    assert chain[1]["remaining_minor"] == 100_000 - STARTER_PRICE_MINOR


@pytest.mark.asyncio
async def test_a_purchase_under_no_delegation_has_no_delegation_section(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    """Absent, not empty. "No delegation was involved" and "one was and did nothing" differ."""

    request_id = await _buy(client, seeded_fixture_data)

    assert (await _evidence(client, seeded_fixture_data, request_id))["delegation"] is None


# --- the trail the spend leaves --------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_spend_joins_the_purchase_timeline(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The spend's audit row could not carry this key when it was written.

    A spend happens inside the budget savepoint, before the request it names exists, so the foreign
    key is written once the row is there. Without it the event is recorded and unreachable from the
    purchase it belongs to.
    """

    await _root(async_session, seeded_fixture_data)
    request_id = await _buy(client, seeded_fixture_data)

    linked = (
        await async_session.scalars(
            select(AuditEvent.event_kind).where(
                AuditEvent.payment_request_id == request_id,
                AuditEvent.delegation_id.is_not(None),
            )
        )
    ).all()

    assert "delegation_spent" in linked
    trail = (await _evidence(client, seeded_fixture_data, request_id))["audit_trail"]
    assert "delegation_spent" in [entry["event_kind"] for entry in trail]


@pytest.mark.asyncio
async def test_a_returned_budget_is_shown_as_returned(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """A payment that dies gives its budget back, and the receipt has to say so.

    Otherwise the record shows a debit with no matching credit, which reads as money spent.
    """

    await _root(async_session, seeded_fixture_data)
    request_id = await _buy(client, seeded_fixture_data)
    payment = await async_session.scalar(
        select(Payment).where(Payment.payment_request_id == request_id)
    )
    assert payment is not None
    await transition(
        async_session,
        payment,
        "CANCELLED",
        reason="testing the release",
        correlation_id=uuid4(),
    )

    delegation = (await _evidence(client, seeded_fixture_data, request_id))["delegation"]
    trail = (await _evidence(client, seeded_fixture_data, request_id))["audit_trail"]

    assert delegation is not None
    assert delegation["released_at"] is not None
    assert "delegation_released" in [entry["event_kind"] for entry in trail]


@pytest.mark.asyncio
async def test_a_checkout_refused_on_a_dead_chain_says_so_in_the_evidence(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The refusal the receipt would otherwise render as an unexplained cancellation."""

    held = await _root(async_session, seeded_fixture_data)
    request_id = await _buy(client, seeded_fixture_data)
    await revoke(async_session, tenant_id=seeded_fixture_data.tenant_a.id, delegation_id=held.id)
    await client.post(
        f"/api/v1/checkout-authorities/{request_id}", headers=_headers(seeded_fixture_data)
    )

    delegation = (await _evidence(client, seeded_fixture_data, request_id))["delegation"]

    assert delegation is not None
    assert delegation["refusal_reason"] == "DELEGATION_REVOKED"
    assert delegation["chain"][0]["revoked_at"] is not None
    assert delegation["released_at"] is not None, "the budget was not returned with the refusal"


# --- and the same facts, rendered -------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_receipt_renders_the_chain_and_the_human_at_its_root(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The receipt is a rendering of the record, so this is about the rendering existing."""

    root = await _root(async_session, seeded_fixture_data, delegate=MIDDLE)
    await grant(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        parent_id=root.id,
        delegator_actor_id=MIDDLE,
        delegate_actor_id=DELEGATE,
        bounds=_bounds(budget=100_000, hours=12),
    )
    request_id = await _buy(client, seeded_fixture_data)

    page = await _receipt(client, seeded_fixture_data, request_id)

    assert page.status_code == 200
    assert "Delegated authority" in page.text
    assert PRINCIPAL in page.text
    assert MIDDLE in page.text
    assert DELEGATE in page.text


@pytest.mark.asyncio
async def test_the_receipt_of_an_undelegated_purchase_says_nothing_about_delegation(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    """No section, and the envelope saying "none" rather than staying silent.

    The two are different claims. A missing section means no chain was involved; the envelope has
    a fixed shape and answers the question either way, which is what makes it comparable between
    one purchase and the next.
    """

    request_id = await _buy(client, seeded_fixture_data)

    page = await _receipt(client, seeded_fixture_data, request_id)

    assert page.status_code == 200
    assert "Delegated authority" not in page.text
    assert "Delegated by" in page.text
