"""The last step of the provider flow, made repeatable and made honest.

`agent.capture` signs two Razorpay-shaped events with the project's own webhook secret and posts
them, carrying a paid order to `CAPTURED`. That is the step the browser callback deliberately
refuses to take, and the step Razorpay cannot take against a laptop.

The risk this command carries is not technical. It is that someone runs it on camera and lets a
viewer believe Razorpay delivered those events. So the provenance disclaimer is asserted here like
any other invariant: the command has to say, every run, that it signed them itself.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fixtures import FixtureData
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.capture import build_event, find_capturable_order, sign
from models.domain import CheckoutAuthority, Payment, RazorpayOrder

SECRET = "capture-test-webhook-secret"  # noqa: S105 - synthetic


async def _order(
    session: AsyncSession, data: FixtureData, *, state: str = "AUTHORIZED"
) -> RazorpayOrder:
    """A payment that reached the provider, with the order row the real adapter would have left."""

    payment = await session.scalar(
        select(Payment).where(Payment.payment_request_id == data.payment_request.id)
    )
    assert payment is not None
    payment.state = state

    authority = CheckoutAuthority(
        id=uuid4(),
        tenant_id=data.tenant_a.id,
        payment_request_id=data.payment_request.id,
        payment_id=payment.id,
        approval_id=None,
        policy_version=data.tenant_a_policy.version,
        snapshot_hash="0" * 64,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
        # Postgres's clock, not this machine's. `used_at` is compared against the row's
        # server-generated `created_at`, so a host value here is a bet on two clocks
        # agreeing - which `tests/test_fixture_discipline.py` exists to refuse, and did.
        used_at=func.now(),
    )
    session.add(authority)
    await session.flush()

    order = RazorpayOrder(
        id=uuid4(),
        tenant_id=data.tenant_a.id,
        checkout_authority_id=authority.id,
        payment_id=payment.id,
        razorpay_order_id=f"order_{uuid4().hex[:14]}",
        provider_state="CONFIRMED",
        receipt=f"tg_{uuid4().hex}"[:40],
        amount_minor=data.payment_request.amount_minor,
        currency=data.payment_request.currency,
    )
    session.add(order)
    await session.flush()
    return order


# --- finding what to capture ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_it_finds_the_paid_order_without_being_handed_an_identifier(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """No identifier copied between terminals on camera."""

    order = await _order(async_session, seeded_fixture_data)

    found = await find_capturable_order(async_session, seeded_fixture_data.tenant_a.id)

    assert found is not None
    assert found[0].razorpay_order_id == order.razorpay_order_id


@pytest.mark.asyncio
async def test_a_settled_payment_is_not_offered_for_capture_again(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Capturing twice is the state machine's problem, and not reaching for it is this one's."""

    await _order(async_session, seeded_fixture_data, state="CAPTURED")

    assert await find_capturable_order(async_session, seeded_fixture_data.tenant_a.id) is None


@pytest.mark.asyncio
async def test_another_tenants_order_is_not_capturable(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    await _order(async_session, seeded_fixture_data)

    assert await find_capturable_order(async_session, seeded_fixture_data.tenant_b.id) is None


# --- the bytes that get signed ---------------------------------------------------------------


def test_the_signature_covers_the_exact_bytes_that_are_delivered() -> None:
    """Signing a re-serialisation of the body would sign a different message than the one sent.

    The route verifies over raw bytes before parsing, for exactly this reason. Building the body
    once and signing those bytes is what makes the two halves agree.
    """

    body = build_event(
        event="payment.captured",
        razorpay_order_id="order_abc123",
        razorpay_payment_id="pay_def456",
        amount_minor=39_900,
        currency="INR",
    )

    expected = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()

    assert sign(body, SECRET) == expected
    assert hmac.compare_digest(sign(body, SECRET), expected)


def test_both_lifecycle_events_carry_one_payment_id_as_razorpay_sends_them() -> None:
    """The case `webhook_event_identity` exists for.

    Razorpay reports the authorization and the capture of one payment under the same payment id.
    A harness that gave them different ids would never exercise the deduplication problem the route
    solves, and would quietly stop proving the thing it was written to prove.
    """

    payment_id = "pay_shared123456"
    authorized = json.loads(
        build_event(
            event="payment.authorized",
            razorpay_order_id="order_abc123",
            razorpay_payment_id=payment_id,
            amount_minor=39_900,
            currency="INR",
        )
    )
    captured = json.loads(
        build_event(
            event="payment.captured",
            razorpay_order_id="order_abc123",
            razorpay_payment_id=payment_id,
            amount_minor=39_900,
            currency="INR",
        )
    )

    assert authorized["payload"]["payment"]["entity"]["id"] == payment_id
    assert captured["payload"]["payment"]["entity"]["id"] == payment_id
    assert authorized["event"] != captured["event"]


def test_the_body_carries_a_timestamp_because_an_undateable_event_is_refused() -> None:
    """The route refuses an event it cannot bound in time, with its own reason code."""

    body = json.loads(
        build_event(
            event="payment.captured",
            razorpay_order_id="order_abc123",
            razorpay_payment_id="pay_def456",
            amount_minor=39_900,
            currency="INR",
        )
    )

    assert isinstance(body["created_at"], int)
    assert abs(body["created_at"] - datetime.now(UTC).timestamp()) < 60


def test_the_amount_is_the_order_s_and_not_a_number_the_command_chose() -> None:
    """The route cross-checks the reported amount against the server-derived order.

    A command that invented its own amount would either fail that check or, worse, pass it by
    coincidence and stop testing it.
    """

    body = json.loads(
        build_event(
            event="payment.captured",
            razorpay_order_id="order_abc123",
            razorpay_payment_id="pay_def456",
            amount_minor=60_000,
            currency="INR",
        )
    )

    assert body["payload"]["payment"]["entity"]["amount"] == 60_000


# --- the part that is not about code ----------------------------------------------------------


def test_the_command_says_it_signed_the_events_itself() -> None:
    """The disclaimer is an invariant, not a nicety.

    This command exists so a capture can be shown on a laptop. The failure mode is not that it
    breaks - it is that it works, and a viewer concludes Razorpay delivered those events. The
    module says otherwise in its own output, and this is what keeps that true when someone tidies
    the printing later.
    """

    from pathlib import Path

    import agent.capture

    source_file = agent.capture.__file__
    assert source_file is not None
    source = Path(source_file).read_text(encoding="utf-8")

    assert "Razorpay did not send them" in source, (
        "the spoken provenance line left the command's output"
    )
    # Not "cannot reach a laptop". With the Cloudflare tunnel up it can, and a disclaimer that
    # overstates its own constraint is still a disclaimer that is wrong.
    assert "unless a tunnel is running" in source
    assert "signed here with this project's own" in source
    assert "provider-originated" in source


def test_it_delivers_the_authorization_before_the_capture() -> None:
    """The state machine has no AUTHORIZED -> CAPTURED edge, so order is correctness, not style."""

    from agent.capture import _LIFECYCLE

    assert _LIFECYCLE == ("payment.authorized", "payment.captured")


@pytest.mark.asyncio
async def test_the_tenant_is_the_one_asked_for_not_the_one_that_happens_to_have_an_order(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """A stale MCP_TENANT_ID should find nothing rather than capture a stranger's payment."""

    await _order(async_session, seeded_fixture_data)

    assert await find_capturable_order(async_session, UUID(int=0)) is None
