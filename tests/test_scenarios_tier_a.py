"""Tier A adversarial scenarios.

Every scenario proves three things, not one: the attack is rejected with its reason code, no
provider order was created, and no payment gained authority it did not have. The second and third
are properties of what changed, so each scenario snapshots tenant state around the attack.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from fixtures import FixtureData, assert_route_scan_works, served_routes
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.app import app
from api.database import get_session
from api.routes.checkout_authorities import (
    CheckoutAuthorityUnavailableError,
    _snapshot_hash,
    consume_checkout_authority,
)
from mcp_server.server import create_mcp_server
from models.domain import (
    Approval,
    AuditEvent,
    CatalogItem,
    CheckoutAuthority,
    Payment,
    PaymentRequest,
    RazorpayOrder,
    SpendingPolicy,
)
from scenarios.report import (
    extract_mutation_section,
    extract_section,
    render_mutation_section,
    render_section,
)
from scenarios.tier_a import REGISTRY
from scenarios.tier_a.harness import (
    assert_attack_created_nothing,
    assert_attack_gained_no_authority,
    snapshot_tenant,
)
from state_machine.transitions import (
    LEGAL_TRANSITIONS,
    ApprovalAlreadyConsumedError,
    ApprovalExpiredError,
    IllegalTransitionError,
    RefundExceedsCapturedError,
    transition,
    validate_transition,
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


async def _seed_tenant_a_authority(session: AsyncSession, data: FixtureData) -> CheckoutAuthority:
    """Create a genuinely valid, unconsumed checkout authority owned by tenant A."""

    now = datetime.now(UTC)
    authority = CheckoutAuthority(
        id=uuid4(),
        tenant_id=data.tenant_a.id,
        payment_request_id=data.payment_request.id,
        payment_id=data.payment.id,
        approval_id=None,
        policy_version=data.tenant_a_policy.version,
        snapshot_hash="a" * 64,
        expires_at=now + timedelta(minutes=15),
    )
    session.add(authority)
    await session.flush()
    return authority


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
    rejected_fields = {error["loc"][-1] for error in response.json()["detail"] if error.get("loc")}
    assert response.status_code == 422
    # Pin which fields were refused. A status-code-only assertion would be satisfied by any
    # unrelated validation failure introduced later.
    assert {"amount_minor", "currency"} <= rejected_fields
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
    """Tenant B attempts to consume a real, valid, unconsumed authority owned by tenant A.

    An unknown identifier would only prove that unknown identifiers are refused. The authority
    here genuinely exists and is genuinely consumable by its owner, so the refusal can only come
    from the tenant filter. Both tenants are snapshotted: tenant B must gain nothing, and tenant
    A's authority must remain unconsumed and unspent.
    """

    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_public")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "test-secret")
    authority = await _seed_tenant_a_authority(async_session, seeded_fixture_data)
    victim_before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    attacker_before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_b.id)

    response = await api_client.post(
        f"/api/v1/razorpay/checkout-authorities/{authority.id}/orders",
        headers={"X-Tenant-Id": str(seeded_fixture_data.tenant_b.id)},
    )

    victim_after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    attacker_after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_b.id)
    await async_session.refresh(authority)
    owner_response = await api_client.post(
        f"/api/v1/razorpay/checkout-authorities/{authority.id}/orders",
        headers=_headers(seeded_fixture_data),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "CHECKOUT_AUTHORITY_NOT_FOUND"
    # The owner is refused for a different reason, which is what makes the tenant filter the only
    # possible cause of tenant B's refusal. A shared precondition failure would refuse both alike.
    assert owner_response.json()["detail"] != "CHECKOUT_AUTHORITY_NOT_FOUND"
    assert authority.used_at is None
    assert_attack_created_nothing(attacker_before, attacker_after)
    assert_attack_created_nothing(victim_before, victim_after)


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


def test_readme_mutation_table_matches_the_mutation_registry() -> None:
    """The published mutation table is generated for the same reason the attack matrix is.

    A hand-written list of guarded invariants decays into a list of invariants the project used to
    guard, and that decay is invisible: the tests still pass, the table still reads well, and the
    claim quietly stops being true. Regenerate with `python -m scenarios.report --mutations`.
    """

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    section = extract_mutation_section(readme)

    assert section is not None, "README is missing the mutation-table markers"
    assert section == render_mutation_section(), "README mutation table is stale; regenerate it"


async def test_a5_an_approval_cannot_be_granted_by_the_requesting_actor(
    api_client: AsyncClient,
    async_session: AsyncSession,
    seeded_fixture_data: FixtureData,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Separation of duties must not rest on configuration hygiene alone.

    Holding a separate approver token is what normally keeps the requester and the approver apart.
    If the configured approver identity is ever the requesting actor, an approval recorded as
    independent review would assert oversight that never happened, which is worse than no approval
    because the evidence would claim a control that was not exercised.
    """

    monkeypatch.setenv("DEMO_APPROVER_TOKEN", "tier-a-approver-token")
    monkeypatch.setenv("DEMO_APPROVER_ID", "tier-a-scenario-actor")

    created = await api_client.post(
        "/api/v1/catalog-payment-requests",
        json=_purchase(sku="CLOUD-TEAM"),
        headers=_headers(seeded_fixture_data),
    )
    assert created.json()["decision"] == "REQUIRE_APPROVAL"
    request_id = created.json()["payment_request_id"]
    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)

    response = await api_client.post(
        f"/api/v1/approvals/{request_id}/grant",
        headers={**_headers(seeded_fixture_data), "X-Approver-Token": "tier-a-approver-token"},
    )

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    assert response.status_code == 403
    assert response.json()["detail"] == "APPROVER_IS_REQUESTER"
    assert_attack_gained_no_authority(before, after)


