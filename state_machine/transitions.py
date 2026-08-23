from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.domain import Approval, AuditEvent, Payment, SpendingPolicy

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
    """

    transaction = session.begin_nested() if session.in_transaction() else session.begin()
    error: StateMachineError | None = None
    locked_payment: Payment | None = None

    async with transaction:
        with session.no_autoflush:
            result = await session.execute(
                select(Payment)
                .where(Payment.id == payment.id, Payment.tenant_id == payment.tenant_id)
                .with_for_update()
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


async def _consume_approval(session: AsyncSession, *, payment: Payment, approval_id: UUID) -> None:
    """Validate and atomically consume the one-time approval in this transaction."""

    approval = await session.scalar(
        select(Approval)
        .where(Approval.id == approval_id, Approval.tenant_id == payment.tenant_id)
        .with_for_update()
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
            correlation_id=correlation_id,
            event_kind=event_kind,
            payload=payload,
        )
    )
