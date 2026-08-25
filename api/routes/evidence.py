"""Assemble the tenant-scoped evidence record for one purchase attempt.

The receipt separates three stages that the project keeps deliberately apart: what the agent
proposed, what the server derived and authorized, and what the provider did. Reading them side by
side is what makes the authority boundary visible rather than merely asserted.

Every query filters by the tenant resolved from the trusted request dependency. A request that
belongs to another tenant is not found rather than refused, so the endpoint never confirms that an
identifier exists elsewhere.

An attack rejected before anything is persisted has no receipt at all, because it created no
payment request to key one on. That absence is the safety property, and the audit trail is its
record.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_session
from api.dependencies import require_tenant
from api.receipt import render_receipt
from models.domain import (
    Approval,
    AuditEvent,
    AuthorizationDecision,
    CheckoutAuthority,
    Payment,
    PaymentRequest,
    ProviderEvent,
    RazorpayOrder,
    SpendingPolicy,
    Tenant,
)
from schemas.domain import (
    EvidenceApproval,
    EvidenceAuditEntry,
    EvidenceAuthority,
    EvidenceDecision,
    EvidenceDerivedFacts,
    EvidencePayment,
    EvidencePolicy,
    EvidenceProposal,
    EvidenceProviderEvent,
    EvidenceProviderOrder,
    PaymentRequestEvidence,
)

router = APIRouter(prefix="/api/v1/payment-requests", tags=["evidence"])


async def build_payment_request_evidence(
    session: AsyncSession, *, tenant: Tenant, payment_request_id: UUID
) -> PaymentRequestEvidence:
    """Assemble the evidence record once, so every rendering shows the same facts.

    The JSON endpoint and the HTML receipt both call this. Assembling separately for each would let
    the two drift, and an evidence artifact that disagrees with itself is worse than none.
    """

    request = await session.scalar(
        select(PaymentRequest).where(
            PaymentRequest.id == payment_request_id,
            PaymentRequest.tenant_id == tenant.id,
        )
    )
    if request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="PAYMENT_REQUEST_NOT_FOUND"
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
    policy = (
        await session.scalar(
            select(SpendingPolicy).where(
                SpendingPolicy.tenant_id == tenant.id,
                SpendingPolicy.version == decision.policy_version,
            )
        )
        if decision is not None
        else None
    )
    approval = await session.scalar(
        select(Approval)
        .where(Approval.tenant_id == tenant.id, Approval.payment_request_id == request.id)
        .order_by(Approval.expires_at.desc())
        .limit(1)
    )
    authority = await session.scalar(
        select(CheckoutAuthority).where(
            CheckoutAuthority.tenant_id == tenant.id,
            CheckoutAuthority.payment_request_id == request.id,
        )
    )
    payment = await session.scalar(
        select(Payment).where(
            Payment.tenant_id == tenant.id, Payment.payment_request_id == request.id
        )
    )
    provider_order = (
        await session.scalar(
            select(RazorpayOrder).where(
                RazorpayOrder.tenant_id == tenant.id,
                RazorpayOrder.payment_id == payment.id,
            )
        )
        if payment is not None
        else None
    )
    provider_events = (
        list(
            await session.scalars(
                select(ProviderEvent)
                .where(
                    ProviderEvent.tenant_id == tenant.id,
                    ProviderEvent.payment_id == payment.id,
                )
                .order_by(ProviderEvent.received_at)
            )
        )
        if payment is not None
        else []
    )
    correlation_ids = {decision.correlation_id} if decision is not None else set()
    audit_events = (
        list(
            await session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.tenant_id == tenant.id,
                    AuditEvent.correlation_id.in_(correlation_ids),
                )
                .order_by(AuditEvent.created_at)
            )
        )
        if correlation_ids
        else []
    )

    return PaymentRequestEvidence(
        payment_request_id=request.id,
        tenant_id=tenant.id,
        generated_at=datetime.now(UTC),
        proposed=EvidenceProposal(
            sku=request.catalog_sku,
            quantity=request.quantity,
            purpose=request.purpose,
            actor_id=request.actor_id,
            source=request.source,
            idempotency_key=request.idempotency_key,
            requested_at=request.created_at,
        ),
        derived=EvidenceDerivedFacts(
            merchant_id=request.merchant_id,
            merchant_display_name=request.merchant_display_name,
            catalog_item_id=request.catalog_item_id,
            catalog_name=request.catalog_name,
            amount_minor=request.amount_minor,
            currency=request.currency,
            order_ref=request.order_ref,
            request_revision=request.request_revision,
        ),
        policy=(
            EvidencePolicy(
                version=policy.version,
                currency=policy.currency,
                max_amount_minor=policy.max_amount_minor,
                max_daily_spend_minor=policy.max_daily_spend_minor,
                approval_required_above_minor=policy.approval_required_above_minor,
                expiry=policy.expiry,
            )
            if policy is not None
            else None
        ),
        decision=(
            EvidenceDecision(
                decision=decision.decision,  # type: ignore[arg-type]
                reasons=list(decision.reasons),
                policy_version=decision.policy_version,
                correlation_id=decision.correlation_id,
                decided_at=decision.created_at,
            )
            if decision is not None
            else None
        ),
        approval=(
            EvidenceApproval(
                approval_id=approval.id,
                granted_by=approval.granted_by,
                policy_version=approval.policy_version,
                expires_at=approval.expires_at,
                consumed_at=approval.consumed_at,
            )
            if approval is not None
            else None
        ),
        authority=(
            EvidenceAuthority(
                checkout_authority_id=authority.id,
                snapshot_hash=authority.snapshot_hash,
                policy_version=authority.policy_version,
                approval_id=authority.approval_id,
                expires_at=authority.expires_at,
                used_at=authority.used_at,
            )
            if authority is not None
            else None
        ),
        payment=(
            EvidencePayment(
                payment_id=payment.id,
                state=payment.state,
                authorized_amount_minor=payment.authorized_amount_minor,
                captured_amount_minor=payment.captured_amount_minor,
                refunded_amount_minor=payment.refunded_amount_minor,
                updated_at=payment.updated_at,
            )
            if payment is not None
            else None
        ),
        provider_order=(
            EvidenceProviderOrder(
                razorpay_order_id=provider_order.razorpay_order_id,
                amount_minor=provider_order.amount_minor,
                currency=provider_order.currency,
                receipt=provider_order.receipt,
                created_at=provider_order.created_at,
            )
            if provider_order is not None
            else None
        ),
        provider_events=[
            EvidenceProviderEvent(
                provider_event_id=event.provider_event_id,
                event_type=event.event_type,
                received_at=event.received_at,
                processed_at=event.processed_at,
            )
            for event in provider_events
        ],
        audit_trail=[
            EvidenceAuditEntry(
                event_kind=event.event_kind,
                correlation_id=event.correlation_id,
                created_at=event.created_at,
            )
            for event in audit_events
        ],
    )


@router.get("/{payment_request_id}/evidence", response_model=PaymentRequestEvidence)
async def read_payment_request_evidence(
    payment_request_id: UUID,
    tenant: Annotated[Tenant, Depends(require_tenant)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PaymentRequestEvidence:
    """Return what was proposed, what was authorized, and what the provider did."""

    return await build_payment_request_evidence(
        session, tenant=tenant, payment_request_id=payment_request_id
    )


@router.get("/{payment_request_id}/receipt", response_class=HTMLResponse)
async def read_payment_request_receipt(
    payment_request_id: UUID,
    tenant: Annotated[Tenant, Depends(require_tenant)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    """Render the same evidence as a readable receipt.

    This is a rendering of the JSON record, not a second assembly of it, so the two cannot disagree
    about what happened.
    """

    evidence = await build_payment_request_evidence(
        session, tenant=tenant, payment_request_id=payment_request_id
    )
    return HTMLResponse(content=render_receipt(evidence))
