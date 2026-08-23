from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_session
from api.dependencies import require_tenant
from models.domain import (
    Approval,
    AuthorizationDecision,
    Payment,
    PaymentRequest,
    SpendingPolicy,
    Tenant,
)
from schemas.domain import ApprovalGrantResponse
from state_machine.transitions import transition

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])


@router.post("/{payment_request_id}/grant", response_model=ApprovalGrantResponse)
async def grant_approval(
    payment_request_id: UUID,
    tenant: Annotated[Tenant, Depends(require_tenant)],
    x_approver_token: Annotated[str, Header()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApprovalGrantResponse:
    expected_token = os.getenv("DEMO_APPROVER_TOKEN")
    approver_id = os.getenv("DEMO_APPROVER_ID")
    if not expected_token or not approver_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="approver unavailable"
        )
    if not secrets.compare_digest(x_approver_token, expected_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="approver token required")
    transaction = session.begin_nested() if session.in_transaction() else session.begin()
    async with transaction:
        payment_request = await session.scalar(
            select(PaymentRequest).where(
                PaymentRequest.id == payment_request_id, PaymentRequest.tenant_id == tenant.id
            )
        )
        if payment_request is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="APPROVAL_NOT_FOUND")
        payment = await session.scalar(
            select(Payment)
            .where(Payment.payment_request_id == payment_request.id, Payment.tenant_id == tenant.id)
            .with_for_update()
        )
        if payment is None or payment.state != "APPROVAL_REQUIRED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="APPROVAL_NOT_REQUIRED"
            )
        existing = await session.scalar(
            select(Approval).where(
                Approval.tenant_id == tenant.id,
                Approval.payment_request_id == payment_request.id,
                Approval.consumed_at.is_(None),
            )
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="APPROVAL_ALREADY_CONSUMED"
            )
        decision = await session.scalar(
            select(AuthorizationDecision)
            .where(
                AuthorizationDecision.tenant_id == tenant.id,
                AuthorizationDecision.payment_request_id == payment_request.id,
            )
            .order_by(AuthorizationDecision.created_at.desc())
            .limit(1)
        )
        policy = await session.scalar(
            select(SpendingPolicy).where(
                SpendingPolicy.tenant_id == tenant.id,
                SpendingPolicy.version == (decision.policy_version if decision else -1),
            )
        )
        if decision is None or policy is None or policy.expiry <= datetime.now(UTC):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="APPROVAL_EXPIRED")
        approval = Approval(
            tenant_id=tenant.id,
            payment_request_id=payment_request.id,
            policy_version=decision.policy_version,
            granted_by=approver_id,
            expires_at=policy.expiry,
        )
        session.add(approval)
        await session.flush()
        await transition(
            session,
            payment,
            "AUTHORIZED",
            reason="human_approval_granted",
            correlation_id=uuid4(),
            approval_id=approval.id,
        )
        return ApprovalGrantResponse(
            approval_id=approval.id,
            payment_request_id=payment_request.id,
            policy_version=approval.policy_version,
            expires_at=approval.expires_at,
        )
