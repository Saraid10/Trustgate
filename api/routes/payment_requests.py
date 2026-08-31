from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Annotated, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_session
from api.dependencies import require_tenant
from delegation.chain import DelegationRefused, active_delegation_for, spend
from models.domain import (
    AuditEvent,
    AuthorizationDecision,
    Merchant,
    Payment,
    PaymentRequest,
    Tenant,
)
from policy_engine.evaluate import (
    PolicyDecision,
    evaluate_payment_request,
    reserve_daily_spend,
)
from schemas.domain import PaymentRequestCreate, PaymentRequestDecision
from state_machine.transitions import transition

router = APIRouter(prefix="/api/v1/payment-requests", tags=["payment requests"])


@dataclass(frozen=True)
class CatalogPurchaseContext:
    catalog_item_id: UUID
    catalog_sku: str
    catalog_name: str
    merchant_display_name: str
    quantity: int
    purpose: str
    source: str = "MCP_AGENT"


def _same_request(
    existing: PaymentRequest,
    request: PaymentRequestCreate,
    catalog_context: CatalogPurchaseContext | None,
) -> bool:
    return (
        existing.actor_id == request.actor_id
        and existing.merchant_id == request.merchant_id
        and existing.amount_minor == request.amount_minor
        and existing.currency == request.currency
        and existing.order_ref == request.order_ref
        and existing.catalog_item_id
        == (catalog_context.catalog_item_id if catalog_context is not None else None)
        and existing.catalog_sku
        == (catalog_context.catalog_sku if catalog_context is not None else None)
        and existing.catalog_name
        == (catalog_context.catalog_name if catalog_context is not None else None)
        and existing.merchant_display_name
        == (catalog_context.merchant_display_name if catalog_context is not None else None)
        and existing.quantity == (catalog_context.quantity if catalog_context is not None else None)
        and existing.purpose == (catalog_context.purpose if catalog_context is not None else None)
        and existing.source == (catalog_context.source if catalog_context is not None else "API")
    )


def _response(
    payment_request: PaymentRequest, decision: AuthorizationDecision, payment: Payment
) -> PaymentRequestDecision:
    return PaymentRequestDecision(
        payment_request_id=payment_request.id,
        decision=cast(Literal["ALLOW", "DENY", "REQUIRE_APPROVAL"], decision.decision),
        reasons=decision.reasons,
        policy_version=decision.policy_version,
        correlation_id=decision.correlation_id,
        next_state=payment.state,
    )


