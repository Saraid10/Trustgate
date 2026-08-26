"""The demo's approval step must find the right purchase and stay a separate identity.

The token literals below are suppressed on the line rather than for the file: the rule that
flags them exists to catch real credentials, and disabling it here would weaken that check
for everything else in this module.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fixtures import FixtureData
from sqlalchemy.ext.asyncio import AsyncSession

from agent.approve import ApprovalUnavailableError, find_pending_approval, grant
from models.domain import Payment, PaymentRequest


async def _awaiting_approval(
    session: AsyncSession, data: FixtureData, *, amount_minor: int = 60_000
) -> PaymentRequest:
    request = PaymentRequest(
        id=uuid4(),
        tenant_id=data.tenant_a.id,
        actor_id=data.tenant_a_actor_one,
        merchant_id=data.tenant_a_allowed_merchant.id,
        catalog_item_id=data.tenant_a_catalog_team.id,
        catalog_sku="CLOUD-TEAM",
        catalog_name="Cloud Team",
        merchant_display_name=data.tenant_a_allowed_merchant.name,
        quantity=1,
        purpose="Team plan for the sprint.",
        source="MCP_AGENT",
        amount_minor=amount_minor,
        currency="INR",
        order_ref=f"order-{uuid4()}",
        idempotency_key=str(uuid4()),
    )
    session.add(request)
    await session.flush()
    session.add(
        Payment(
            id=uuid4(),
            tenant_id=data.tenant_a.id,
            payment_request_id=request.id,
            state="APPROVAL_REQUIRED",
            captured_amount_minor=0,
            refunded_amount_minor=0,
        )
    )
    await session.flush()
    return request


async def test_it_finds_the_purchase_that_is_actually_waiting(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """No identifier has to be copied between commands, so the demo has one less thing to fumble."""

    request = await _awaiting_approval(async_session, seeded_fixture_data)

    pending = await find_pending_approval(async_session, seeded_fixture_data.tenant_a.id)

    assert pending is not None
    assert pending.payment_request_id == request.id
    assert pending.amount_minor == 60_000
    assert pending.sku == "CLOUD-TEAM"
    assert pending.actor_id == seeded_fixture_data.tenant_a_actor_one


async def test_it_finds_nothing_when_no_purchase_is_waiting(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """A missing approval is a state to report, not an exception to raise mid-demo."""

    pending = await find_pending_approval(async_session, seeded_fixture_data.tenant_a.id)

    assert pending is None


async def test_it_does_not_reach_across_tenants_for_something_to_approve(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Tenant scoping holds in demo tooling exactly as it does on the API."""

    await _awaiting_approval(async_session, seeded_fixture_data)

    pending = await find_pending_approval(async_session, seeded_fixture_data.tenant_b.id)

    assert pending is None


async def test_a_refusal_from_the_server_is_reported_in_words_not_a_traceback() -> None:
    """A demo failure should say what happened, on camera, without a stack trace.

    The server's reason code is the useful part, so it is surfaced rather than swallowed.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "APPROVER_IS_REQUESTER"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ApprovalUnavailableError) as raised:
            await grant(
                base_url="http://testserver",
                tenant_id=uuid4(),
                payment_request_id=uuid4(),
                token="a-token",  # noqa: S106 - synthetic
                client=client,
            )

    assert "APPROVER_IS_REQUESTER" in str(raised.value)


async def test_it_sends_the_tenant_and_approver_token_the_route_requires() -> None:
    """The approval is granted over HTTP holding a token the agent does not have.

    Wiring approval into the buyer would have made the demo shorter and the separation of duties
    fictional, so this asserts the request really carries a separate credential.
    """

    tenant_id = uuid4()
    payment_request_id = uuid4()
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "approval_id": str(uuid4()),
                "payment_request_id": str(payment_request_id),
                "policy_version": 1,
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        body = await grant(
            base_url="http://testserver/",
            tenant_id=tenant_id,
            payment_request_id=payment_request_id,
            token="separate-approver-token",  # noqa: S106 - synthetic
            client=client,
        )

    assert seen["x-tenant-id"] == str(tenant_id)
    assert seen["x-approver-token"] == "separate-approver-token"
    assert seen["url"].endswith(f"/api/v1/approvals/{payment_request_id}/grant")
    assert body["payment_request_id"] == str(payment_request_id)
