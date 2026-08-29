from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from delegation.chain import DelegationRefused, release
from models.domain import Approval, AuditEvent, Payment, PaymentRequest, SpendingPolicy
from models.locking import locked
from policy_engine.evaluate import release_daily_spend

LEGAL_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = {
    "CREATED": frozenset({"APPROVAL_REQUIRED", "AUTHORIZED", "DENIED", "EXPIRED"}),
    "APPROVAL_REQUIRED": frozenset({"AUTHORIZED", "DENIED", "EXPIRED"}),
    "AUTHORIZED": frozenset({"PROVIDER_PENDING", "EXPIRED", "CANCELLED"}),
    "PROVIDER_PENDING": frozenset({"CAPTURED", "FAILED"}),
    "CAPTURED": frozenset({"REFUNDED", "PARTIALLY_REFUNDED"}),
    "PARTIALLY_REFUNDED": frozenset({"PARTIALLY_REFUNDED", "REFUNDED"}),
    "DENIED": frozenset(),
    "EXPIRED": frozenset(),
    "FAILED": frozenset(),
    "REFUNDED": frozenset(),
    "CANCELLED": frozenset(),
}

# States a payment reaches when its reserved budget will never be spent. Reaching one of these
# returns the reservation, so an abandoned approval cannot consume an actor's day.
#
# CAPTURED is absent because the money was spent. Refund states are also absent: a refund is not
# evidence that the day's budget should reopen, and treating it as such would let the same limit be
# spent twice within one day.
BUDGET_RELEASING_STATES: Final[frozenset[str]] = frozenset(
    {"DENIED", "EXPIRED", "FAILED", "CANCELLED"}
)

# States a payment occupies only after its request reserved daily budget. Budget is reserved when
# a decision is ALLOW or REQUIRE_APPROVAL, which move the payment to AUTHORIZED or
# APPROVAL_REQUIRED respectively; a request denied outright never reserves and stays in CREATED
# until it is marked DENIED.
#
# Releasing without this guard would refund budget that was never taken. A request denied *because*
# the day was already full would hand back an amount it never held, letting an agent manufacture
# budget by making requests it knew would fail.
RESERVATION_HOLDING_STATES: Final[frozenset[str]] = frozenset(
    {"APPROVAL_REQUIRED", "AUTHORIZED", "PROVIDER_PENDING"}
)


class StateMachineError(Exception):
    reason_code = "STATE_MACHINE_ERROR"

    def __init__(self, payment_id: UUID, message: str) -> None:
        super().__init__(message)
        self.payment_id = payment_id


class PaymentNotFoundError(StateMachineError):
    reason_code = "PAYMENT_NOT_FOUND"


class IllegalTransitionError(StateMachineError):
    reason_code = "ILLEGAL_STATE_TRANSITION"

    def __init__(self, payment_id: UUID, from_state: str, to_state: str) -> None:
        super().__init__(payment_id, f"Cannot transition payment from {from_state} to {to_state}.")
        self.from_state = from_state
        self.to_state = to_state


class CaptureExceedsAuthorizedError(StateMachineError):
    reason_code = "CAPTURE_EXCEEDS_AUTHORIZED"


class RefundExceedsCapturedError(StateMachineError):
    reason_code = "REFUND_EXCEEDS_CAPTURED"


class ApprovalRequiredForAuthorizationError(StateMachineError):
    """Raised when approval-required work is authorized without an approval id."""

    reason_code = "APPROVAL_REQUIRED_MISSING"

    def __init__(self, payment_id: UUID) -> None:
        super().__init__(payment_id, "An approval id is required to authorize this payment.")


class ApprovalNotFoundError(StateMachineError):
    reason_code = "APPROVAL_NOT_FOUND"


class ApprovalAlreadyConsumedError(StateMachineError):
    reason_code = "APPROVAL_ALREADY_CONSUMED"


class ApprovalExpiredError(StateMachineError):
    reason_code = "APPROVAL_EXPIRED"


class ApprovalPolicyVersionMismatchError(StateMachineError):
    reason_code = "APPROVAL_POLICY_VERSION_MISMATCH"


