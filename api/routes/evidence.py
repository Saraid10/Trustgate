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
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_session
from api.dependencies import require_tenant
from api.receipt import render_receipt
from delegation.chain import DelegationRefused, resolve_chain
from models.domain import (
    Approval,
    AuditEvent,
    AuthorizationDecision,
    CheckoutAuthority,
    Delegation,
    DelegationSpend,
    Payment,
    PaymentRequest,
    ProviderEvent,
    RazorpayOrder,
    SpendingPolicy,
    Tenant,
)
from schemas.domain import (
    AuthorizationEnvelope,
    EvidenceApproval,
    EvidenceAuditEntry,
    EvidenceAuthority,
    EvidenceDecision,
    EvidenceDelegation,
    EvidenceDelegationHop,
    EvidenceDerivedFacts,
    EvidencePayment,
    EvidencePolicy,
    EvidenceProposal,
    EvidenceProviderEvent,
    EvidenceProviderOrder,
    PaymentRequestEvidence,
)

router = APIRouter(prefix="/api/v1/payment-requests", tags=["evidence"])

# States a payment reaches only by going through the provider. Reaching one is the opposite of
# never having been authorized, and the envelope has to tell a reader which of the two happened.
_SETTLED_STATES = frozenset({"CAPTURED", "REFUNDED", "PARTIALLY_REFUNDED"})


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
    # Correlation ids cover requests, not purchases: later lifecycle work deliberately receives a
    # fresh one. Audit rows therefore carry durable record references instead of making the
    # receipt depend on JSON payload conventions maintained by every future writer.
    matches_this_purchase = [AuditEvent.payment_request_id == request.id]
    if payment is not None:
        matches_this_purchase.append(AuditEvent.payment_id == payment.id)
    if authority is not None:
        matches_this_purchase.append(AuditEvent.checkout_authority_id == authority.id)
    if provider_order is not None:
        matches_this_purchase.append(AuditEvent.provider_order_id == provider_order.id)

    audit_events = list(
        await session.scalars(
            select(AuditEvent)
            .where(AuditEvent.tenant_id == tenant.id, or_(*matches_this_purchase))
            .order_by(AuditEvent.created_at)
        )
    )

    # The chain is read here rather than matched through the audit trail, because a hop's numbers
    # move after the purchase - a later grant takes allocation, a later spend takes budget - and a
    # receipt that showed the chain as it was would be a snapshot this project does not keep. Every
    # other figure in this record is read live too.
    delegation_chain: list[Delegation] = []
    delegation_spend: DelegationSpend | None = None
    if request.delegation_id is not None:
        try:
            delegation_chain = await resolve_chain(
                session, tenant_id=tenant.id, delegation_id=request.delegation_id
            )
        except DelegationRefused:
            # The foreign key says the hop is there, so this is a chain broken above it. Reporting
            # no delegation section is wrong and raising loses the whole receipt; the audit trail
            # below still carries the spend.
            delegation_chain = []
        delegation_spend = await session.scalar(
            select(DelegationSpend).where(
                DelegationSpend.tenant_id == tenant.id,
                DelegationSpend.reference == request.id,
            )
        )

    # A refusal recorded against the chain rather than against the policy. An authorization refused
    # on a delegation never reaches here - it sets no `delegation_id`, and its reason is already in
    # `decision.reasons` - so this is specifically the case where authority died between being
    # granted and being spent.
    delegation_refusal = next(
        (
            str(event.payload["reason"])
            for event in reversed(audit_events)
            if event.event_kind == "checkout_authority_rejected"
            and event.delegation_id is not None
            and isinstance(event.payload, dict)
            and str(event.payload.get("reason", "")).startswith("DELEGATION_")
        ),
        None,
    )

    now = datetime.now(UTC)
    if decision is None:
        approval_state = "UNKNOWN"
    elif decision.decision != "REQUIRE_APPROVAL":
        approval_state = "NOT_REQUIRED"
    elif approval is None:
        approval_state = "REQUIRED"
    elif approval.consumed_at is not None:
        approval_state = "CONSUMED"
    elif approval.expires_at <= now:
        approval_state = "EXPIRED"
    else:
        approval_state = "GRANTED"

    # Ordered the way the real gate checks, so the reason a reader sees is the reason they would
    # get. It is still a description of stored rows rather than a verdict: `consume_checkout_
    # authority` re-runs all of this under row locks, and it is the one that decides.
    blocked: str | None = None
    if payment is None:
        blocked = "PAYMENT_NOT_AUTHORIZED"
    elif payment.state in _SETTLED_STATES:
        # Not "never authorized". A captured payment was authorized *and* paid, and saying
        # otherwise next to a row reading CAPTURED is the panel contradicting the table beneath it.
        # No further provider action is allowed here because the money already moved.
        blocked = "PAYMENT_ALREADY_SETTLED"
    elif payment.state == "PROVIDER_PENDING":
        blocked = "PAYMENT_ALREADY_WITH_THE_PROVIDER"
    elif payment.state != "AUTHORIZED":
        blocked = "PAYMENT_NOT_AUTHORIZED"
    elif authority is None:
        blocked = "NO_CHECKOUT_AUTHORITY_ISSUED"
    elif authority.used_at is not None:
        blocked = "CHECKOUT_AUTHORITY_ALREADY_USED"
    elif authority.expires_at <= now:
        blocked = "CHECKOUT_AUTHORITY_EXPIRED"
    elif any(hop.revoked_at is not None for hop in delegation_chain):
        blocked = "DELEGATION_REVOKED"
    elif any(hop.expires_at <= now for hop in delegation_chain):
        blocked = "DELEGATION_EXPIRED"

    return PaymentRequestEvidence(
        envelope=AuthorizationEnvelope(
            payment_request_id=request.id,
            decision=decision.decision if decision is not None else None,  # type: ignore[arg-type]
            reason_codes=list(decision.reasons) if decision is not None else [],
            merchant_id=request.merchant_id,
            merchant_display_name=request.merchant_display_name,
            amount_minor=request.amount_minor,
            currency=request.currency,
            policy_version=decision.policy_version if decision is not None else None,
            approval_state=approval_state,  # type: ignore[arg-type]
            delegation_id=request.delegation_id,
            delegation_root_actor_id=(
                delegation_chain[0].root_actor_id if delegation_chain else None
            ),
            authority_expires_at=authority.expires_at if authority is not None else None,
            provider_action_allowed=blocked is None,
            provider_action_blocked_reason=blocked,
        ),
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
        delegation=(
            EvidenceDelegation(
                root_actor_id=delegation_chain[0].root_actor_id,
                chain=[
                    EvidenceDelegationHop(
                        delegation_id=hop.id,
                        depth=hop.depth,
                        delegator_actor_id=hop.delegator_actor_id,
                        delegate_actor_id=hop.delegate_actor_id,
                        budget_minor=hop.budget_minor,
                        allocated_minor=hop.allocated_minor,
                        spent_minor=hop.spent_minor,
                        remaining_minor=hop.budget_minor - hop.allocated_minor - hop.spent_minor,
                        max_amount_minor=hop.max_amount_minor,
                        allowed_skus=list(hop.allowed_skus),
                        purpose=hop.purpose,
                        expires_at=hop.expires_at,
                        revoked_at=hop.revoked_at,
                    )
                    for hop in delegation_chain
                ],
                spent_minor=delegation_spend.amount_minor if delegation_spend else 0,
                spent_sku=delegation_spend.sku if delegation_spend else None,
                released_at=delegation_spend.released_at if delegation_spend else None,
                refusal_reason=delegation_refusal,
            )
            if delegation_chain
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
                provider_state=provider_order.provider_state,
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
