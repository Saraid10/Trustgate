from __future__ import annotations

from uuid import uuid4

import pytest
from fixtures import FixtureData
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.domain import AuditEvent, Payment
from state_machine.transitions import (
    LEGAL_TRANSITIONS,
    CaptureExceedsAuthorizedError,
    IllegalTransitionError,
    RefundExceedsCapturedError,
    transition,
    validate_transition,
)

ALL_STATES = tuple(LEGAL_TRANSITIONS)
TERMINAL_STATES = ("DENIED", "EXPIRED", "FAILED", "REFUNDED", "CANCELLED")


def _payment(
    *,
    state: str = "CREATED",
    authorized_amount_minor: int | None = 100,
    captured_amount_minor: int = 0,
    refunded_amount_minor: int = 0,
) -> Payment:
    return Payment(
        id=uuid4(),
        tenant_id=uuid4(),
        payment_request_id=uuid4(),
        state=state,
        authorized_amount_minor=authorized_amount_minor,
        captured_amount_minor=captured_amount_minor,
        refunded_amount_minor=refunded_amount_minor,
    )


@pytest.mark.asyncio
async def test_legal_transition_updates_state_and_writes_one_audit_event(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    correlation_id = uuid4()

    transitioned = await transition(
        async_session,
        seeded_fixture_data.payment,
        "AUTHORIZED",
        reason="policy_allowed",
        correlation_id=correlation_id,
    )

    audit_events = await async_session.scalars(
        select(AuditEvent).where(AuditEvent.correlation_id == correlation_id)
    )
    events = list(audit_events)
    assert transitioned.state == "AUTHORIZED"
    assert len(events) == 1
    assert events[0].event_kind == "payment_transition"
    assert events[0].payload["reason_code"] == "STATE_TRANSITION_ACCEPTED"


@pytest.mark.asyncio
async def test_illegal_transition_keeps_state_and_writes_one_audit_event(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    correlation_id = uuid4()

    with pytest.raises(IllegalTransitionError):
        await transition(
            async_session,
            seeded_fixture_data.payment,
            "CAPTURED",
            reason="provider_capture",
            correlation_id=correlation_id,
        )

    state = await async_session.scalar(
        select(Payment.state).where(Payment.id == seeded_fixture_data.payment.id)
    )
    audit_count = await async_session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.correlation_id == correlation_id)
    )
    assert state == "CREATED"
    assert audit_count == 1


@pytest.mark.asyncio
async def test_capture_cannot_exceed_authorized_amount(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    payment = _payment(
        state="PROVIDER_PENDING",
        authorized_amount_minor=100,
        captured_amount_minor=101,
    )
    payment.tenant_id = seeded_fixture_data.tenant_a.id
    payment.payment_request_id = seeded_fixture_data.payment_request.id
    async_session.add(payment)
    await async_session.flush()

    with pytest.raises(CaptureExceedsAuthorizedError):
        await transition(
            async_session,
            payment,
            "CAPTURED",
            reason="provider_capture",
            correlation_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_refund_cannot_exceed_captured_amount(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    payment = _payment(
        state="CAPTURED",
        authorized_amount_minor=100,
        captured_amount_minor=100,
        refunded_amount_minor=101,
    )
    payment.tenant_id = seeded_fixture_data.tenant_a.id
    payment.payment_request_id = seeded_fixture_data.payment_request.id
    async_session.add(payment)
    await async_session.flush()

    with pytest.raises(RefundExceedsCapturedError):
        await transition(
            async_session,
            payment,
            "REFUNDED",
            reason="refund_requested",
            correlation_id=uuid4(),
        )


@pytest.mark.parametrize("terminal_state", TERMINAL_STATES)
@pytest.mark.parametrize("to_state", ("AUTHORIZED", "CAPTURED"))
def test_terminal_states_cannot_authorize_or_capture(terminal_state: str, to_state: str) -> None:
    with pytest.raises(IllegalTransitionError):
        validate_transition(_payment(state=terminal_state), to_state)


@settings(max_examples=500, deadline=None)
@given(st.lists(st.sampled_from(ALL_STATES), min_size=1, max_size=30))
def test_generated_transition_sequences_only_follow_legal_edges(destinations: list[str]) -> None:
    payment = _payment()

    for destination in destinations:
        was_legal = destination in LEGAL_TRANSITIONS[payment.state]
        try:
            validate_transition(payment, destination)
        except IllegalTransitionError:
            assert not was_legal
        else:
            assert was_legal
            payment.state = destination


@settings(max_examples=500, deadline=None)
@given(
    authorized_amount=st.one_of(st.none(), st.integers(min_value=0, max_value=10_000)),
    captured_amount=st.integers(min_value=0, max_value=10_000),
    refunded_amount=st.integers(min_value=0, max_value=10_000),
)
def test_amount_invariants_reject_excess_capture_or_refund(
    authorized_amount: int | None, captured_amount: int, refunded_amount: int
) -> None:
    payment = _payment(
        state="CAPTURED",
        authorized_amount_minor=authorized_amount,
        captured_amount_minor=captured_amount,
        refunded_amount_minor=refunded_amount,
    )

    if authorized_amount is None:
        with pytest.raises(CaptureExceedsAuthorizedError):
            validate_transition(payment, "REFUNDED")
    elif captured_amount > authorized_amount:
        with pytest.raises(CaptureExceedsAuthorizedError):
            validate_transition(payment, "REFUNDED")
    elif refunded_amount > captured_amount:
        with pytest.raises(RefundExceedsCapturedError):
            validate_transition(payment, "REFUNDED")
    else:
        validate_transition(payment, "REFUNDED")
