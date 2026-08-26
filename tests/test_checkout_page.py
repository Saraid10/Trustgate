"""The Standard Checkout page renders; it must never authorize.

A page a browser can request is the least trusted surface in the system. These tests pin the two
properties that matter: it moves nothing, and it leaks nothing. Loading it must consume no
authority and create no provider order, and the rendered bytes must carry no secret.
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
from api.database import get_session
from models.domain import CheckoutAuthority, Payment, PaymentRequest, RazorpayOrder
from scenarios.tier_a.harness import assert_attack_created_nothing, snapshot_tenant

KEY_ID = "rzp_test_publishable"
KEY_SECRET = "server-side-only-secret"  # noqa: S105
WEBHOOK_SECRET = "webhook-side-only-secret"  # noqa: S105
ORDER_AMOUNT = 39_900


@pytest_asyncio.fixture
async def client(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("RAZORPAY_KEY_ID", KEY_ID)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", KEY_SECRET)
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", WEBHOOK_SECRET)

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


async def _confirmed_order(
    session: AsyncSession, data: FixtureData, *, state: str = "CONFIRMED"
) -> RazorpayOrder:
    request = PaymentRequest(
        id=uuid4(),
        tenant_id=data.tenant_a.id,
        actor_id=data.tenant_a_actor_one,
        merchant_id=data.tenant_a_allowed_merchant.id,
        catalog_item_id=data.tenant_a_catalog_starter.id,
        catalog_sku="CLOUD-STARTER",
        catalog_name="Cloud Starter",
        merchant_display_name="A Allowed One",
        quantity=1,
        purpose="Provision an isolated build environment.",
        amount_minor=ORDER_AMOUNT,
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
        authorized_amount_minor=ORDER_AMOUNT,
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
        snapshot_hash="d" * 64,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
        # Postgres stamps created_at from its own clock, so used_at must come from the same one.
        # A host-clock timestamp here fails `used_at >= created_at` whenever the container clock
        # drifts ahead, which under Docker Desktop it does.
        used_at=func.now(),
    )
    session.add(authority)
    await session.flush()
    order = RazorpayOrder(
        id=uuid4(),
        tenant_id=data.tenant_a.id,
        checkout_authority_id=authority.id,
        payment_id=payment.id,
        razorpay_order_id=f"order_{uuid4().hex[:14]}" if state == "CONFIRMED" else None,
        provider_state=state,
        receipt=f"tg_{authority.id.hex}",
        amount_minor=ORDER_AMOUNT,
        currency="INR",
    )
    session.add(order)
    await session.flush()
    return order


async def test_the_page_carries_no_secret(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The single most important property of this page."""

    order = await _confirmed_order(async_session, seeded_fixture_data)

    response = await client.get(f"/api/v1/razorpay/checkout/{order.razorpay_order_id}")

    assert response.status_code == 200
    assert KEY_SECRET not in response.text
    assert WEBHOOK_SECRET not in response.text
    # The publishable key is required in the browser and is safe to expose.
    assert KEY_ID in response.text


async def test_the_page_renders_the_server_derived_purchase(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    order = await _confirmed_order(async_session, seeded_fixture_data)

    response = await client.get(f"/api/v1/razorpay/checkout/{order.razorpay_order_id}")

    assert "CLOUD-STARTER" in response.text
    assert "Cloud Starter" in response.text
    assert "399.00" in response.text
    assert str(order.razorpay_order_id) in response.text
    assert str(ORDER_AMOUNT) in response.text


async def test_loading_the_page_authorizes_nothing(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Rendering must not consume an authority, create an order, or advance a payment."""

    order = await _confirmed_order(async_session, seeded_fixture_data)
    before = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)

    await client.get(f"/api/v1/razorpay/checkout/{order.razorpay_order_id}")
    await client.get(f"/api/v1/razorpay/checkout/{order.razorpay_order_id}")

    after = await snapshot_tenant(async_session, seeded_fixture_data.tenant_a.id)
    assert_attack_created_nothing(before, after)


async def test_an_unconfirmed_intent_has_no_checkout_page(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """A pending intent has no provider order to pay against."""

    await _confirmed_order(async_session, seeded_fixture_data, state="PENDING")

    response = await client.get("/api/v1/razorpay/checkout/order_doesnotexistyet")

    assert response.status_code == 404
    assert response.json()["detail"] == "RAZORPAY_ORDER_NOT_FOUND"


async def test_an_unknown_order_is_not_found(client: AsyncClient) -> None:
    response = await client.get("/api/v1/razorpay/checkout/order_neverexisted")

    assert response.status_code == 404


async def test_the_page_posts_the_browser_result_back_for_verification(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The handler must not treat its own callback as success."""

    order = await _confirmed_order(async_session, seeded_fixture_data)

    body = (await client.get(f"/api/v1/razorpay/checkout/{order.razorpay_order_id}")).text

    assert "/api/v1/razorpay/callback" in body
    assert "razorpay_signature" in body
    # The page states plainly that completing it is not proof of payment.
    assert "not proof of payment" in body


async def test_the_page_loads_checkout_from_razorpay(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    order = await _confirmed_order(async_session, seeded_fixture_data)

    body = (await client.get(f"/api/v1/razorpay/checkout/{order.razorpay_order_id}")).text

    assert "https://checkout.razorpay.com/v1/checkout.js" in body
    assert "Payment attempt failed. You may retry:" in body


HOSTILE_NAME = "</script><script>window.__pwned=1</script>"


async def test_hostile_catalog_text_cannot_escape_the_script_block(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Catalog text is the untrusted vector this project exists to contain.

    `json.dumps` emits `</script>` verbatim, so a catalog name carrying it would close the script
    element and let whatever followed execute in the customer's browser.
    """

    order = await _confirmed_order(async_session, seeded_fixture_data)
    purchase = await async_session.scalar(
        select(PaymentRequest)
        .join(
            CheckoutAuthority,
            CheckoutAuthority.payment_request_id == PaymentRequest.id,
        )
        .where(CheckoutAuthority.id == order.checkout_authority_id)
    )
    assert purchase is not None
    purchase.catalog_name = HOSTILE_NAME
    await async_session.flush()

    body = (await client.get(f"/api/v1/razorpay/checkout/{order.razorpay_order_id}")).text

    # The payload appears nowhere as live markup, in the script block or the document body.
    assert "<script>window.__pwned" not in body
    assert "</script><script>" not in body
    assert "\\u003c/script\\u003e" in body, "the name was not neutralised inside the script block"


async def test_the_page_sends_a_script_nonce_policy(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Defence in depth: even injected markup would have no way to execute."""

    order = await _confirmed_order(async_session, seeded_fixture_data)

    response = await client.get(f"/api/v1/razorpay/checkout/{order.razorpay_order_id}")
    policy = response.headers.get("Content-Security-Policy", "")

    assert "script-src 'nonce-" in policy
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy
    nonce = policy.split("script-src 'nonce-")[1].split("'")[0]
    assert nonce, "the policy carries an empty nonce"
    assert f'nonce="{nonce}"' in response.text, "the page scripts do not carry the policy nonce"


async def test_each_render_uses_a_fresh_nonce(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """A reused nonce is no better than allowing inline scripts outright."""

    order = await _confirmed_order(async_session, seeded_fixture_data)
    path = f"/api/v1/razorpay/checkout/{order.razorpay_order_id}"

    first = (await client.get(path)).headers["Content-Security-Policy"]
    second = (await client.get(path)).headers["Content-Security-Policy"]

    assert first != second