async def test_a5_a_separate_approver_can_still_grant(
    api_client: AsyncClient, seeded_fixture_data: FixtureData, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard must refuse self-approval without blocking genuine review."""

    monkeypatch.setenv("DEMO_APPROVER_TOKEN", "tier-a-approver-token")
    monkeypatch.setenv("DEMO_APPROVER_ID", "an-independent-approver")

    created = await api_client.post(
        "/api/v1/catalog-payment-requests",
        json=_purchase(sku="CLOUD-TEAM"),
        headers=_headers(seeded_fixture_data),
    )
    request_id = created.json()["payment_request_id"]

    response = await api_client.post(
        f"/api/v1/approvals/{request_id}/grant",
        headers={**_headers(seeded_fixture_data), "X-Approver-Token": "tier-a-approver-token"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["payment_request_id"] == request_id


# --------------------------------------------------------------------------------------
# A3 - Currency substitution
# --------------------------------------------------------------------------------------


async def test_a3_the_agent_surface_derives_currency_and_cannot_be_told_one() -> None:
    """The strongest form of this defence is that the field does not exist.

    A validated currency parameter would still be a parameter untrusted text could aim at. Deriving
    currency from the catalog item leaves nothing to validate, and the assertion is made against
    the schema every tool actually publishes rather than against the ones this test remembered.
    """

    server = create_mcp_server()
    tools = await server.list_tools()

    assert tools, "no MCP tools were exposed, so this proves nothing"
    for tool in tools:
        properties = set(tool.inputSchema.get("properties", {}))
        assert "currency" not in properties, f"{tool.name} accepts a caller-supplied currency"


async def test_a3_the_only_currency_accepting_route_is_disabled_by_default(
    api_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The legacy operator route is the one surface that takes a currency, and it is off.

    A field that cannot be reached is a field that cannot be attacked. This is asserted rather than
    assumed because the flag is the whole reason the surface is safe by default, and a change to
    its default would otherwise be invisible.
    """

    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)

    response = await api_client.post(
        "/api/v1/payment-requests",
        json={
            "actor_id": seeded_fixture_data.tenant_a_actor_one,
            "merchant_id": str(seeded_fixture_data.tenant_a_allowed_merchant.id),
            "amount_minor": 1_000,
            "currency": "USD",
            "order_ref": f"order-{uuid4()}",
            "idempotency_key": str(uuid4()),
        },
        headers=_headers(seeded_fixture_data),
    )

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    assert response.status_code == 404, response.text
    assert_attack_created_nothing(before, after)


