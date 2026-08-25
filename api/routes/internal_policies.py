from __future__ import annotations

import os
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_session
from models.domain import Merchant, PolicyMerchant, SpendingPolicy, Tenant
from models.locking import locked
from schemas.domain import InternalPolicyCreate, SpendingPolicySchema

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/policies", response_model=SpendingPolicySchema, status_code=status.HTTP_201_CREATED)
async def create_policy(
    request: InternalPolicyCreate,
    x_internal_admin_token: Annotated[str, Header()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SpendingPolicy:
    expected_token = os.getenv("INTERNAL_ADMIN_TOKEN")
    if not expected_token or not secrets.compare_digest(x_internal_admin_token, expected_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="internal token required")
    transaction = session.begin_nested() if session.in_transaction() else session.begin()
    async with transaction:
        tenant = await session.scalar(locked(select(Tenant).where(Tenant.id == request.tenant_id)))
        if tenant is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown tenant")
        merchants = list(
            await session.scalars(
                select(Merchant).where(
                    Merchant.tenant_id == request.tenant_id,
                    Merchant.id.in_(request.allowed_merchant_ids),
                )
            )
        )
        if len(merchants) != len(set(request.allowed_merchant_ids)):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid merchant"
            )
        next_version = (
            int(
                (
                    await session.scalar(
                        select(func.coalesce(func.max(SpendingPolicy.version), 0)).where(
                            SpendingPolicy.tenant_id == request.tenant_id
                        )
                    )
                )
                or 0
            )
            + 1
        )
        policy = SpendingPolicy(
            tenant_id=request.tenant_id,
            version=next_version,
            max_amount_minor=request.max_amount_minor,
            currency=request.currency,
            max_daily_spend_minor=request.max_daily_spend_minor,
            expiry=request.expiry,
            approval_required_above_minor=request.approval_required_above_minor,
        )
        session.add(policy)
        await session.flush()
        session.add_all(
            [
                PolicyMerchant(
                    tenant_id=request.tenant_id, policy_id=policy.id, merchant_id=merchant.id
                )
                for merchant in merchants
            ]
        )
        await session.flush()
        return policy
