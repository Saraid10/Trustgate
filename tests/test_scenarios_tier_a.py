"""Tier A adversarial scenarios.

Every scenario proves three things, not one: the attack is rejected with its reason code, no
provider order was created, and no payment gained authority it did not have. The second and third
are properties of what changed, so each scenario snapshots tenant state around the attack.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from fixtures import FixtureData
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.app import app
from api.database import get_session
from mcp_server.server import create_mcp_server
from models.domain import AuditEvent, CatalogItem, Payment, PaymentRequest
from scenarios.report import extract_section, render_section
from scenarios.tier_a import REGISTRY
from scenarios.tier_a.harness import (
    assert_attack_created_nothing,
    assert_attack_gained_no_authority,
    snapshot_tenant,
)

McpCall = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest_asyncio.fixture
async def api_client(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("TRUSTGATE_API_ACTOR_ID", "tier-a-scenario-actor")

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


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


def _headers(data: FixtureData) -> dict[str, str]:
    return {"X-Tenant-Id": str(data.tenant_a.id)}


def _purchase(**overrides: object) -> dict[str, object]:
    return {
        "sku": "CLOUD-STARTER",
        "quantity": 1,
        "purpose": "Provision an isolated build environment.",
        "idempotency_key": str(uuid4()),
        **overrides,
    }


async def _seed_tenant_b_payment(session: AsyncSession, data: FixtureData) -> Payment:
    """Give tenant B a payment for tenant A to attempt to reach.

    Seeded here rather than skipped when absent: a skipped adversarial test would let the
    published matrix claim coverage that never ran.
    """

    request = PaymentRequest(
        id=uuid4(),
        tenant_id=data.tenant_b.id,
        actor_id=data.tenant_b_actor_one,
        merchant_id=data.tenant_b_allowed_merchant.id,
        amount_minor=25_000,
        currency="INR",
        order_ref=f"order-{uuid4()}",
        idempotency_key=str(uuid4()),
    )
    # The composite foreign key to (tenant_id, id) is a table constraint rather than a mapped
    # relationship, so the unit of work cannot order these inserts. The request is flushed first.
    session.add(request)
    await session.flush()
    payment = Payment(
        id=uuid4(),
        tenant_id=data.tenant_b.id,
        payment_request_id=request.id,
        state="CREATED",
        captured_amount_minor=0,
        refunded_amount_minor=0,
    )
    session.add(payment)
    await session.flush()
    return payment


async def _latest_rejection(session: AsyncSession) -> AuditEvent | None:
    return await session.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_kind == "catalog_purchase_rejected")
        .order_by(AuditEvent.created_at.desc())
    )


# --------------------------------------------------------------------------------------
# A1 - Amount tampering
# --------------------------------------------------------------------------------------


async def test_a1_supplied_amount_field_is_refused_at_the_boundary(
    api_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)

    response = await api_client.post(
        "/api/v1/catalog-payment-requests",
        json=_purchase(amount_minor=1, currency="USD"),
        headers=_headers(seeded_fixture_data),
    )

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    assert response.status_code == 422
    assert_attack_created_nothing(before, after)


async def test_a1_mcp_surface_has_no_amount_parameter(mcp_call: McpCall) -> None:
    server = create_mcp_server()
    tools = await server.list_tools()
    money_fields = {"amount", "amount_minor", "price", "price_minor", "currency", "merchant_id"}

    for tool in tools:
        properties = set(tool.inputSchema.get("properties", {}))
        assert not properties & money_fields, f"{tool.name} accepts {properties & money_fields}"


async def test_a1_quantity_cannot_be_used_to_escalate_the_amount(
    api_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Quantity is the only amount lever the agent holds, and it is bounded server-side.

    CLOUD-TEAM is 60,000 minor units with a server-owned maximum of 2. A quantity of 50 would be
    3,000,000 minor units, far past both the per-payment and daily policy limits.
    """

    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)

    response = await api_client.post(
        "/api/v1/catalog-payment-requests",
        json=_purchase(sku="CLOUD-TEAM", quantity=50),
        headers=_headers(seeded_fixture_data),
    )

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    audit = await _latest_rejection(async_session)
    assert response.status_code == 422
    assert response.json()["detail"] == "QUANTITY_EXCEEDS_LIMIT"
    assert audit is not None and audit.payload["reason"] == "QUANTITY_EXCEEDS_LIMIT"
    assert_attack_created_nothing(before, after)


# --------------------------------------------------------------------------------------
# A2 - Merchant substitution
# --------------------------------------------------------------------------------------


async def test_a2_another_tenants_sku_is_not_reachable(
    api_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)

    response = await api_client.post(
        "/api/v1/catalog-payment-requests",
        json=_purchase(sku=seeded_fixture_data.tenant_b_catalog_private.sku),
        headers=_headers(seeded_fixture_data),
    )

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    audit = await _latest_rejection(async_session)
    assert response.status_code == 404
    assert response.json()["detail"] == "CATALOG_ITEM_NOT_AVAILABLE"
    assert audit is not None and audit.payload["reason"] == "CATALOG_ITEM_NOT_AVAILABLE"
    assert_attack_created_nothing(before, after)