async def test_a3_an_enabled_legacy_route_still_denies_a_currency_outside_the_policy(
    api_client: AsyncClient,
    async_session: AsyncSession,
    seeded_fixture_data: FixtureData,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Turning the surface on must not turn the control off.

    An operator who enables the legacy route for a real integration should not thereby acquire a
    currency-substitution hole, so the flag is switched on here deliberately and the policy is
    still what settles the currency. A mismatch is denied rather than converted: a system that
    silently reinterprets a currency has decided an amount on the payer's behalf.
    """

    monkeypatch.setenv("ENABLE_LEGACY_PAYMENT_REQUEST_API", "true")
    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)

    response = await api_client.post(
        "/api/v1/payment-requests",
        json={
            "actor_id": seeded_fixture_data.tenant_a_actor_one,
            "merchant_id": str(seeded_fixture_data.tenant_a_allowed_merchant.id),
            "amount_minor": 1_000,
            "currency": "USD",
            "order_ref": f"order-{uuid4()}",
            "idempotency_key": str(uuid4()),
        },
        headers=_headers(seeded_fixture_data),
    )

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["decision"] == "DENY"
    assert "CURRENCY_NOT_ALLOWED" in body["reasons"]
    assert_attack_gained_no_authority(before, after)


# --------------------------------------------------------------------------------------
# A11a - Unknown tenant header
# --------------------------------------------------------------------------------------


async def test_a11a_an_unknown_tenant_header_is_refused(
    api_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """A well-formed tenant id that resolves to nothing is refused before any route body runs."""

    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)

    response = await api_client.post(
        "/api/v1/catalog-payment-requests",
        json=_purchase(),
        headers={"X-Tenant-Id": str(uuid4())},
    )

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    assert response.status_code == 403
    assert_attack_created_nothing(before, after)


async def test_a11a_an_unknown_tenant_is_indistinguishable_from_a_forbidden_one() -> None:
    """Refusal must not become an oracle for which tenant ids exist.

    A distinct 404 for "no such tenant" against 403 for "not yours" would let anyone holding the
    endpoint enumerate real tenant identifiers one request at a time. Both answers are the same
    status and the same body, so a refusal carries no information beyond the refusal.
    """

    unknown = str(uuid4())
    malformed = "not-a-uuid"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unknown_response = await client.post(
            "/api/v1/catalog-payment-requests", json=_purchase(), headers={"X-Tenant-Id": unknown}
        )
        malformed_response = await client.post(
            "/api/v1/catalog-payment-requests", json=_purchase(), headers={"X-Tenant-Id": malformed}
        )

    assert unknown_response.status_code == 403
    assert unknown_response.json()["detail"] == "unknown tenant"
    # A malformed header is refused by validation before resolution, which is a different code path
    # reaching the same outcome: no tenant, no work done, nothing disclosed about what exists.
    assert malformed_response.status_code == 422
    assert unknown not in malformed_response.text


# --------------------------------------------------------------------------------------
# A12 - Idempotency key collision
# --------------------------------------------------------------------------------------


async def test_a12_a_reused_key_with_a_different_purchase_returns_the_first_decision(
    api_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """A repeated key must never authorize the second purchase it was attached to.

    The dangerous reading of an idempotency key is "this request already succeeded, return that".
    Applied to a *different* payload that turns a retry into an authorization the payer never
    made: send a small purchase, then resend the key carrying a larger one. The server answers with
    the original decision and a 409, so the caller cannot mistake it for acceptance of what it just
    sent, and the second purchase never exists.
    """

    key = str(uuid4())
    first = await api_client.post(
        "/api/v1/catalog-payment-requests",
        json=_purchase(sku="CLOUD-STARTER", quantity=1, idempotency_key=key),
        headers=_headers(seeded_fixture_data),
    )
    assert first.status_code == 201
    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)

    second = await api_client.post(
        "/api/v1/catalog-payment-requests",
        json=_purchase(sku="CLOUD-TEAM", quantity=2, idempotency_key=key),
        headers=_headers(seeded_fixture_data),
    )

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    collision = await async_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_kind == "idempotency_key_collision")
        .order_by(AuditEvent.created_at.desc())
    )

    assert second.status_code == 409
    assert second.json()["payment_request_id"] == first.json()["payment_request_id"]
    assert collision is not None
    assert collision.payload["reason"] == "IDEMPOTENCY_KEY_REPLAYED"
    # The replayed key created no second request, and nothing gained authority from the collision.
    assert after.payment_request_ids == before.payment_request_ids
    assert_attack_gained_no_authority(before, after)


# --------------------------------------------------------------------------------------
# A4 - Expired approval reuse
# --------------------------------------------------------------------------------------


async def test_a4_an_expired_approval_cannot_authorize(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Human approval is a permission with a lifetime, not a permanent grant.

    An approval that outlives its window is the most reusable credential in the system: it was
    genuinely granted by a real approver, so nothing about it looks forged. The expiry is what
    stops a purchase being authorized on the strength of a decision someone made last month about
    circumstances that no longer hold.

    The seeded approval is aged rather than a fresh one inserted, because a partial unique index
    permits only one unconsumed approval per payment request. That index is itself part of the
    defence, so working within it keeps the scenario honest about the real schema.
    """

    payment = seeded_fixture_data.payment
    payment.state = "APPROVAL_REQUIRED"
    approval = seeded_fixture_data.unconsumed_approval
    approval.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await async_session.flush()
    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)

    with pytest.raises(ApprovalExpiredError):
        await transition(
            async_session,
            payment,
            "AUTHORIZED",
            reason="a4-expired-approval",
            correlation_id=uuid4(),
            approval_id=approval.id,
        )

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    consumed_at = await async_session.scalar(
        select(Approval.consumed_at).where(Approval.id == approval.id)
    )
    assert consumed_at is None, (
        "a refused approval was consumed anyway, which would hide the reuse from the next attempt"
    )
    assert_attack_gained_no_authority(before, after)