def validate_transition(payment: Payment, to_state: str) -> None:
    """Validate the shared lifecycle and amount invariants without persisting changes."""

    if payment.state not in LEGAL_TRANSITIONS or to_state not in LEGAL_TRANSITIONS[payment.state]:
        raise IllegalTransitionError(payment.id, payment.state, to_state)

    authorized_amount = payment.authorized_amount_minor
    if authorized_amount is None:
        capture_states = {"CAPTURED", "PARTIALLY_REFUNDED", "REFUNDED"}
        if (
            payment.captured_amount_minor > 0
            or payment.state in capture_states
            or to_state in capture_states
        ):
            raise CaptureExceedsAuthorizedError(
                payment.id,
                "A payment cannot be captured without an authorized amount.",
            )
    elif payment.captured_amount_minor > authorized_amount:
        raise CaptureExceedsAuthorizedError(
            payment.id,
            "Captured amount exceeds the authorized amount.",
        )

    if payment.refunded_amount_minor > payment.captured_amount_minor:
        raise RefundExceedsCapturedError(
            payment.id,
            "Refunded amount exceeds the captured amount.",
        )


async def transition(
    session: AsyncSession,
    payment: Payment,
    to_state: str,
    *,
    reason: str,
    correlation_id: UUID,
    approval_id: UUID | None = None,
) -> Payment:
    """Perform the only permitted payment state change and write its audit record.

    The caller supplies a payment already scoped by trusted tenant identity. This
    function re-reads and locks that exact tenant/payment pair before updating it.

    `populate_existing` is what makes the lock mean anything. Without it SQLAlchemy returns the
    instance already in the identity map and keeps its loaded attributes, so a caller that waited
    on the lock would acquire it, receive the committed row from Postgres, and then decide from the
    state it read before waiting. The wait would be real and the decision stale, which permits two
    callers to authorize the same payment. `expire_on_commit=False` removes the only thing that
    would otherwise have refreshed it.
    """

    transaction = session.begin_nested() if session.in_transaction() else session.begin()
    error: StateMachineError | None = None
    locked_payment: Payment | None = None

    async with transaction:
        with session.no_autoflush:
            result = await session.execute(
                locked(
                    select(Payment).where(
                        Payment.id == payment.id, Payment.tenant_id == payment.tenant_id
                    )
                )
            )
            locked_payment = result.scalar_one_or_none()

        if locked_payment is None:
            raise PaymentNotFoundError(payment.id, "Payment was not found for the supplied tenant.")

        try:
            if locked_payment.state == "APPROVAL_REQUIRED" and to_state == "AUTHORIZED":
                if approval_id is None:
                    session.add(
                        AuditEvent(
                            tenant_id=locked_payment.tenant_id,
                            payment_request_id=locked_payment.payment_request_id,
                            payment_id=locked_payment.id,
                            correlation_id=correlation_id,
                            event_kind="illegal_transition_attempt",
                            payload={
                                "reason": "APPROVAL_REQUIRED_MISSING",
                                "payment_id": str(locked_payment.id),
                            },
                        )
                    )
                    raise ApprovalRequiredForAuthorizationError(locked_payment.id)
                await _consume_approval(
                    session,
                    payment=locked_payment,
                    approval_id=approval_id,
                )
            validate_transition(locked_payment, to_state)
        except StateMachineError as exc:
            error = exc
            if not isinstance(exc, ApprovalRequiredForAuthorizationError):
                attempted_amounts = {
                    "authorized_amount_minor": locked_payment.authorized_amount_minor,
                    "captured_amount_minor": locked_payment.captured_amount_minor,
                    "refunded_amount_minor": locked_payment.refunded_amount_minor,
                }
                await session.refresh(locked_payment)
                _write_audit_event(
                    session,
                    locked_payment,
                    correlation_id=correlation_id,
                    event_kind="illegal_transition_attempt",
                    reason=reason,
                    reason_code=exc.reason_code,
                    to_state=to_state,
                    attempted_amounts=attempted_amounts,
                )
        else:
            from_state = locked_payment.state
            locked_payment.state = to_state
            if to_state in BUDGET_RELEASING_STATES and from_state in RESERVATION_HOLDING_STATES:
                await _release_reserved_budget(session, payment=locked_payment)
                await _release_delegated_budget(
                    session, payment=locked_payment, correlation_id=correlation_id
                )
            _write_audit_event(
                session,
                locked_payment,
                correlation_id=correlation_id,
                event_kind="payment_transition",
                reason=reason,
                reason_code="STATE_TRANSITION_ACCEPTED",
                to_state=to_state,
                from_state=from_state,
            )

        await session.flush()

    if error is not None:
        raise error

    if locked_payment is None:
        raise RuntimeError("Payment row lock unexpectedly returned no payment.")
    return locked_payment


