from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Annotated, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_session
from api.dependencies import require_tenant
from models.domain import (
    AuditEvent,
    AuthorizationDecision,
    Merchant,
    Payment,
    PaymentRequest,
    Tenant,
)
from policy_engine.evaluate import PolicyDecision, evaluate_payment_request, reserve_daily_spend
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
        if result.decision != "DENY":
            reserved = await reserve_daily_spend(
                session,
                tenant_id=tenant.id,
                actor_id=request.actor_id,
                amount_minor=request.amount_minor,
                policy_version=result.policy_version,
            )
            if not reserved:
                result = PolicyDecision(
                    decision="DENY",
                    reasons=["DAILY_LIMIT_EXCEEDED"],
                    policy_version=result.policy_version,
                )
        correlation_id = uuid4()
        payment_request = PaymentRequest(
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
            **request.model_dump(),
        )
        session.add(payment_request)
        await session.flush()
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