async def test_a4_an_already_consumed_approval_cannot_authorize_again(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Single use is enforced by the stored consumption mark, not by the caller's discipline.

    The approval here is unexpired and was genuinely granted. Only the fact that it has already
    been spent stands between it and a second authorization.
    """

    payment = seeded_fixture_data.payment
    payment.state = "APPROVAL_REQUIRED"
    approval = seeded_fixture_data.consumed_approval
    approval.expires_at = datetime.now(UTC) + timedelta(minutes=15)
    await async_session.flush()
    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)

    with pytest.raises(ApprovalAlreadyConsumedError):
        await transition(
            async_session,
            payment,
            "AUTHORIZED",
            reason="a4-replayed-approval",
            correlation_id=uuid4(),
            approval_id=approval.id,
        )

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    assert_attack_gained_no_authority(before, after)


# --------------------------------------------------------------------------------------
# A9 - Out-of-order provider events
# --------------------------------------------------------------------------------------


async def test_a9_a_capture_cannot_precede_an_authorization(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Arrival order is the provider's; legality is ours.

    Webhooks are not ordered. A capture that arrives before the authorization it belongs to must be
    refused rather than applied, because accepting it would let a payment reach CAPTURED without
    ever passing through the state where authority was checked.
    """

    payment = seeded_fixture_data.payment
    payment.state = "CREATED"
    await async_session.flush()
    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)

    with pytest.raises(IllegalTransitionError):
        await transition(
            async_session,
            payment,
            "CAPTURED",
            reason="a9-out-of-order-capture",
            correlation_id=uuid4(),
        )

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    assert_attack_gained_no_authority(before, after)


async def test_a9_a_terminal_payment_accepts_no_further_provider_outcome(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """A late event about a settled payment cannot revive it.

    DENIED is terminal by construction: its legal successor set is empty, so a provider event
    arriving afterwards has nowhere to move the payment. This is a property of the transition table
    rather than of any handler, which is why the table is asserted alongside the refusal.
    """

    payment = seeded_fixture_data.payment
    payment.state = "DENIED"
    await async_session.flush()
    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)

    assert LEGAL_TRANSITIONS["DENIED"] == frozenset()
    with pytest.raises(IllegalTransitionError):
        await transition(
            async_session,
            payment,
            "AUTHORIZED",
            reason="a9-late-event-on-terminal-payment",
            correlation_id=uuid4(),
        )

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    assert_attack_gained_no_authority(before, after)


# --------------------------------------------------------------------------------------
# A10 - Double refund
# --------------------------------------------------------------------------------------


async def test_a10_no_surface_anywhere_can_initiate_a_refund() -> None:
    """The refund story starts with the fact that nothing can start one.

    This project authorizes purchases; it never moves money back. Rather than trusting that no
    route was written, the assertion is made against the app's own route table and the MCP tool
    list, so adding a refund endpoint later fails here instead of quietly widening what an agent
    can reach.
    """

    server = create_mcp_server()
    tools = await server.list_tools()
    assert tools, "no MCP tools were exposed, so this proves nothing"
    assert not {tool.name for tool in tools if "refund" in tool.name.lower()}

    # The scan is verified before it is trusted. This assertion previously ran against a walk that
    # could not see any application route, so "no refund route exists" was true of a search that
    # examined nothing - a registered Tier A claim resting on an empty list.
    routes = served_routes(app)
    assert_route_scan_works(routes)

    refund_routes = [
        (sorted(methods), path)
        for methods, path in routes
        if "refund" in path.lower() and set(methods) - {"GET", "HEAD", "OPTIONS"}
    ]
    assert not refund_routes, f"a refund-initiating route now exists: {refund_routes}"


