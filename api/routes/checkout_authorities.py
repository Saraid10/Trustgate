from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_session
from api.dependencies import require_tenant
from delegation.chain import DelegationRefused, assert_chain_live
from models.domain import (
    Approval,
    AuditEvent,
    AuthorizationDecision,
    CheckoutAuthority,
    Payment,
    PaymentRequest,
    SpendingPolicy,
    Tenant,
)
from models.locking import locked
from schemas.domain import CheckoutAuthorityResponse
from state_machine.transitions import transition

router = APIRouter(prefix="/api/v1/checkout-authorities", tags=["checkout authorities"])
_AUTHORITY_TTL = timedelta(minutes=15)


class CheckoutAuthorityUnavailableError(Exception):
    """Raised when a provider attempts to consume an invalid checkout authority."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _snapshot_hash(request: PaymentRequest, policy_version: int) -> str:
    """Hash the immutable purchase facts that a provider order must represent."""

    snapshot = {
        "tenant_id": str(request.tenant_id),
        "payment_request_id": str(request.id),
        "actor_id": request.actor_id,
        "merchant_id": str(request.merchant_id),
        "catalog_item_id": str(request.catalog_item_id),
        "catalog_sku": request.catalog_sku,
        "catalog_name": request.catalog_name,
        "merchant_display_name": request.merchant_display_name,
        "quantity": request.quantity,
        "purpose": request.purpose,
        "amount_minor": request.amount_minor,
        "currency": request.currency,
        "policy_version": policy_version,
        "request_revision": request.request_revision,
    }
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _rejection(reason: str, http_status: int) -> JSONResponse:
    return JSONResponse(status_code=http_status, content={"detail": reason})


async def consume_checkout_authority(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    checkout_authority_id: UUID,
    correlation_id: UUID,
) -> CheckoutAuthority:
    """Atomically claim one still-valid authority for the future provider adapter.

    A provider call must use this helper in its transaction before creating an order. A crash after
    the claim is deliberately fail-closed: retry cannot create a second provider order.
    """

    transaction = session.begin_nested() if session.in_transaction() else session.begin()
    async with transaction:
        await session.scalar(locked(select(Tenant).where(Tenant.id == tenant_id)))
        authority = await session.scalar(
            locked(
                select(CheckoutAuthority).where(
                    CheckoutAuthority.id == checkout_authority_id,
                    CheckoutAuthority.tenant_id == tenant_id,
                )
            )
        )
        if authority is None:
            raise CheckoutAuthorityUnavailableError("CHECKOUT_AUTHORITY_NOT_FOUND")
        if authority.used_at is not None or authority.expires_at <= datetime.now(UTC):
            raise CheckoutAuthorityUnavailableError("CHECKOUT_AUTHORITY_UNAVAILABLE")
        request = await session.scalar(
            locked(
                select(PaymentRequest).where(
                    PaymentRequest.id == authority.payment_request_id,
                    PaymentRequest.tenant_id == tenant_id,
                )
            )
        )
        payment = await session.scalar(
            locked(
                select(Payment).where(
                    Payment.id == authority.payment_id, Payment.tenant_id == tenant_id
                )
            )
        )
        policy = await session.scalar(
            select(SpendingPolicy)
            .where(SpendingPolicy.tenant_id == tenant_id)
            .order_by(SpendingPolicy.version.desc())
            .limit(1)
        )
        if request is None or payment is None or payment.state != "AUTHORIZED":
            raise CheckoutAuthorityUnavailableError("CHECKOUT_AUTHORITY_PAYMENT_NOT_AUTHORIZED")
        if (
            policy is None
            or policy.version != authority.policy_version
            or policy.expiry <= datetime.now(UTC)
            or _snapshot_hash(request, authority.policy_version) != authority.snapshot_hash
        ):
            raise CheckoutAuthorityUnavailableError("CHECKOUT_AUTHORITY_POLICY_DRIFT")
        if request.delegation_id is not None:
            # The last gate before a provider order exists. An authority issued while the chain was
            # live can be consumed up to fifteen minutes later, and a human revoking inside that
            # window means this money must not move.
            #
            # Refusing is all that happens here. The budgets this payment holds are not returned,
            # because returning them means writing, and every write in this function is undone by
            # the rollback that carries the raise out - `get_session` rolls the request back, which
            # is the property that makes a crash mid-consume fail closed. Issuing is where a dead
            # chain cancels the payment and releases both budgets; this is the narrower case of a
            # chain that died after its authority was already in hand.
            try:
                await assert_chain_live(
                    session, tenant_id=tenant_id, delegation_id=request.delegation_id
                )
            except DelegationRefused as refused:
                raise CheckoutAuthorityUnavailableError(refused.reason) from refused
        authority.used_at = datetime.now(UTC)
        session.add(
            AuditEvent(
                tenant_id=tenant_id,
                payment_request_id=request.id,
                payment_id=payment.id,
                checkout_authority_id=authority.id,
                correlation_id=correlation_id,
                event_kind="checkout_authority_consumed",
                payload={"checkout_authority_id": str(authority.id), "payment_id": str(payment.id)},
            )
        )
        await session.flush()
        return authority


@router.post("/{payment_request_id}", response_model=CheckoutAuthorityResponse)
async def issue_checkout_authority(
    payment_request_id: UUID,
    tenant: Annotated[Tenant, Depends(require_tenant)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CheckoutAuthorityResponse | JSONResponse:
    """Issue one checkout permission only for the still-authorized purchase snapshot."""

    transaction = session.begin_nested() if session.in_transaction() else session.begin()
    async with transaction:
        await session.scalar(locked(select(Tenant).where(Tenant.id == tenant.id)))
        request = await session.scalar(
            locked(
                select(PaymentRequest).where(
                    PaymentRequest.id == payment_request_id,
                    PaymentRequest.tenant_id == tenant.id,
                )
            )
        )
        if request is None:
            return _rejection("CHECKOUT_AUTHORITY_NOT_FOUND", status.HTTP_404_NOT_FOUND)
        payment = await session.scalar(
            locked(
                select(Payment).where(
                    Payment.tenant_id == tenant.id, Payment.payment_request_id == request.id
                )
            )
        )
        decision = await session.scalar(
            select(AuthorizationDecision)
            .where(
                AuthorizationDecision.tenant_id == tenant.id,
                AuthorizationDecision.payment_request_id == request.id,
            )
            .order_by(AuthorizationDecision.created_at.desc())
            .limit(1)
        )
        policy = await session.scalar(
            select(SpendingPolicy)
            .where(SpendingPolicy.tenant_id == tenant.id)
            .order_by(SpendingPolicy.version.desc())
            .limit(1)
        )
        if payment is None or decision is None or policy is None or payment.state != "AUTHORIZED":
            reason = "CHECKOUT_AUTHORITY_PAYMENT_NOT_AUTHORIZED"
            session.add(
                AuditEvent(
                    tenant_id=tenant.id,
                    payment_request_id=request.id,
                    payment_id=payment.id if payment is not None else None,
                    correlation_id=uuid4(),
                    event_kind="checkout_authority_rejected",
                    payload={"reason": reason, "payment_request_id": str(request.id)},
                )
            )
            return _rejection(reason, status.HTTP_409_CONFLICT)
        if policy.version != decision.policy_version or policy.expiry <= datetime.now(UTC):
            reason = "CHECKOUT_AUTHORITY_POLICY_DRIFT"
            session.add(
                AuditEvent(
                    tenant_id=tenant.id,
                    payment_request_id=request.id,
                    payment_id=payment.id if payment is not None else None,
                    correlation_id=uuid4(),
                    event_kind="checkout_authority_rejected",
                    payload={"reason": reason, "payment_request_id": str(request.id)},
                )
            )
            return _rejection(reason, status.HTTP_409_CONFLICT)
        if request.delegation_id is not None:
            # Authorization asked this chain once, and a human can revoke between then and now.
            # Issuing anyway would hand the provider adapter a permission slip for authority that
            # had already been withdrawn - the one thing revocation is supposed to prevent.
            try:
                await assert_chain_live(
                    session, tenant_id=tenant.id, delegation_id=request.delegation_id
                )
            except DelegationRefused as refused:
                correlation_id = uuid4()
                session.add(
                    AuditEvent(
                        tenant_id=tenant.id,
                        payment_request_id=request.id,
                        payment_id=payment.id,
                        delegation_id=request.delegation_id,
                        correlation_id=correlation_id,
                        event_kind="checkout_authority_rejected",
                        payload={
                            "reason": refused.reason,
                            "payment_request_id": str(request.id),
                            "delegation_id": str(request.delegation_id),
                        },
                    )
                )
                # This payment is holding a daily reservation and a delegation debit for money that
                # will now never move, and nothing sweeps a stranded AUTHORIZED payment. CANCELLED
                # returns both, through the single path that already enumerates the states a
                # payment dies in and is itself covered by a mutation - so this inherits that
                # enumeration rather than growing a second copy of it here.
                await transition(
                    session,
                    payment,
                    "CANCELLED",
                    reason=refused.reason,
                    correlation_id=correlation_id,
                )
                return _rejection(refused.reason, status.HTTP_409_CONFLICT)
        if (
            request.catalog_item_id is None
            or request.catalog_sku is None
            or request.catalog_name is None
            or request.merchant_display_name is None
            or request.quantity is None
            or request.purpose is None
        ):
            reason = "CHECKOUT_AUTHORITY_CATALOG_SNAPSHOT_REQUIRED"
            session.add(
                AuditEvent(
                    tenant_id=tenant.id,
                    payment_request_id=request.id,
                    payment_id=payment.id if payment is not None else None,
                    correlation_id=uuid4(),
                    event_kind="checkout_authority_rejected",
                    payload={"reason": reason, "payment_request_id": str(request.id)},
                )
            )
            return _rejection(reason, status.HTTP_409_CONFLICT)

        approval_id: UUID | None = None
        if decision.decision == "REQUIRE_APPROVAL":
            approval = await session.scalar(
                select(Approval)
                .where(
                    Approval.tenant_id == tenant.id,
                    Approval.payment_request_id == request.id,
                    Approval.consumed_at.is_not(None),
                )
                .order_by(Approval.consumed_at.desc())
                .limit(1)
            )
            if approval is None:
                reason = "CHECKOUT_AUTHORITY_APPROVAL_REQUIRED"
                session.add(
                    AuditEvent(
                        tenant_id=tenant.id,
                        payment_request_id=request.id,
                        payment_id=payment.id if payment is not None else None,
                        correlation_id=uuid4(),
                        event_kind="checkout_authority_rejected",
                        payload={"reason": reason, "payment_request_id": str(request.id)},
                    )
                )
                return _rejection(reason, status.HTTP_409_CONFLICT)
            approval_id = approval.id

        snapshot_hash = _snapshot_hash(request, decision.policy_version)
        existing = await session.scalar(
            select(CheckoutAuthority).where(
                CheckoutAuthority.tenant_id == tenant.id,
                CheckoutAuthority.payment_request_id == request.id,
            )
        )
        if existing is not None:
            if (
                existing.used_at is not None
                or existing.expires_at <= datetime.now(UTC)
                or existing.snapshot_hash != snapshot_hash
                or existing.policy_version != decision.policy_version
            ):
                return _rejection("CHECKOUT_AUTHORITY_UNAVAILABLE", status.HTTP_409_CONFLICT)
            return CheckoutAuthorityResponse(
                checkout_authority_id=existing.id,
                payment_request_id=existing.payment_request_id,
                payment_id=existing.payment_id,
                expires_at=existing.expires_at,
                snapshot_hash=existing.snapshot_hash,
            )

        authority = CheckoutAuthority(
            tenant_id=tenant.id,
            payment_request_id=request.id,
            payment_id=payment.id,
            approval_id=approval_id,
            policy_version=decision.policy_version,
            snapshot_hash=snapshot_hash,
            expires_at=min(policy.expiry, datetime.now(UTC) + _AUTHORITY_TTL),
        )
        session.add(authority)
        await session.flush()
        session.add(
            AuditEvent(
                tenant_id=tenant.id,
                payment_request_id=request.id,
                payment_id=payment.id,
                checkout_authority_id=authority.id,
                correlation_id=uuid4(),
                event_kind="checkout_authority_issued",
                payload={
                    "checkout_authority_id": str(authority.id),
                    "payment_request_id": str(request.id),
                    "snapshot_hash": snapshot_hash,
                    "policy_version": decision.policy_version,
                },
            )
        )
        return CheckoutAuthorityResponse(
            checkout_authority_id=authority.id,
            payment_request_id=request.id,
            payment_id=payment.id,
            expires_at=authority.expires_at,
            snapshot_hash=snapshot_hash,
        )
