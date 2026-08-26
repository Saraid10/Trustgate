"""The console must show everything and be able to change nothing."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from fixtures import FixtureData
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.app import app
from api.console_view import ConsoleEntry, render_console
from api.database import get_session
from models.domain import (
    AuditEvent,
    AuthorizationDecision,
    CheckoutAuthority,
    Payment,
    PaymentRequest,
    RazorpayOrder,
)


@pytest_asyncio.fixture
async def console_client(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("ENABLE_CONSOLE", "true")

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def _attempt(
    session: AsyncSession,
    data: FixtureData,
    *,
    sku: str,
    amount_minor: int,
    decision: str,
    reasons: list[str],
    with_provider_order: bool,
) -> PaymentRequest:
    """One complete purchase attempt, shaped the way a real flow would leave it.

    The catalog snapshot is written in full because a CHECK constraint requires all of it or none
    of it: a half-populated snapshot is exactly the state that would let a receipt name a SKU whose
    price nobody can now look up.
    """

    item = data.tenant_a_catalog_team if sku == "CLOUD-TEAM" else data.tenant_a_catalog_starter
    request = PaymentRequest(
        id=uuid4(),
        tenant_id=data.tenant_a.id,
        actor_id=data.tenant_a_actor_one,
        merchant_id=data.tenant_a_allowed_merchant.id,
        catalog_item_id=item.id,
        catalog_sku=sku,
        catalog_name=f"{sku} plan",
        merchant_display_name=data.tenant_a_allowed_merchant.name,
        quantity=1,
        purpose="Provision an isolated build environment.",
        source="MCP_AGENT",
        amount_minor=amount_minor,
        currency="INR",
        order_ref=f"order-{uuid4()}",
        idempotency_key=str(uuid4()),
    )
    session.add(request)
    await session.flush()
    session.add(
        AuthorizationDecision(
            id=uuid4(),
            tenant_id=data.tenant_a.id,
            payment_request_id=request.id,
            decision=decision,
            reasons=reasons,
            policy_version=data.tenant_a_policy.version,
            correlation_id=uuid4(),
        )
    )
    payment = Payment(
        id=uuid4(),
        tenant_id=data.tenant_a.id,
        payment_request_id=request.id,
        state="CAPTURED" if with_provider_order else "DENIED",
        authorized_amount_minor=amount_minor if with_provider_order else None,
        captured_amount_minor=amount_minor if with_provider_order else 0,
        refunded_amount_minor=0,
    )
    session.add(payment)
    await session.flush()
    if with_provider_order:
        authority = CheckoutAuthority(
            id=uuid4(),
            tenant_id=data.tenant_a.id,
            payment_request_id=request.id,
            payment_id=payment.id,
            approval_id=None,
            policy_version=data.tenant_a_policy.version,
            snapshot_hash="d" * 64,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
            used_at=datetime.now(UTC),
        )
        session.add(authority)
        await session.flush()
        session.add(
            RazorpayOrder(
                id=uuid4(),
                tenant_id=data.tenant_a.id,
                checkout_authority_id=authority.id,
                payment_id=payment.id,
                razorpay_order_id=f"order_{uuid4().hex[:14]}",
                provider_state="CONFIRMED",
                receipt=f"tg_{authority.id.hex}",
                amount_minor=amount_minor,
                currency="INR",
            )
        )
    await session.flush()
    return request


async def test_the_console_is_unreachable_unless_it_is_switched_on(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """A demo surface that ships reachable is one somebody deploys by accident.

    No `ENABLE_CONSOLE` is set here, and the refusal is a 404 rather than a 403 so that a
    deployment which never enabled it does not advertise that the route exists.
    """

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/console/{seeded_fixture_data.tenant_a.id}")
    app.dependency_overrides.clear()

    assert response.status_code == 404


async def test_the_console_offers_no_way_to_change_anything() -> None:
    """The console is a window, not a control panel.

    An approve or authorize button here would make it a new authority surface, and the project's
    claim that authority is reachable only through the checked paths would need an asterisk. This
    asserts against the app's live route table rather than against the routes this test remembers,
    so adding a state-changing console route fails here.
    """

    changing = [
        (sorted(getattr(route, "methods", set()) or set()), path)
        for route in _walk(app)
        if (path := str(getattr(route, "path", ""))).startswith("/console")
        and (set(getattr(route, "methods", set()) or set()) - {"GET", "HEAD", "OPTIONS"})
    ]

    assert not changing, f"the console gained a state-changing route: {changing}"


def _walk(application: object) -> list[object]:
    found: list[object] = []
    pending: list[object] = list(getattr(application, "routes", []))
    while pending:
        route = pending.pop()
        nested = getattr(route, "routes", None)
        if nested:
            pending.extend(nested)
            continue
        found.append(route)
    return found


async def test_an_unknown_tenant_is_refused_without_saying_which_ones_exist(
    console_client: AsyncClient,
) -> None:
    response = await console_client.get(f"/console/{uuid4()}")

    assert response.status_code == 403
    assert response.json()["detail"] == "unknown tenant"


async def test_the_timeline_shows_only_the_named_tenants_attempts(
    console_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Cross-tenant isolation holds on the console exactly as it does on the API."""

    mine = await _attempt(
        async_session,
        seeded_fixture_data,
        sku="CLOUD-STARTER",
        amount_minor=39_900,
        decision="ALLOW",
        reasons=[],
        with_provider_order=True,
    )
    theirs = PaymentRequest(
        id=uuid4(),
        tenant_id=seeded_fixture_data.tenant_b.id,
        actor_id=seeded_fixture_data.tenant_b_actor_one,
        merchant_id=seeded_fixture_data.tenant_b_allowed_merchant.id,
        catalog_item_id=seeded_fixture_data.tenant_b_catalog_private.id,
        catalog_sku="TENANT-B-SECRET",
        catalog_name="Tenant B private plan",
        merchant_display_name=seeded_fixture_data.tenant_b_allowed_merchant.name,
        quantity=1,
        purpose="Tenant B internal purchase.",
        amount_minor=1_000,
        currency="INR",
        order_ref=f"order-{uuid4()}",
        idempotency_key=str(uuid4()),
    )
    async_session.add(theirs)
    await async_session.flush()

    response = await console_client.get(f"/console/{seeded_fixture_data.tenant_a.id}")

    assert response.status_code == 200
    assert str(mine.id) in response.text
    assert "TENANT-B-SECRET" not in response.text