async def test_a10_a_refund_total_cannot_exceed_what_was_captured(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The ledger invariant holds even if a refund path is added later.

    The guard lives in `validate_transition`, so it applies to every route that could ever move a
    payment rather than to the one that happens to exist. A second refund of an already fully
    refunded capture is the concrete case: both refunds are individually plausible and only their
    total is wrong.
    """

    payment = seeded_fixture_data.payment
    payment.state = "CAPTURED"
    payment.authorized_amount_minor = 50_000
    payment.captured_amount_minor = 50_000
    payment.refunded_amount_minor = 50_000
    await async_session.flush()

    # A second refund of the same capture pushes the running total past it.
    payment.refunded_amount_minor = 100_000

    with pytest.raises(RefundExceedsCapturedError):
        validate_transition(payment, "REFUNDED")


# --------------------------------------------------------------------------------------
# A6, A7, A8, A14 - Provider webhook authenticity, integrity, replay, and freshness
# --------------------------------------------------------------------------------------

# Synthetic. The linter rule that flags assigned secrets exists to catch real credentials, so it is
# suppressed on the line rather than for the file.
SCENARIO_WEBHOOK_SECRET = "tier-a-webhook-secret"  # noqa: S105
SCENARIO_ORDER_AMOUNT = 39_900


@pytest_asyncio.fixture
async def webhook_client(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", SCENARIO_WEBHOOK_SECRET)
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_public")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "test-secret")

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def provider_order(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> RazorpayOrder:
    """An authorized payment with a confirmed provider order, as the order route would leave it."""

    request = PaymentRequest(
        id=uuid4(),
        tenant_id=seeded_fixture_data.tenant_a.id,
        actor_id=seeded_fixture_data.tenant_a_actor_one,
        merchant_id=seeded_fixture_data.tenant_a_allowed_merchant.id,
        amount_minor=SCENARIO_ORDER_AMOUNT,
        currency="INR",
        order_ref=f"order-{uuid4()}",
        idempotency_key=str(uuid4()),
    )
    async_session.add(request)
    await async_session.flush()
    payment = Payment(
        id=uuid4(),
        tenant_id=seeded_fixture_data.tenant_a.id,
        payment_request_id=request.id,
        state="AUTHORIZED",
        authorized_amount_minor=SCENARIO_ORDER_AMOUNT,
        captured_amount_minor=0,
        refunded_amount_minor=0,
    )
    async_session.add(payment)
    await async_session.flush()
    authority = CheckoutAuthority(
        id=uuid4(),
        tenant_id=seeded_fixture_data.tenant_a.id,
        payment_request_id=request.id,
        payment_id=payment.id,
        approval_id=None,
        policy_version=seeded_fixture_data.tenant_a_policy.version,
        snapshot_hash="c" * 64,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
        # Postgres stamps created_at from its own clock, so used_at must come from the same one.
        # A host-clock timestamp here fails `used_at >= created_at` whenever the container clock
        # drifts ahead, which under Docker Desktop it does.
        used_at=func.now(),
    )
    async_session.add(authority)
    await async_session.flush()
    order = RazorpayOrder(
        id=uuid4(),
        tenant_id=seeded_fixture_data.tenant_a.id,
        checkout_authority_id=authority.id,
        payment_id=payment.id,
        razorpay_order_id=f"order_{uuid4().hex[:14]}",
        provider_state="CONFIRMED",
        receipt=f"tg_{authority.id.hex}",
        amount_minor=SCENARIO_ORDER_AMOUNT,
        currency="INR",
    )
    async_session.add(order)
    await async_session.flush()
    return order


def _event_body(
    order_id: str,
    *,
    event: str = "payment.captured",
    payment_id: str | None = None,
    created_at: int | None = None,
) -> bytes:
    return json.dumps(
        {
            "entity": "event",
            "event": event,
            "created_at": int(datetime.now(UTC).timestamp()) if created_at is None else created_at,
            "contains": ["payment"],
            "payload": {
                "payment": {
                    "entity": {
                        "id": payment_id or f"pay_{uuid4().hex[:14]}",
                        "order_id": order_id,
                        "amount": SCENARIO_ORDER_AMOUNT,
                        "currency": "INR",
                        "status": "captured",
                    }
                }
            },
        },
        separators=(",", ":"),
    ).encode()


def _sign(body: bytes) -> str:
    return hmac.new(SCENARIO_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _webhook_headers(body: bytes, *, signature: str | None = None) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Razorpay-Signature": _sign(body) if signature is None else signature,
        "X-Razorpay-Event-Id": f"evt_{uuid4().hex[:16]}",
    }


async def test_a6_a_forged_signature_is_refused(
    webhook_client: AsyncClient,
    async_session: AsyncSession,
    seeded_fixture_data: FixtureData,
    provider_order: RazorpayOrder,
) -> None:
    """A webhook is an instruction from outside, so authenticity is checked before anything else.

    The body here is entirely well-formed and names a real order. Only the signature is wrong,
    which is exactly the position an attacker who has learned an order id but not the signing
    secret is in.
    """

    body = _event_body(provider_order.razorpay_order_id)
    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)

    response = await webhook_client.post(
        "/api/v1/razorpay/webhook",
        content=body,
        headers=_webhook_headers(body, signature="0" * 64),
    )

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    assert response.status_code == 400
    assert response.json()["detail"] == "RAZORPAY_WEBHOOK_SIGNATURE_INVALID"
    assert after.payment_states == before.payment_states


async def test_a6_an_unsigned_event_is_refused(
    webhook_client: AsyncClient,
    async_session: AsyncSession,
    seeded_fixture_data: FixtureData,
    provider_order: RazorpayOrder,
) -> None:
    """A missing signature is refused rather than treated as nothing to verify."""

    body = _event_body(provider_order.razorpay_order_id)
    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)

    response = await webhook_client.post(
        "/api/v1/razorpay/webhook",
        content=body,
        headers={"Content-Type": "application/json"},
    )

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    assert response.status_code == 400
    assert response.json()["detail"] == "RAZORPAY_WEBHOOK_SIGNATURE_INVALID"
    assert after.payment_states == before.payment_states


async def test_a7_a_body_altered_after_signing_no_longer_verifies(
    webhook_client: AsyncClient,
    async_session: AsyncSession,
    seeded_fixture_data: FixtureData,
    provider_order: RazorpayOrder,
) -> None:
    """The signature covers the exact bytes, so tampering is detected rather than tolerated.

    This is the interesting half of A6: the attacker holds a genuinely signed event and changes one
    field in it. Raising the reported amount is the profitable edit, and it fails because the
    signature was computed over the original bytes and the server verifies before it parses.
    """

    original = _event_body(provider_order.razorpay_order_id)
    signature = _sign(original)
    tampered = original.replace(
        f'"amount":{SCENARIO_ORDER_AMOUNT}'.encode(),
        b'"amount":1',
    )
    assert tampered != original, "the tamper did not change the body, so this proves nothing"
    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)

    response = await webhook_client.post(
        "/api/v1/razorpay/webhook",
        content=tampered,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Signature": signature,
            "X-Razorpay-Event-Id": f"evt_{uuid4().hex[:16]}",
        },
    )

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    assert response.status_code == 400
    assert response.json()["detail"] == "RAZORPAY_WEBHOOK_SIGNATURE_INVALID"
    assert after.payment_states == before.payment_states


async def test_a8_a_replayed_event_does_not_transition_the_payment_twice(
    webhook_client: AsyncClient,
    async_session: AsyncSession,
    seeded_fixture_data: FixtureData,
    provider_order: RazorpayOrder,
) -> None:
    """A duplicate delivery is authentic, in window, and must still change nothing.

    Providers retry, and networks duplicate. The same signed bytes with the same event id are
    replayed here, so nothing distinguishes the second delivery from the first except that it has
    already been applied. Identity is stored, so the second is refused by the database rather than
    by whichever handler happens to look.
    """

    # payment.authorized, because the payment is AUTHORIZED and a capture from there is refused as
    # out of order by A9. Replay is the property under test, so the first delivery has to succeed.
    body = _event_body(provider_order.razorpay_order_id, event="payment.authorized")
    headers = _webhook_headers(body)

    first = await webhook_client.post("/api/v1/razorpay/webhook", content=body, headers=headers)
    assert first.status_code == 202, first.text
    after_first = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)

    second = await webhook_client.post("/api/v1/razorpay/webhook", content=body, headers=headers)

    after_second = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    transitions = await async_session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(
            AuditEvent.tenant_id == seeded_fixture_data.tenant_a.id,
            AuditEvent.event_kind == "payment_transition",
            AuditEvent.payment_id == provider_order.payment_id,
        )
    )

    assert second.status_code == 409, second.text
    assert second.json()["detail"] == "RAZORPAY_WEBHOOK_DUPLICATE_EVENT"
    assert after_second.payment_states == after_first.payment_states
    assert transitions == 1, f"the replay produced {transitions} transitions"


async def test_a14_a_stale_signed_event_is_refused(
    webhook_client: AsyncClient,
    async_session: AsyncSession,
    seeded_fixture_data: FixtureData,
    provider_order: RazorpayOrder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid signature proves origin, not recency.

    Without a bound, a captured event is a permanent credential: anything able to replay bytes from
    a log or an old environment could apply them at any future moment. The window is configured
    tightly here so the test does not depend on the shipped default, and the event is otherwise
    perfect - correctly signed, real order, fresh event id.
    """

    monkeypatch.setenv("RAZORPAY_WEBHOOK_MAX_AGE_SECONDS", "60")
    stale = int((datetime.now(UTC) - timedelta(hours=2)).timestamp())
    body = _event_body(provider_order.razorpay_order_id, created_at=stale)
    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)

    response = await webhook_client.post(
        "/api/v1/razorpay/webhook", content=body, headers=_webhook_headers(body)
    )

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    assert response.status_code == 400
    assert response.json()["detail"] == "RAZORPAY_WEBHOOK_STALE"
    assert after.payment_states == before.payment_states


