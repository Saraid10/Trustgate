"""Granting authority over HTTP, and the token that has to be held to do it.

A chain could only be created from a Python shell before this, which made delegation something an
operator did rather than something the system offered. These routes offer it - to a human.

Every write here needs the approver token, the same one the approvals route uses. That is not
belt-and-braces. The alternative to a human-gated route is a tool, and a tool is reachable by the
agent; the agent is offered exactly five tools, none of them a grant, and a test asserts it. This
router exists so that granting is possible without ever becoming something the agent can do.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from fixtures import FixtureData
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app import app
from api.dependencies import get_session
from models.domain import Delegation

TOKEN = "delegation-api-test-token"  # noqa: S105 - synthetic

_GRANTED_AT = datetime.now(UTC)
"""One instant for every hop in a test: a child may not outlive its parent, and two calls to
`now()` a millisecond apart are enough for it to."""
APPROVER = "delegation-api-human"


@pytest_asyncio.fixture
async def client(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("DEMO_APPROVER_TOKEN", TOKEN)
    monkeypatch.setenv("DEMO_APPROVER_ID", APPROVER)

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


def _headers(data: FixtureData, *, token: str | None = TOKEN) -> dict[str, str]:
    headers = {"X-Tenant-Id": str(data.tenant_a.id)}
    if token is not None:
        headers["X-Approver-Token"] = token
    return headers


def _grant(**overrides: object) -> dict[str, object]:
    return {
        "delegate_actor_id": f"agent-{uuid4().hex[:8]}",
        "budget_minor": 50_000,
        "max_amount_minor": 20_000,
        "allowed_skus": ["CLOUD-STARTER"],
        "purpose": "procurement",
        "expires_at": (_GRANTED_AT + timedelta(hours=6)).isoformat(),
        **overrides,
    }


@pytest.mark.asyncio
async def test_a_human_can_grant_a_root_and_the_chain_names_them(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    response = await client.post(
        "/api/v1/delegations", json=_grant(), headers=_headers(seeded_fixture_data)
    )

    assert response.status_code == 201, response.text
    chain = response.json()["chain"]
    assert len(chain) == 1
    assert chain[0]["depth"] == 0
    assert chain[0]["root_actor_id"] == APPROVER
    assert chain[0]["remaining_minor"] == 50_000


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "wrong-token"], ids=["absent", "wrong"])
async def test_granting_without_the_human_token_is_refused(
    client: AsyncClient, seeded_fixture_data: FixtureData, token: str | None
) -> None:
    """The single most important assertion on this router."""

    response = await client.post(
        "/api/v1/delegations",
        json=_grant(),
        headers=_headers(seeded_fixture_data, token=token),
    )

    assert response.status_code in (403, 422)


@pytest.mark.asyncio
async def test_a_child_narrows_and_the_chain_comes_back_root_first(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    root = await client.post(
        "/api/v1/delegations", json=_grant(), headers=_headers(seeded_fixture_data)
    )
    parent_id = root.json()["chain"][0]["delegation_id"]

    child = await client.post(
        "/api/v1/delegations",
        json=_grant(budget_minor=20_000, max_amount_minor=10_000, parent_id=parent_id),
        headers=_headers(seeded_fixture_data),
    )

    assert child.status_code == 201, child.text
    chain = child.json()["chain"]
    assert [hop["depth"] for hop in chain] == [0, 1]
    assert chain[0]["allocated_minor"] == 20_000
    assert chain[0]["remaining_minor"] == 30_000


@pytest.mark.asyncio
async def test_a_child_that_widens_its_parent_is_refused_with_a_reason(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    root = await client.post(
        "/api/v1/delegations", json=_grant(), headers=_headers(seeded_fixture_data)
    )
    parent_id = root.json()["chain"][0]["delegation_id"]

    child = await client.post(
        "/api/v1/delegations",
        json=_grant(budget_minor=999_000, parent_id=parent_id),
        headers=_headers(seeded_fixture_data),
    )

    assert child.status_code == 409
    assert child.json()["detail"] == "DELEGATION_BUDGET_EXCEEDS_PARENT"


@pytest.mark.asyncio
async def test_a_root_wider_than_its_policy_is_refused(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    response = await client.post(
        "/api/v1/delegations",
        json=_grant(budget_minor=9_999_999),
        headers=_headers(seeded_fixture_data),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "DELEGATION_EXCEEDS_POLICY_DAILY_LIMIT"


@pytest.mark.asyncio
async def test_revoking_needs_the_token_and_ends_the_hop(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    root = await client.post(
        "/api/v1/delegations", json=_grant(), headers=_headers(seeded_fixture_data)
    )
    delegation_id = root.json()["chain"][0]["delegation_id"]

    refused = await client.post(
        f"/api/v1/delegations/{delegation_id}/revoke",
        headers=_headers(seeded_fixture_data, token="wrong-token"),  # noqa: S106 - synthetic
    )
    assert refused.status_code == 403

    accepted = await client.post(
        f"/api/v1/delegations/{delegation_id}/revoke",
        headers=_headers(seeded_fixture_data),
    )
    assert accepted.status_code == 200
    assert accepted.json()["chain"][0]["revoked_at"] is not None

    stored = await async_session.scalar(
        select(Delegation.revoked_at).where(Delegation.id == delegation_id)
    )
    assert stored is not None


@pytest.mark.asyncio
async def test_reading_a_chain_needs_no_token_because_reading_spends_nothing(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    root = await client.post(
        "/api/v1/delegations", json=_grant(), headers=_headers(seeded_fixture_data)
    )
    delegation_id = root.json()["chain"][0]["delegation_id"]

    response = await client.get(
        f"/api/v1/delegations/{delegation_id}",
        headers={"X-Tenant-Id": str(seeded_fixture_data.tenant_a.id)},
    )

    assert response.status_code == 200
    assert response.json()["chain"][0]["delegation_id"] == delegation_id


@pytest.mark.asyncio
async def test_a_chain_from_another_tenant_is_not_readable(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    """Tenant scoping, asserted rather than assumed, on a route that returns spending authority."""

    root = await client.post(
        "/api/v1/delegations", json=_grant(), headers=_headers(seeded_fixture_data)
    )
    delegation_id = root.json()["chain"][0]["delegation_id"]

    response = await client.get(
        f"/api/v1/delegations/{delegation_id}",
        headers={"X-Tenant-Id": str(seeded_fixture_data.tenant_b.id)},
    )

    assert response.status_code == 404


def test_the_agent_is_still_offered_no_way_to_grant_anything() -> None:
    """The reason this is a route and not a tool, asserted where someone will read it.

    Adding a grant tool would be the single change that undoes the argument of this project, and it
    would look entirely reasonable in a diff.
    """

    from mcp_server import server

    source = server.__file__
    assert source is not None
    text = Path(source).read_text(encoding="utf-8").casefold()

    for forbidden in ("delegation", "delegate", "grant_root"):
        assert forbidden not in text, (
            f"the agent tool surface mentions {forbidden!r}; granting must stay a human act"
        )
