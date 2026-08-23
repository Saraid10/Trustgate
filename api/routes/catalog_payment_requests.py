from __future__ import annotations

import os
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_session
from api.dependencies import require_tenant
from api.routes.payment_requests import CatalogPurchaseContext, create_payment_request_for_context
from models.domain import AuditEvent, CatalogItem, Merchant, Tenant
from schemas.domain import (
    CatalogPaymentRequestCreate,
    CatalogPaymentRequestDecision,
    PaymentRequestCreate,
)

router = APIRouter(prefix="/api/v1/catalog-payment-requests", tags=["catalog payment requests"])


def _rejection(reason: str) -> JSONResponse:
    status_code = (
        status.HTTP_404_NOT_FOUND
        if reason == "CATALOG_ITEM_NOT_AVAILABLE"
        else status.HTTP_422_UNPROCESSABLE_CONTENT
    )
    return JSONResponse(status_code=status_code, content={"detail": reason})


async def create_catalog_payment_request_for_source(
    request: CatalogPaymentRequestCreate,
    tenant: Tenant,
    session: AsyncSession,
    *,
    actor_id: str,
    source: str,
) -> CatalogPaymentRequestDecision | JSONResponse:
    """Create a request using tenant-scoped catalog facts, never agent-supplied payment values."""

    transaction = session.begin_nested() if session.in_transaction() else session.begin()
    async with transaction:
        row = await session.execute(
            select(CatalogItem, Merchant.name)
            .join(
                Merchant,
                (Merchant.id == CatalogItem.merchant_id)
                & (Merchant.tenant_id == CatalogItem.tenant_id),
            )
            .where(
                CatalogItem.tenant_id == tenant.id,
                CatalogItem.sku == request.sku,
                CatalogItem.active.is_(True),
            )
        )
        catalog_item, merchant_name = row.one_or_none() or (None, None)
        if catalog_item is None:
            reason = "CATALOG_ITEM_NOT_AVAILABLE"
            session.add(
                AuditEvent(
                    tenant_id=tenant.id,
                    correlation_id=uuid4(),
                    event_kind="catalog_purchase_rejected",
                    payload={"reason": reason, "sku": request.sku},
                )
            )
            await session.flush()
            return _rejection(reason)
        if merchant_name is None:
            raise RuntimeError("Active catalog item is missing its tenant-scoped merchant.")
        if request.quantity > catalog_item.max_quantity:
            reason = "QUANTITY_EXCEEDS_LIMIT"
            session.add(
                AuditEvent(
                    tenant_id=tenant.id,
                    correlation_id=uuid4(),
                    event_kind="catalog_purchase_rejected",
                    payload={
                        "reason": reason,
                        "sku": catalog_item.sku,
                        "requested_quantity": request.quantity,
                        "max_quantity": catalog_item.max_quantity,
                    },
                )
            )
            await session.flush()
            return _rejection(reason)

        result = await create_payment_request_for_context(
            PaymentRequestCreate(
                actor_id=actor_id,
                merchant_id=catalog_item.merchant_id,
                amount_minor=catalog_item.price_minor * request.quantity,
                currency=catalog_item.currency,
                order_ref=f"catalog:{catalog_item.sku}",
                idempotency_key=request.idempotency_key,
            ),
            tenant,
            session,
            catalog_context=CatalogPurchaseContext(
                catalog_item_id=catalog_item.id,
                catalog_sku=catalog_item.sku,
                catalog_name=catalog_item.name,
                merchant_display_name=merchant_name,
                quantity=request.quantity,
                purpose=request.purpose,
                source=source,
            ),
        )
        if isinstance(result, JSONResponse):
            return result
        return CatalogPaymentRequestDecision(
            **result.model_dump(),
            sku=catalog_item.sku,
            quantity=request.quantity,
            purpose=request.purpose,
            merchant_display_name=merchant_name,
            amount_minor=catalog_item.price_minor * request.quantity,
            currency=catalog_item.currency,
        )


@router.post("", response_model=CatalogPaymentRequestDecision, status_code=status.HTTP_201_CREATED)
async def create_catalog_payment_request(
    request: CatalogPaymentRequestCreate,
    tenant: Annotated[Tenant, Depends(require_tenant)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CatalogPaymentRequestDecision | JSONResponse:
    """Create a public API request using catalog-derived payment facts."""

    api_actor_id = os.getenv("TRUSTGATE_API_ACTOR_ID")
    if not api_actor_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="api actor unavailable"
        )
    return await create_catalog_payment_request_for_source(
        request, tenant, session, actor_id=api_actor_id, source="API"
    )
