"""Taking an authorized purchase to a payable page must pick the right one, and only once."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from fixtures import FixtureData
from sqlalchemy.ext.asyncio import AsyncSession

from agent.checkout import CheckoutUnavailableError, find_payable, prepare_checkout
from models.domain import CheckoutAuthority, Payment, PaymentRequest, RazorpayOrder


async def _authorized(
    session: AsyncSession, data: FixtureData, *, with_order: bool = False
) -> PaymentRequest:
    request = PaymentRequest(
        id=uuid4(),
        tenant_id=data.tenant_a.id,
        actor_id=data.tenant_a_actor_one,
        merchant_id=data.tenant_a_allowed_merchant.id,
        catalog_item_id=data.tenant_a_catalog_starter.id,
        catalog_sku="CLOUD-STARTER",
        catalog_name="Cloud Starter",
        merchant_display_name=data.tenant_a_allowed_merchant.name,
        quantity=1,
        purpose="Provision a build environment.",
        source="MCP_AGENT",
        amount_minor=39_900,
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
        authorized_amount_minor=39_900,
        captured_amount_minor=0,
        refunded_amount_minor=0,
    )
    session.add(payment)
    await session.flush()
    if with_order:
        from datetime import UTC, datetime, timedelta

        authority = CheckoutAuthority(
            id=uuid4(),
            tenant_id=data.tenant_a.id,
            payment_request_id=request.id,
            payment_id=payment.id,
            approval_id=None,
            policy_version=data.tenant_a_policy.version,
            snapshot_hash="e" * 64,
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
                amount_minor=39_900,
                currency="INR",
            )
        )
        await session.flush()
    return request


async def test_it_offers_an_authorized_purchase_that_has_not_reached_the_provider(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    request = await _authorized(async_session, seeded_fixture_data)

    payable = await find_payable(async_session, seeded_fixture_data.tenant_a.id)

    assert payable is not None
    assert payable.payment_request_id == request.id
    assert payable.amount_minor == 39_900


async def test_it_does_not_offer_a_purchase_that_already_has_an_order(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """One authorization, one provider order.

    The server would refuse a second, but only after the demo had asked for it - and a refusal on
    camera that the tooling invited is worse than one it avoided.
    """

    await _authorized(async_session, seeded_fixture_data, with_order=True)

    payable = await find_payable(async_session, seeded_fixture_data.tenant_a.id)

    assert payable is None


async def test_it_does_not_reach_across_tenants(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    await _authorized(async_session, seeded_fixture_data)

    payable = await find_payable(async_session, seeded_fixture_data.tenant_b.id)

    assert payable is None


async def test_both_calls_carry_the_tenant_header_and_hit_the_documented_routes() -> None:
    """The driver adds no path to authority; it uses the operator routes that already exist."""

    tenant_id = uuid4()
    request_id = uuid4()
    authority_id = uuid4()
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url.path), request.headers.get("x-tenant-id", "")))
        if "checkout-authorities" in str(request.url.path) and "orders" not in str(
            request.url.path
        ):
            return httpx.Response(
                200,
                json={
                    "checkout_authority_id": str(authority_id),
                    "payment_request_id": str(request_id),
                    "payment_id": str(uuid4()),
                    "expires_at": "2026-08-27T00:00:00Z",
                    "snapshot_hash": "f" * 64,
                },
            )
        return httpx.Response(
            200,
            json={
                "checkout_authority_id": str(authority_id),
                "razorpay_key_id": "rzp_test_x",
                "razorpay_order_id": "order_ABC123",
                "amount_minor": 39_900,
                "currency": "INR",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await prepare_checkout(
            base_url="http://testserver",
            tenant_id=tenant_id,
            payment_request_id=request_id,
            client=client,
        )

    assert seen[0] == (f"/api/v1/checkout-authorities/{request_id}", str(tenant_id))
    assert seen[1] == (
        f"/api/v1/razorpay/checkout-authorities/{authority_id}/orders",
        str(tenant_id),
    )
    assert result["checkout_url"] == "http://testserver/checkout/order_ABC123"


async def test_a_refusal_names_which_step_failed_and_why() -> None:
    """Mid-demo, "policy drift on the authority" is actionable and a traceback is not."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "CHECKOUT_AUTHORITY_POLICY_DRIFT"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(CheckoutUnavailableError) as raised:
            await prepare_checkout(
                base_url="http://testserver",
                tenant_id=uuid4(),
                payment_request_id=uuid4(),
                client=client,
            )

    message = str(raised.value)
    assert "issuing the checkout authority" in message
    assert "CHECKOUT_AUTHORITY_POLICY_DRIFT" in message