async def _release_delegated_budget(
    session: AsyncSession,
    *,
    payment: Payment,
    correlation_id: UUID,
) -> None:
    """Give back the delegation budget this payment's request claimed, if it claimed any.

    Hung on the same condition as the daily reservation deliberately. That condition already
    enumerates every state a payment dies in - DENIED, EXPIRED, FAILED, CANCELLED - and is itself
    covered by a mutation, so this inherits the enumeration rather than repeating it and getting a
    state wrong later.

    A request that never spent a delegation has nothing to return, and `release` says so by
    refusing. That is the ordinary case here, not an error: most payments have no delegation at
    all. It is caught and dropped for that reason, and for no other.
    """

    try:
        await release(
            session,
            tenant_id=payment.tenant_id,
            reference=payment.payment_request_id,
            correlation_id=correlation_id,
        )
    except DelegationRefused:
        return


async def _release_reserved_budget(session: AsyncSession, *, payment: Payment) -> None:
    """Return the reservation this payment's request took when it was evaluated.

    The reservation is keyed by the day the request was evaluated, not by today, so a request that
    dies after midnight releases the budget it actually consumed rather than a later day's.
    """

    request = await session.scalar(
        select(PaymentRequest).where(
            PaymentRequest.id == payment.payment_request_id,
            PaymentRequest.tenant_id == payment.tenant_id,
        )
    )
    if request is None:
        return
    await release_daily_spend(
        session,
        tenant_id=payment.tenant_id,
        actor_id=request.actor_id,
        amount_minor=request.amount_minor,
        spend_date=request.created_at.date(),
    )


async def _consume_approval(session: AsyncSession, *, payment: Payment, approval_id: UUID) -> None:
    """Validate and atomically consume the one-time approval in this transaction."""

    approval = await session.scalar(
        locked(
            select(Approval).where(
                Approval.id == approval_id, Approval.tenant_id == payment.tenant_id
            )
        )
    )
    if approval is None or approval.payment_request_id != payment.payment_request_id:
        raise ApprovalNotFoundError(approval_id, "Approval was not found for this payment request.")
    if approval.consumed_at is not None:
        raise ApprovalAlreadyConsumedError(approval_id, "Approval has already been consumed.")
    now = datetime.now(UTC)
    if approval.expires_at <= now:
        raise ApprovalExpiredError(approval_id, "Approval has expired.")
    current_policy_version = await session.scalar(
        select(SpendingPolicy.version)
        .where(SpendingPolicy.tenant_id == payment.tenant_id)
        .order_by(SpendingPolicy.version.desc())
        .limit(1)
    )
    if current_policy_version != approval.policy_version:
        raise ApprovalPolicyVersionMismatchError(
            approval_id, "Approval was granted for a different policy version."
        )
    consumed = await session.execute(
        update(Approval)
        .where(
            Approval.id == approval_id,
            Approval.tenant_id == payment.tenant_id,
            Approval.payment_request_id == payment.payment_request_id,
            Approval.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    if int(getattr(consumed, "rowcount", 0)) != 1:
        raise ApprovalAlreadyConsumedError(approval_id, "Approval has already been consumed.")


def _write_audit_event(
    session: AsyncSession,
    payment: Payment,
    *,
    correlation_id: UUID,
    event_kind: str,
    reason: str,
    reason_code: str,
    to_state: str,
    from_state: str | None = None,
    attempted_amounts: dict[str, int | None] | None = None,
) -> None:
    payload: dict[str, object] = {
        "payment_id": str(payment.id),
        "from_state": from_state if from_state is not None else payment.state,
        "to_state": to_state,
        "reason": reason,
        "reason_code": reason_code,
    }
    if attempted_amounts is not None:
        payload["attempted_amounts"] = attempted_amounts

    session.add(
        AuditEvent(
            tenant_id=payment.tenant_id,
            payment_request_id=payment.payment_request_id,
            payment_id=payment.id,
            correlation_id=correlation_id,
            event_kind=event_kind,
            payload=payload,
        )
    )