async def test_a14_a_post_dated_event_cannot_extend_its_own_validity(
    webhook_client: AsyncClient,
    async_session: AsyncSession,
    seeded_fixture_data: FixtureData,
    provider_order: RazorpayOrder,
) -> None:
    """Stamping an event into the future would defeat the age bound, so it is refused.

    Backward tolerance has to be generous because providers retry. Forward tolerance does not:
    clock skew explains minutes, and nothing legitimate explains an event dated hours ahead. Only
    the timestamp is unusual here, and it is inside the signed body, so this is the shape a
    tamper-then-resign attempt would take if the secret ever leaked.
    """

    post_dated = int((datetime.now(UTC) + timedelta(hours=6)).timestamp())
    body = _event_body(provider_order.razorpay_order_id, created_at=post_dated)
    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)

    response = await webhook_client.post(
        "/api/v1/razorpay/webhook", content=body, headers=_webhook_headers(body)
    )

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    assert response.status_code == 400
    assert response.json()["detail"] == "RAZORPAY_WEBHOOK_POST_DATED"
    assert after.payment_states == before.payment_states


async def test_a14_an_event_with_no_timestamp_is_refused_rather_than_exempted(
    webhook_client: AsyncClient,
    async_session: AsyncSession,
    seeded_fixture_data: FixtureData,
    provider_order: RazorpayOrder,
) -> None:
    """An event that cannot be dated cannot be bounded, so it fails closed.

    Treating a missing timestamp as "nothing to check" would hand every replay a trivial bypass:
    drop the field and the window disappears. It carries its own reason code so an operator can
    tell a provider payload change apart from an attack.
    """

    body = json.dumps(
        {
            "entity": "event",
            "event": "payment.captured",
            "contains": ["payment"],
            "payload": {
                "payment": {
                    "entity": {
                        "id": f"pay_{uuid4().hex[:14]}",
                        "order_id": provider_order.razorpay_order_id,
                        "amount": SCENARIO_ORDER_AMOUNT,
                        "currency": "INR",
                        "status": "captured",
                    }
                }
            },
        },
        separators=(",", ":"),
    ).encode()
    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)

    response = await webhook_client.post(
        "/api/v1/razorpay/webhook", content=body, headers=_webhook_headers(body)
    )

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    assert response.status_code == 400
    assert response.json()["detail"] == "RAZORPAY_WEBHOOK_TIMESTAMP_MISSING"
    assert after.payment_states == before.payment_states


