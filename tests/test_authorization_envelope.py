"""One fixed-shape answer to "may money move here, and on whose authority".

Everything in the envelope is already somewhere in the evidence record. What it adds is that the
answer has one shape and one place, so comparing two purchases does not mean reading five sections
each and knowing which of them outranks the others.

`provider_action_allowed` is the field worth being careful about, and these tests are mostly about
keeping it honest. It describes stored rows - is there a live, unused authority over an authorized
payment whose chain is still live - and it is not the gate. `consume_checkout_authority` re-runs
all of it under row locks and is what actually stands between an agent and Razorpay. An envelope
that says ALLOWED and a consume that refuses a second later is the two doing their jobs, not a
contradiction, which is why nothing here asserts that the envelope predicts the outcome.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fixtures import FixtureData
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.app import app
from api.dependencies import get_session
from delegation.chain import Bounds, grant_root, revoke
from models.domain import Delegation

ACTOR = "envelope-actor"
PRINCIPAL = "envelope-finance-lead"
TOKEN = "envelope-approver-token"  # noqa: S105 - synthetic
APPROVER = "envelope-human"
STARTER_PRICE_MINOR = 39_900


@pytest_asyncio.fixture
async def client(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("TRUSTGATE_API_ACTOR_ID", ACTOR)
    monkeypatch.setenv("DEMO_APPROVER_TOKEN", TOKEN)
    monkeypatch.setenv("DEMO_APPROVER_ID", APPROVER)
    # The console is off unless asked for, and a 404 here would look like a missing row
    # rather than a surface that ships disabled.
    monkeypatch.setenv("ENABLE_CONSOLE", "true")

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


def _headers(data: FixtureData) -> dict[str, str]:
    return {"X-Tenant-Id": str(data.tenant_a.id)}


async def _root(session: AsyncSession, data: FixtureData, *, budget: int = 200_000) -> Delegation:
    return await grant_root(
        session,
        tenant_id=data.tenant_a.id,
        policy=data.tenant_a_policy,
        principal_actor_id=PRINCIPAL,
        delegate_actor_id=ACTOR,
        bounds=Bounds(
            budget_minor=budget,
            max_amount_minor=100_000,
            allowed_skus=("CLOUD-STARTER",),
            purpose="envelope",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        ),
    )


async def _buy(client: AsyncClient, data: FixtureData, *, sku: str = "CLOUD-STARTER") -> UUID:
    response = await client.post(
        "/api/v1/catalog-payment-requests",
        json={
            "sku": sku,
            "quantity": 1,
            "purpose": "Provision an isolated build environment.",
            "idempotency_key": str(uuid4()),
        },
        headers=_headers(data),
    )
    assert response.status_code == 201, response.text
    return UUID(str(response.json()["payment_request_id"]))


async def _envelope(client: AsyncClient, data: FixtureData, request_id: UUID) -> dict[str, Any]:
    response = await client.get(
        f"/api/v1/payment-requests/{request_id}/evidence", headers=_headers(data)
    )
    assert response.status_code == 200, response.text
    envelope: dict[str, Any] = response.json()["envelope"]
    return envelope


# --- the shape is the point -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_envelope_answers_every_field_for_an_ordinary_purchase(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    """A fixed shape means saying "none" rather than saying nothing."""

    request_id = await _buy(client, seeded_fixture_data)

    envelope = await _envelope(client, seeded_fixture_data, request_id)

    assert envelope["payment_request_id"] == str(request_id)
    assert envelope["decision"] == "ALLOW"
    assert envelope["amount_minor"] == STARTER_PRICE_MINOR
    assert envelope["currency"] == "INR"
    assert envelope["policy_version"] is not None
    assert envelope["approval_state"] == "NOT_REQUIRED"
    assert envelope["delegation_id"] is None
    assert envelope["delegation_root_actor_id"] is None


@pytest.mark.asyncio
async def test_the_envelope_names_the_delegation_and_the_human_behind_it(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    held = await _root(async_session, seeded_fixture_data)
    request_id = await _buy(client, seeded_fixture_data)

    envelope = await _envelope(client, seeded_fixture_data, request_id)

    assert envelope["delegation_id"] == str(held.id)
    assert envelope["delegation_root_actor_id"] == PRINCIPAL


# --- provider action, which is the field that has to stay honest -------------------------------


@pytest.mark.asyncio
async def test_an_authorized_purchase_with_no_authority_yet_is_not_allowed_to_pay(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    """The state the whole design exists to produce, said in one field.

    Authorized and unable to pay is not a half-finished purchase; it is the outcome. An envelope
    that reported ALLOWED here would be describing the opposite of what the system does.
    """

    request_id = await _buy(client, seeded_fixture_data)

    envelope = await _envelope(client, seeded_fixture_data, request_id)

    assert envelope["provider_action_allowed"] is False
    assert envelope["provider_action_blocked_reason"] == "NO_CHECKOUT_AUTHORITY_ISSUED"
    assert envelope["authority_expires_at"] is None


@pytest.mark.asyncio
async def test_issuing_the_authority_is_what_makes_the_provider_action_allowed(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    request_id = await _buy(client, seeded_fixture_data)
    issued = await client.post(
        f"/api/v1/checkout-authorities/{request_id}", headers=_headers(seeded_fixture_data)
    )
    assert issued.status_code == 200, issued.text

    envelope = await _envelope(client, seeded_fixture_data, request_id)

    assert envelope["provider_action_allowed"] is True
    assert envelope["provider_action_blocked_reason"] is None
    assert envelope["authority_expires_at"] is not None


@pytest.mark.asyncio
async def test_a_revoked_chain_takes_the_provider_action_away_again(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The consume-time window, described before anyone tries to consume.

    The authority is issued and unused, the payment is authorized, and the chain underneath has
    been revoked. Nothing about the authority row changed - only the authority behind it did.
    """

    held = await _root(async_session, seeded_fixture_data)
    request_id = await _buy(client, seeded_fixture_data)
    issued = await client.post(
        f"/api/v1/checkout-authorities/{request_id}", headers=_headers(seeded_fixture_data)
    )
    assert issued.status_code == 200, issued.text
    assert (await _envelope(client, seeded_fixture_data, request_id))["provider_action_allowed"]

    await revoke(async_session, tenant_id=seeded_fixture_data.tenant_a.id, delegation_id=held.id)

    envelope = await _envelope(client, seeded_fixture_data, request_id)

    assert envelope["provider_action_allowed"] is False
    assert envelope["provider_action_blocked_reason"] == "DELEGATION_REVOKED"