async def test_a2_policy_disallowed_merchant_cannot_be_paid(
    api_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Substitution inside the tenant is a policy question, not a tenancy one.

    A catalog item can legitimately reference a merchant the active policy does not allow. The
    request is recorded and denied, but must never gain authority or reach a provider.
    """

    async_session.add(
        CatalogItem(
            id=uuid4(),
            tenant_id=seeded_fixture_data.tenant_a.id,
            merchant_id=seeded_fixture_data.tenant_a_blocked_merchant.id,
            sku="BLOCKED-MERCHANT-ITEM",
            name="Blocked Merchant Item",
            description_untrusted="Synthetic item bound to a merchant outside the active policy.",
            price_minor=10_000,
            currency="INR",
            max_quantity=1,
            active=True,
        )
    )
    await async_session.flush()
    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)

    response = await api_client.post(
        "/api/v1/catalog-payment-requests",
        json=_purchase(sku="BLOCKED-MERCHANT-ITEM"),
        headers=_headers(seeded_fixture_data),
    )

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    body = response.json()
    assert body["decision"] == "DENY"
    assert "MERCHANT_NOT_ALLOWED" in body["reasons"]
    assert_attack_gained_no_authority(before, after)


# --------------------------------------------------------------------------------------
# A11b - Cross-tenant object access
# --------------------------------------------------------------------------------------


async def test_a11b_checkout_authority_route_refuses_another_tenants_request(
    api_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_b.id)

    response = await api_client.post(
        f"/api/v1/checkout-authorities/{seeded_fixture_data.payment_request.id}",
        headers={"X-Tenant-Id": str(seeded_fixture_data.tenant_b.id)},
    )

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_b.id)
    assert response.status_code == 404
    assert response.json()["detail"] == "CHECKOUT_AUTHORITY_NOT_FOUND"
    assert_attack_created_nothing(before, after)


async def test_a11b_razorpay_route_refuses_another_tenants_authority(
    api_client: AsyncClient,
    async_session: AsyncSession,
    seeded_fixture_data: FixtureData,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Credentials are configured so the refusal comes from the tenant check, not a missing key.

    The authority id is one tenant B does not own. The route must refuse to consume it rather
    than resolving it from another tenant.
    """

    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_public")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "test-secret")
    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_b.id)

    response = await api_client.post(
        f"/api/v1/razorpay/checkout-authorities/{uuid4()}/orders",
        headers={"X-Tenant-Id": str(seeded_fixture_data.tenant_b.id)},
    )

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_b.id)
    assert response.status_code == 409
    assert response.json()["detail"] == "CHECKOUT_AUTHORITY_NOT_FOUND"
    assert_attack_created_nothing(before, after)


async def test_a11b_mcp_refuses_another_tenants_payment(
    mcp_call: McpCall, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    tenant_b_payment = await _seed_tenant_b_payment(async_session, seeded_fixture_data)
    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_b.id)

    result = await mcp_call("get_payment_status", {"payment_id": str(tenant_b_payment.id)})

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_b.id)
    assert result["found"] is False
    assert result["reason"] == "CROSS_TENANT_ACCESS_DENIED"
    assert_attack_created_nothing(before, after)


# --------------------------------------------------------------------------------------
# A15 - Unauthorized capture via MCP
# --------------------------------------------------------------------------------------


async def test_a15_mcp_exposes_no_provider_or_authorization_tool() -> None:
    server = create_mcp_server()
    names = {tool.name for tool in await server.list_tools()}
    forbidden = {
        "authorize_payment",
        "capture_payment",
        "call_provider",
        "refund_payment",
        "create_razorpay_order",
        "grant_approval",
    }

    assert not names & forbidden


async def test_a15_every_exposed_mcp_tool_grants_no_payment_authority(
    mcp_call: McpCall, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Prove the property by exercising the surface, not by inspecting tool names.

    A future tool could grant authority without a suspicious name. Every tool the server actually
    exposes is called with hostile arguments; none may advance a payment or reach a provider.
    """

    server = create_mcp_server()
    tool_names = [tool.name for tool in await server.list_tools()]
    hostile: dict[str, dict[str, object]] = {
        "list_catalog": {},
        "create_payment_request": {
            "sku": "CLOUD-TEAM",
            "quantity": 2,
            "purpose": "Escalate to a captured payment.",
            "idempotency_key": str(uuid4()),
        },
        "evaluate_payment_policy": {
            "payment_request_id": str(seeded_fixture_data.payment_request.id)
        },
        "request_user_approval": {
            "payment_request_id": str(seeded_fixture_data.payment_request.id)
        },
        "get_payment_status": {"payment_id": str(seeded_fixture_data.payment.id)},
    }
    assert set(tool_names) == set(hostile), (
        "the MCP surface changed; add the new tool to this scenario before it ships: "
        f"{sorted(set(tool_names) ^ set(hostile))}"
    )
    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)

    for name in tool_names:
        await mcp_call(name, hostile[name])

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    assert_attack_gained_no_authority(before, after)


# --------------------------------------------------------------------------------------
# Registry and published matrix
# --------------------------------------------------------------------------------------


def test_every_registered_scenario_names_tests_that_exist() -> None:
    defined = {
        name
        for name, value in globals().items()
        if name.startswith("test_") and inspect.isfunction(value)
    }
    registered = {name for scenario in REGISTRY for name in scenario.test_names}

    assert registered <= defined, f"registry names missing tests: {sorted(registered - defined)}"


def test_readme_attack_matrix_matches_the_registry() -> None:
    """The published matrix is generated, so it cannot claim uncovered attacks.

    Regenerate with `python -m scenarios.report` and paste between the markers when the registry
    changes.
    """

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    section = extract_section(readme)

    assert section is not None, "README is missing the attack-matrix markers"
    assert section == render_section(), "README attack matrix is stale; regenerate it"
