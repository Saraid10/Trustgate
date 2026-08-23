from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from uuid import uuid4

import pytest
import pytest_asyncio
from fixtures import FixtureData
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mcp_server.server import create_mcp_server
from models.domain import Approval, AuditEvent, Payment, PaymentRequest

McpCall = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]


@pytest_asyncio.fixture
async def mcp_call(
    async_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    seeded_fixture_data: FixtureData,
) -> McpCall:
    monkeypatch.setenv("MCP_TENANT_ID", str(seeded_fixture_data.tenant_a.id))
    monkeypatch.setenv("MCP_ACTOR_ID", seeded_fixture_data.tenant_a_actor_two)
    factory = async_sessionmaker(bind=async_session.bind, expire_on_commit=False)
    server = create_mcp_server(factory)

    async def call(name: str, arguments: dict[str, object]) -> dict[str, object]:
        result = await server.call_tool(name, arguments)
        if isinstance(result, tuple):
            _, result = result
        assert isinstance(result, dict)
        return result

    return call


@pytest.mark.asyncio
async def test_mcp_exposes_exactly_the_five_trustgate_tools(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, seeded_fixture_data: FixtureData
) -> None:
    monkeypatch.setenv("MCP_TENANT_ID", str(seeded_fixture_data.tenant_a.id))
    monkeypatch.setenv("MCP_ACTOR_ID", seeded_fixture_data.tenant_a_actor_two)
    server = create_mcp_server(async_sessionmaker(bind=async_session.bind, expire_on_commit=False))
    tools = await server.list_tools()
    names = {tool.name for tool in tools}
    assert names == {
        "list_catalog",
        "create_payment_request",
        "evaluate_payment_policy",
        "request_user_approval",
        "get_payment_status",
    }
    assert {"authorize_payment", "capture_payment", "call_provider"}.isdisjoint(names)


@pytest.mark.asyncio
async def test_mcp_tool_schemas_do_not_accept_tenant_or_secret_values(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, seeded_fixture_data: FixtureData
) -> None:
    monkeypatch.setenv("MCP_TENANT_ID", str(seeded_fixture_data.tenant_a.id))
    server = create_mcp_server(async_sessionmaker(bind=async_session.bind, expire_on_commit=False))
    schema = json.dumps([tool.inputSchema for tool in await server.list_tools()]).lower()
    assert "tenant_id" not in schema
    assert "provider_webhook_secret" not in schema
    assert "signature" not in schema
    assert "merchant_id" not in schema
    assert "amount_minor" not in schema
    assert "actor_id" not in schema


@pytest.mark.asyncio
async def test_mcp_reads_the_stored_policy_decision_for_the_configured_tenant(
    mcp_call: McpCall, seeded_fixture_data: FixtureData
) -> None:
    created = await mcp_call(
        "create_payment_request",
        {
            "sku": "CLOUD-STARTER",
            "quantity": 1,
            "purpose": "Provision a build environment.",
            "idempotency_key": str(uuid4()),
        },
    )
    result = await mcp_call(
        "evaluate_payment_policy", {"payment_request_id": created["payment_request_id"]}
    )
    assert result == {"found": True, "decision": "ALLOW", "reasons": [], "policy_version": 1}


@pytest.mark.asyncio
async def test_mcp_lists_only_active_items_for_the_configured_tenant(
    mcp_call: McpCall, seeded_fixture_data: FixtureData
) -> None:
    result = await mcp_call("list_catalog", {})
    assert result == {
        "items": [
            {
                "sku": "CLOUD-STARTER",
                "name": "Cloud Starter",
                "merchant_display_name": "A Allowed One",
                "description": "Synthetic cloud-credit package for the TrustGate demo.",
                "price_minor": 39_900,
                "currency": "INR",
                "max_quantity": 1,
            },
            {
                "sku": "CLOUD-TEAM",
                "name": "Cloud Team",
                "merchant_display_name": "A Allowed One",
                "description": "Synthetic higher-value cloud-credit package for approval demos.",
                "price_minor": 60_000,
                "currency": "INR",
                "max_quantity": 2,
            },
        ]
    }
    assert seeded_fixture_data.tenant_b_catalog_private.sku not in str(result)


@pytest.mark.asyncio
async def test_mcp_creates_request_then_reads_its_status(
    mcp_call: McpCall, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    created = await mcp_call(
        "create_payment_request",
        {
            "sku": "CLOUD-STARTER",
            "quantity": 1,
            "purpose": "Provision a build environment.",
            "idempotency_key": str(uuid4()),
        },
    )
    status = await mcp_call("get_payment_status", {"payment_id": created["payment_id"]})
    request = await async_session.scalar(
        select(PaymentRequest).where(PaymentRequest.id == created["payment_request_id"])
    )
    assert created["decision"] == "ALLOW"
    assert status["found"] is True
    assert status["state"] == "AUTHORIZED"
    assert request is not None and request.source == "MCP_AGENT"


@pytest.mark.asyncio
async def test_mcp_requests_approval_for_an_approval_required_payment(
    mcp_call: McpCall, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    created = await mcp_call(
        "create_payment_request",
        {
            "sku": "CLOUD-TEAM",
            "quantity": 1,
            "purpose": "Provision a larger build environment.",
            "idempotency_key": str(uuid4()),
        },
    )
    approval = await mcp_call(
        "request_user_approval",
        {"payment_request_id": created["payment_request_id"]},
    )
    approvals = await async_session.scalar(select(func.count()).select_from(Approval))
    assert created["decision"] == "REQUIRE_APPROVAL"
    assert approval == {"ok": True, "status": "PENDING_HUMAN_APPROVAL"}
    assert approvals == 2


@pytest.mark.asyncio
async def test_rejected_mcp_approval_request_writes_an_audit_event(
    mcp_call: McpCall, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    before = await async_session.scalar(select(func.count()).select_from(AuditEvent))
    result = await mcp_call(
        "request_user_approval",
        {"payment_request_id": str(seeded_fixture_data.payment_request.id)},
    )
    after = await async_session.scalar(select(func.count()).select_from(AuditEvent))
    assert result == {"ok": False, "reason": "APPROVAL_NOT_REQUIRED"}
    assert after == before + 1


@pytest.mark.asyncio
async def test_mcp_hides_cross_tenant_payment_and_audits_the_attempt(
    mcp_call: McpCall, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    foreign_request = PaymentRequest(
        tenant_id=seeded_fixture_data.tenant_b.id,
        actor_id=seeded_fixture_data.tenant_b_actor_one,
        merchant_id=seeded_fixture_data.tenant_b_allowed_merchant.id,
        amount_minor=10_000,
        currency="INR",
        order_ref=f"foreign-{uuid4()}",
        idempotency_key=str(uuid4()),
    )
    async_session.add(foreign_request)
    await async_session.flush()
    foreign_payment = Payment(
        tenant_id=seeded_fixture_data.tenant_b.id,
        payment_request_id=foreign_request.id,
        state="CREATED",
        authorized_amount_minor=None,
        captured_amount_minor=0,
        refunded_amount_minor=0,
    )
    async_session.add(foreign_payment)
    await async_session.flush()
    before = await async_session.scalar(select(func.count()).select_from(AuditEvent))
    result = await mcp_call("get_payment_status", {"payment_id": str(foreign_payment.id)})
    after = await async_session.scalar(select(func.count()).select_from(AuditEvent))
    assert result == {"found": False, "reason": "CROSS_TENANT_ACCESS_DENIED"}
    assert after == before + 1