async def test_a_refused_attempt_says_plainly_that_nothing_reached_the_provider(
    console_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The single most important cell on the page.

    A viewer will take "DENY" on trust; they should not have to take "and therefore no money
    moved" on trust as well. The row states it, and it is derived from whether a provider order
    actually exists rather than from the decision.
    """

    await _attempt(
        async_session,
        seeded_fixture_data,
        sku="CLOUD-TEAM",
        amount_minor=2_000_000,
        decision="DENY",
        reasons=["AMOUNT_EXCEEDS_LIMIT"],
        with_provider_order=False,
    )

    response = await console_client.get(f"/console/{seeded_fixture_data.tenant_a.id}")

    assert "Nothing reached Razorpay" in response.text
    assert "AMOUNT_EXCEEDS_LIMIT" in response.text


async def test_the_receipt_for_an_attempt_opens_from_a_browser(
    console_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The API receipt needs an `X-Tenant-Id` header, which a browser cannot send.

    Without this route the demo could show the timeline and never open a single receipt, so the
    console carries tenant identity in the path and renders the same receipt.
    """

    request = await _attempt(
        async_session,
        seeded_fixture_data,
        sku="CLOUD-STARTER",
        amount_minor=39_900,
        decision="ALLOW",
        reasons=[],
        with_provider_order=True,
    )

    response = await console_client.get(
        f"/console/{seeded_fixture_data.tenant_a.id}/requests/{request.id}"
    )

    assert response.status_code == 200
    assert "TrustGate receipt" in response.text
    assert "CLOUD-STARTER" in response.text


async def test_another_tenant_cannot_open_a_receipt_through_the_console(
    console_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Guessing a request id is not enough; the tenant in the path still has to own it."""

    request = await _attempt(
        async_session,
        seeded_fixture_data,
        sku="CLOUD-STARTER",
        amount_minor=39_900,
        decision="ALLOW",
        reasons=[],
        with_provider_order=True,
    )

    response = await console_client.get(
        f"/console/{seeded_fixture_data.tenant_b.id}/requests/{request.id}"
    )

    assert response.status_code == 404


def test_hostile_catalog_text_cannot_inject_markup_into_the_timeline() -> None:
    """Catalog text is written by third parties and is rendered here verbatim otherwise.

    The renderer is pure, so this needs no database: hand it an entry carrying markup and check it
    comes back escaped.
    """

    entry = ConsoleEntry(
        payment_request_id=uuid4(),
        requested_at=datetime.now(UTC),
        actor_id="<script>alert('actor')</script>",
        source="MCP_AGENT",
        sku="<img src=x onerror=alert(1)>",
        quantity=1,
        purpose="</td></tr><script>alert('purpose')</script>",
        merchant_display_name="Acme",
        amount_minor=39_900,
        currency="INR",
        decision="DENY",
        reasons=("<script>alert('reason')</script>",),
        approval_granted_by=None,
        payment_state="DENIED",
        provider_order_id=None,
        provider_state=None,
    )

    page = render_console(
        tenant_id=uuid4(),
        tenant_name="<script>alert('tenant')</script>",
        entries=[entry],
        receipt_href="/console/x/requests/{payment_request_id}",
        generated_at=datetime.now(UTC),
    )

    # The page contains no markup of its own from these fields, so any `<script` or `<img` in the
    # output could only have come from the data. Asserting on the attribute text instead would be
    # a weaker check that also fails on safely escaped output: `&lt;img src=x onerror=alert(1)&gt;`
    # is inert, and still contains the substring `onerror=alert`.
    assert "<script" not in page
    assert "<img" not in page
    assert "&lt;script&gt;" in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page


async def test_an_attempt_refused_before_a_request_existed_still_appears(
    console_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The row the demo depends on most, and the one a naive timeline loses.

    An attack turned away at the MCP boundary never becomes a payment request, so a timeline built
    only from requests is silent exactly where the strongest evidence belongs. The audit event is
    the whole record, and the row says plainly that no request was created - which is a stronger
    claim than a denial, because a denial at least implies something was written down.
    """

    async_session.add(
        AuditEvent(
            tenant_id=seeded_fixture_data.tenant_a.id,
            correlation_id=uuid4(),
            event_kind="catalog_purchase_rejected",
            payload={
                "sku": "CLOUD-TEAM",
                "reason": "QUANTITY_EXCEEDS_LIMIT",
                "max_quantity": 2,
                "requested_quantity": 50,
            },
        )
    )
    await async_session.flush()

    response = await console_client.get(f"/console/{seeded_fixture_data.tenant_a.id}")

    assert response.status_code == 200
    assert "QUANTITY_EXCEEDS_LIMIT" in response.text
    assert "no payment request was created" in response.text
    assert "no amount was derived" in response.text
    assert "&times;50" in response.text


async def test_a_boundary_refusal_offers_no_receipt_to_open(
    console_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """There is no receipt, so the row must not pretend there is one.

    A dead link on the row that matters most would be the worst place for one, and it would appear
    only on camera.
    """

    async_session.add(
        AuditEvent(
            tenant_id=seeded_fixture_data.tenant_a.id,
            correlation_id=uuid4(),
            event_kind="catalog_purchase_rejected",
            payload={"sku": "CLOUD-TEAM", "reason": "QUANTITY_EXCEEDS_LIMIT"},
        )
    )
    await async_session.flush()

    response = await console_client.get(f"/console/{seeded_fixture_data.tenant_a.id}")

    assert "no receipt" in response.text
    assert "/requests/None" not in response.text


async def test_a_refusal_and_a_purchase_share_one_timeline_in_time_order(
    console_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Two sources, one list. Ordering by query would put the demo's beats out of sequence."""

    await _attempt(
        async_session,
        seeded_fixture_data,
        sku="CLOUD-STARTER",
        amount_minor=39_900,
        decision="ALLOW",
        reasons=[],
        with_provider_order=True,
    )
    async_session.add(
        AuditEvent(
            tenant_id=seeded_fixture_data.tenant_a.id,
            correlation_id=uuid4(),
            event_kind="catalog_purchase_rejected",
            payload={"sku": "CLOUD-TEAM", "reason": "QUANTITY_EXCEEDS_LIMIT"},
        )
    )
    await async_session.flush()

    response = await console_client.get(f"/console/{seeded_fixture_data.tenant_a.id}")

    page = response.text
    # Ordering is the claim, so it is asserted by position rather than by counting: the
    # refusal was recorded second, so it must render above the purchase.
    assert page.index("CLOUD-TEAM") < page.index("CLOUD-STARTER")
    assert "no payment request was created" in page
    assert "ALLOW" in page