@pytest.mark.asyncio
async def test_a_refused_purchase_is_not_allowed_to_pay_and_carries_its_reasons(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
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
    request_id = UUID(str(response.json()["payment_request_id"]))

    envelope = await _envelope(client, seeded_fixture_data, request_id)

    assert envelope["decision"] == "DENY"
    assert envelope["reason_codes"] != []
    assert envelope["provider_action_allowed"] is False
    assert envelope["provider_action_blocked_reason"] == "PAYMENT_NOT_AUTHORIZED"


# --- approval, which has more than two states --------------------------------------------------


@pytest.mark.asyncio
async def test_a_purchase_awaiting_a_human_says_so(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    """REQUIRED and NOT_REQUIRED read very differently, and both used to be a missing approval."""

    request_id = await _buy(client, seeded_fixture_data, sku="CLOUD-TEAM")

    envelope = await _envelope(client, seeded_fixture_data, request_id)

    assert envelope["decision"] == "REQUIRE_APPROVAL"
    assert envelope["approval_state"] == "REQUIRED"
    assert envelope["provider_action_allowed"] is False


@pytest.mark.asyncio
async def test_a_consumed_approval_is_reported_as_consumed(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    request_id = await _buy(client, seeded_fixture_data, sku="CLOUD-TEAM")
    granted = await client.post(
        f"/api/v1/approvals/{request_id}/grant",
        headers={**_headers(seeded_fixture_data), "X-Approver-Token": TOKEN},
    )
    assert granted.status_code in (200, 201), granted.text

    envelope = await _envelope(client, seeded_fixture_data, request_id)

    assert envelope["approval_state"] == "CONSUMED"


# --- and the same fact on the timeline ---------------------------------------------------------


@pytest.mark.asyncio
async def test_the_console_says_whose_authority_a_delegated_purchase_ran_under(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """A reviewer scanning rows would otherwise see an agent buying things and no owner."""

    await _root(async_session, seeded_fixture_data)
    await _buy(client, seeded_fixture_data)

    page = await client.get(f"/console/{seeded_fixture_data.tenant_a.id}")

    assert page.status_code == 200
    assert f"under authority from {PRINCIPAL}" in page.text


@pytest.mark.asyncio
async def test_the_console_says_nothing_about_authority_when_there_was_none(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    await _buy(client, seeded_fixture_data)

    page = await client.get(f"/console/{seeded_fixture_data.tenant_a.id}")

    assert page.status_code == 200
    assert "under authority from" not in page.text