async def create_payment_request_for_context(
    request: PaymentRequestCreate,
    tenant: Tenant,
    session: AsyncSession,
    *,
    catalog_context: CatalogPurchaseContext | None = None,
) -> PaymentRequestDecision | JSONResponse:
    """Persist one decision per tenant-scoped idempotency key."""

    transaction = session.begin_nested() if session.in_transaction() else session.begin()
    async with transaction:
        idempotency_lock = int.from_bytes(
            hashlib.sha256(f"{tenant.id}:{request.idempotency_key}".encode()).digest()[:8],
            byteorder="big",
            signed=True,
        )
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": idempotency_lock}
        )
        merchant = await session.scalar(
            select(Merchant).where(
                Merchant.id == request.merchant_id, Merchant.tenant_id == tenant.id
            )
        )
        if merchant is None:
            correlation_id = uuid4()
            session.add(
                AuditEvent(
                    tenant_id=tenant.id,
                    correlation_id=correlation_id,
                    event_kind="payment_request_rejected",
                    payload={
                        "reason": "CROSS_TENANT_ACCESS_DENIED",
                        "merchant_id": str(request.merchant_id),
                    },
                )
            )
            await session.flush()
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "CROSS_TENANT_ACCESS_DENIED"},
            )

        existing = await session.scalar(
            select(PaymentRequest).where(
                PaymentRequest.tenant_id == tenant.id,
                PaymentRequest.idempotency_key == request.idempotency_key,
            )
        )
        if existing is not None:
            decision = await session.scalar(
                select(AuthorizationDecision)
                .where(
                    AuthorizationDecision.tenant_id == tenant.id,
                    AuthorizationDecision.payment_request_id == existing.id,
                )
                .order_by(AuthorizationDecision.created_at.desc())
                .limit(1)
            )
            payment = await session.scalar(
                select(Payment).where(
                    Payment.tenant_id == tenant.id, Payment.payment_request_id == existing.id
                )
            )
            if decision is None or payment is None:
                raise RuntimeError("Idempotent payment request is missing its decision or payment.")
            if _same_request(existing, request, catalog_context):
                return _response(existing, decision, payment)
            session.add(
                AuditEvent(
                    tenant_id=tenant.id,
                    payment_request_id=existing.id,
                    payment_id=payment.id,
                    correlation_id=decision.correlation_id,
                    event_kind="idempotency_key_collision",
                    payload={
                        "reason": "IDEMPOTENCY_KEY_REPLAYED",
                        "payment_request_id": str(existing.id),
                    },
                )
            )
            await session.flush()
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content=_response(existing, decision, payment).model_dump(mode="json"),
            )

        result = await evaluate_payment_request(
            session,
            tenant_id=tenant.id,
            actor_id=request.actor_id,
            merchant_id=request.merchant_id,
            amount_minor=request.amount_minor,
            currency=request.currency,
        )
        correlation_id = uuid4()
        # Generated before either budget is claimed. The delegation spend is keyed by the request
        # it authorizes, so a retry charges once and the evidence joins this payment's timeline.
        request_id = uuid4()

        held = await active_delegation_for(session, tenant_id=tenant.id, actor_id=request.actor_id)
        # Set only where the spend below succeeded, so it records what was *debited* rather than
        # what the actor happened to hold. Checkout re-asks this chain before money moves, and a
        # request that was refused must not hand it one to re-ask.
        spent_delegation_id: UUID | None = None

        if result.decision != "DENY":
            # Both budgets inside one savepoint. Claiming the daily reservation and then refusing
            # on the delegation would leave it moved for a payment that never happens - and the
            # release path fires on a transition out of a holding state, which a request denied
            # here never enters. Doing the delegation first only mirrors the leak. Either both
            # hold or neither does, and the order stops mattering.
            claim = await session.begin_nested()
            refusal: str | None = None
            try:
                reserved = await reserve_daily_spend(
                    session,
                    tenant_id=tenant.id,
                    actor_id=request.actor_id,
                    amount_minor=request.amount_minor,
                    policy_version=result.policy_version,
                )
                if not reserved:
                    refusal = "DAILY_LIMIT_EXCEEDED"
                elif held is not None:
                    sku = catalog_context.catalog_sku if catalog_context else None
                    if sku is None:
                        # A delegation is scoped by SKU. A request carrying none cannot be checked
                        # against that scope, so it is refused rather than quietly exempted.
                        refusal = "DELEGATION_REQUIRES_A_CATALOG_SKU"
                    else:
                        await spend(
                            session,
                            tenant_id=tenant.id,
                            delegation_id=held.id,
                            amount_minor=request.amount_minor,
                            sku=sku,
                            reference=request_id,
                            correlation_id=correlation_id,
                        )
                        spent_delegation_id = held.id
            except DelegationRefused as refused:
                refusal = refused.reason

            if refusal is None:
                await claim.commit()
            else:
                # Nothing to unset. The spend below is the last thing that can happen inside the
                # savepoint, so reaching here means it either never ran or raised, and either way
                # `spent_delegation_id` was never assigned. A clearing line here reads like care
                # and is unreachable - the mutation suite said so by surviving its removal. If a
                # third budget is ever added after the spend, that stops being true.
                await claim.rollback()
                result = PolicyDecision(
                    decision="DENY",
                    reasons=[refusal],
                    policy_version=result.policy_version,
                )

        payment_request = PaymentRequest(
            id=request_id,
            tenant_id=tenant.id,
            catalog_item_id=catalog_context.catalog_item_id if catalog_context else None,
            catalog_sku=catalog_context.catalog_sku if catalog_context else None,
            catalog_name=catalog_context.catalog_name if catalog_context else None,
            merchant_display_name=(
                catalog_context.merchant_display_name if catalog_context else None
            ),
            quantity=catalog_context.quantity if catalog_context else None,
            purpose=catalog_context.purpose if catalog_context else None,
            source=catalog_context.source if catalog_context else "API",
            delegation_id=spent_delegation_id,
            **request.model_dump(),
        )
        session.add(payment_request)
        await session.flush()
        if spent_delegation_id is not None:
            # The spend's audit row could not carry this when it was written: the spend happens
            # inside the budget savepoint, and the request it names does not exist until the flush
            # above. Writing the foreign key then would have refused the insert.
            #
            # Narrow on purpose. This correlation was generated for this authorization and nothing
            # else writes under it, so the update reaches exactly the events this spend produced -
            # and `payment_request_id IS NULL` means a re-entry could not steal another purchase's
            # evidence even if that stopped being true.
            await session.execute(
                update(AuditEvent)
                .where(
                    AuditEvent.tenant_id == tenant.id,
                    AuditEvent.delegation_id == spent_delegation_id,
                    AuditEvent.correlation_id == correlation_id,
                    AuditEvent.payment_request_id.is_(None),
                )
                .values(payment_request_id=payment_request.id)
            )
        payment = Payment(
            tenant_id=tenant.id,
            payment_request_id=payment_request.id,
            state="CREATED",
            authorized_amount_minor=request.amount_minor if result.decision == "ALLOW" else None,
            captured_amount_minor=0,
            refunded_amount_minor=0,
        )
        decision = AuthorizationDecision(
            tenant_id=tenant.id,
            payment_request_id=payment_request.id,
            decision=result.decision,
            reasons=result.reasons,
            policy_version=result.policy_version,
            correlation_id=correlation_id,
        )
        session.add_all([payment, decision])
        await session.flush()

        target_state = {
            "ALLOW": "AUTHORIZED",
            "DENY": "DENIED",
            "REQUIRE_APPROVAL": "APPROVAL_REQUIRED",
        }[result.decision]
        await transition(
            session,
            payment,
            target_state,
            reason=result.reasons[0] if result.reasons else "POLICY_ALLOWED",
            correlation_id=correlation_id,
        )
        return _response(payment_request, decision, payment)


@router.post("", response_model=PaymentRequestDecision, status_code=status.HTTP_201_CREATED)
async def create_payment_request(
    request: PaymentRequestCreate,
    tenant: Annotated[Tenant, Depends(require_tenant)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PaymentRequestDecision | JSONResponse:
    """Create a legacy API payment request with API provenance."""

    if os.getenv("ENABLE_LEGACY_PAYMENT_REQUEST_API") != "true":
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": "not found"})
    return await create_payment_request_for_context(request, tenant, session)