# --------------------------------------------------------------------------------------
# A13 - TOCTOU policy change between authorization and use
# --------------------------------------------------------------------------------------


async def _authority_bound_to_current_policy(
    session: AsyncSession, data: FixtureData
) -> tuple[CheckoutAuthority, Payment]:
    """An authority that would genuinely succeed if nothing drifted.

    Built against the live policy version and the real snapshot hash so that the only thing a
    scenario changes afterwards is the one fact under test. An authority that was invalid to begin
    with would pass every rejection assertion while proving nothing.
    """

    request = PaymentRequest(
        id=uuid4(),
        tenant_id=data.tenant_a.id,
        actor_id=data.tenant_a_actor_one,
        merchant_id=data.tenant_a_allowed_merchant.id,
        amount_minor=25_000,
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
        authorized_amount_minor=25_000,
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
        snapshot_hash=_snapshot_hash(request, data.tenant_a_policy.version),
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    session.add(authority)
    await session.flush()
    return authority, payment


async def test_a13_an_authority_is_valid_until_the_policy_under_it_moves(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The control group: with nothing drifting, this exact authority is consumable.

    Registered as part of A13 rather than kept as a local sanity check, because the rejection tests
    below are only meaningful if the thing they reject would otherwise have worked.
    """

    authority, _ = await _authority_bound_to_current_policy(async_session, seeded_fixture_data)

    consumed = await consume_checkout_authority(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        checkout_authority_id=authority.id,
        correlation_id=uuid4(),
    )

    assert consumed.used_at is not None


async def test_a13_a_policy_published_after_authorization_revokes_the_authority(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Authorization is a decision about a policy, and it does not outlive that policy.

    The window between deciding and spending is where this bites. An operator tightens a limit, or
    removes a merchant, in the fifteen minutes an authority is live. The authority was honestly
    issued and has not expired or been used, so nothing about it looks stale - except that the
    rules it was checked against are no longer the rules in force.

    Rejecting on version rather than on re-derived limits is deliberate: it fails closed for any
    change, including ones nobody thought to compare.
    """

    authority, _ = await _authority_bound_to_current_policy(async_session, seeded_fixture_data)
    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)

    superseding = SpendingPolicy(
        id=uuid4(),
        tenant_id=seeded_fixture_data.tenant_a.id,
        version=seeded_fixture_data.tenant_a_policy.version + 1,
        max_amount_minor=seeded_fixture_data.tenant_a_policy.max_amount_minor,
        currency=seeded_fixture_data.tenant_a_policy.currency,
        max_daily_spend_minor=seeded_fixture_data.tenant_a_policy.max_daily_spend_minor,
        approval_required_above_minor=(
            seeded_fixture_data.tenant_a_policy.approval_required_above_minor
        ),
        expiry=datetime.now(UTC) + timedelta(days=30),
    )
    async_session.add(superseding)
    await async_session.flush()

    with pytest.raises(CheckoutAuthorityUnavailableError) as raised:
        await consume_checkout_authority(
            async_session,
            tenant_id=seeded_fixture_data.tenant_a.id,
            checkout_authority_id=authority.id,
            correlation_id=uuid4(),
        )

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    used_at = await async_session.scalar(
        select(CheckoutAuthority.used_at).where(CheckoutAuthority.id == authority.id)
    )
    assert raised.value.reason == "CHECKOUT_AUTHORITY_POLICY_DRIFT"
    assert used_at is None, "a refused authority was marked used, silently burning it"
    assert after.consumed_authority_ids == before.consumed_authority_ids
    assert_attack_created_nothing(before, after)


async def test_a13_an_amount_edited_after_authorization_breaks_the_snapshot_hash(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The second drift vector: the policy holds still and the purchase changes underneath it.

    A version check alone would not see this, because the policy never moved. The authority is
    bound to a hash of the exact purchase it was issued for, so editing the amount afterwards
    invalidates it without anyone needing to enumerate which fields matter.
    """

    authority, payment = await _authority_bound_to_current_policy(
        async_session, seeded_fixture_data
    )
    request = await async_session.scalar(
        select(PaymentRequest).where(PaymentRequest.id == authority.payment_request_id)
    )
    assert request is not None
    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)

    request.amount_minor = 250_000
    await async_session.flush()

    with pytest.raises(CheckoutAuthorityUnavailableError) as raised:
        await consume_checkout_authority(
            async_session,
            tenant_id=seeded_fixture_data.tenant_a.id,
            checkout_authority_id=authority.id,
            correlation_id=uuid4(),
        )

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    assert raised.value.reason == "CHECKOUT_AUTHORITY_POLICY_DRIFT"
    assert after.consumed_authority_ids == before.consumed_authority_ids
