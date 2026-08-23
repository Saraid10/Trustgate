"""Razorpay Test Mode order creation and callback verification.

The browser receives only the public key ID and provider order ID. It never supplies
purchase facts, and callback verification always uses the order ID stored here.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Annotated
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_session
from api.dependencies import require_tenant
from api.routes.checkout_authorities import (
    CheckoutAuthorityUnavailableError,
    consume_checkout_authority,
)
from models.domain import AuditEvent, PaymentRequest, RazorpayOrder, Tenant
from schemas.domain import RazorpayCallback, RazorpayOrderResponse

router = APIRouter(prefix="/api/v1/razorpay", tags=["razorpay test mode"])
_ORDERS_URL = "https://api.razorpay.com/v1/orders"


def _credentials() -> tuple[str, str]:
    key_id = os.getenv("RAZORPAY_KEY_ID")
    key_secret = os.getenv("RAZORPAY_KEY_SECRET")
    if not key_id or not key_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="RAZORPAY_UNAVAILABLE"
        )
    return key_id, key_secret


async def _create_razorpay_order(
    *,
    key_id: str,
    key_secret: str,
    amount_minor: int,
    currency: str,
    receipt: str,
    notes: dict[str, str],
) -> str:
    """Create exactly one Test Mode order using server-held Basic Auth credentials."""

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                os.getenv("RAZORPAY_ORDERS_URL", _ORDERS_URL),
                auth=(key_id, key_secret),
                json={
                    "amount": amount_minor,
                    "currency": currency,
                    "receipt": receipt,
                    "notes": notes,
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="RAZORPAY_NETWORK_ERROR",
        ) from exc
    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="RAZORPAY_ORDER_REJECTED"
        )
    try:
        order_id = response.json().get("id")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="RAZORPAY_ORDER_INVALID",
        ) from exc
    if not isinstance(order_id, str) or not order_id:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="RAZORPAY_ORDER_INVALID"
        )
    return order_id


def _order_response(order: RazorpayOrder, key_id: str) -> RazorpayOrderResponse:
    return RazorpayOrderResponse(
        checkout_authority_id=order.checkout_authority_id,
        razorpay_key_id=key_id,
        razorpay_order_id=order.razorpay_order_id,
        amount_minor=order.amount_minor,
        currency=order.currency,
    )


@router.post(
    "/checkout-authorities/{checkout_authority_id}/orders",
    response_model=RazorpayOrderResponse,
)
async def create_order(
    checkout_authority_id: UUID,
    tenant: Annotated[Tenant, Depends(require_tenant)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RazorpayOrderResponse:
    """Claim one authority, then create a provider order from its stored catalog snapshot."""

    key_id, key_secret = _credentials()
    existing = await session.scalar(
        select(RazorpayOrder).where(
            RazorpayOrder.tenant_id == tenant.id,
            RazorpayOrder.checkout_authority_id == checkout_authority_id,
        )
    )
    if existing is not None:
        return _order_response(existing, key_id)
    correlation_id = uuid4()
    try:
        authority = await consume_checkout_authority(
            session,
            tenant_id=tenant.id,
            checkout_authority_id=checkout_authority_id,
            correlation_id=correlation_id,
        )
    except CheckoutAuthorityUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.reason) from exc
    request = await session.scalar(
        select(PaymentRequest).where(
            PaymentRequest.tenant_id == tenant.id,
            PaymentRequest.id == authority.payment_request_id,
        )
    )
    if request is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="CHECKOUT_AUTHORITY_UNAVAILABLE"
        )
    receipt = f"tg_{authority.id.hex}"
    try:
        razorpay_order_id = await _create_razorpay_order(
            key_id=key_id,
            key_secret=key_secret,
            amount_minor=request.amount_minor,
            currency=request.currency,
            receipt=receipt,
            notes={
                "payment_id": str(authority.payment_id),
                "snapshot_hash": authority.snapshot_hash,
            },
        )
    except HTTPException:
        session.add(
            AuditEvent(
                tenant_id=tenant.id,
                correlation_id=correlation_id,
                event_kind="razorpay_order_creation_failed",
                payload={"checkout_authority_id": str(authority.id)},
            )
        )
        await session.commit()
        raise
    order = RazorpayOrder(
        tenant_id=tenant.id,
        checkout_authority_id=authority.id,
        payment_id=authority.payment_id,
        razorpay_order_id=razorpay_order_id,
        receipt=receipt,
        amount_minor=request.amount_minor,
        currency=request.currency,
    )
    session.add(order)
    session.add(
        AuditEvent(
            tenant_id=tenant.id,
            correlation_id=correlation_id,
            event_kind="razorpay_order_created",
            payload={
                "checkout_authority_id": str(authority.id),
                "razorpay_order_id": razorpay_order_id,
            },
        )
    )
    await session.commit()
    return _order_response(order, key_id)


@router.post("/callback", status_code=status.HTTP_202_ACCEPTED)
async def verify_callback(
    callback: RazorpayCallback,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> JSONResponse:
    """Verify the browser callback, without treating it as authoritative capture evidence."""

    _, key_secret = _credentials()
    order = await session.scalar(
        select(RazorpayOrder).where(RazorpayOrder.razorpay_order_id == callback.razorpay_order_id)
    )
    if order is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND, content={"detail": "RAZORPAY_ORDER_NOT_FOUND"}
        )
    expected = hmac.new(
        key_secret.encode(),
        f"{order.razorpay_order_id}|{callback.razorpay_payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, callback.razorpay_signature):
        session.add(
            AuditEvent(
                tenant_id=order.tenant_id,
                correlation_id=uuid4(),
                event_kind="razorpay_callback_rejected",
                payload={
                    "reason": "RAZORPAY_SIGNATURE_INVALID",
                    "razorpay_order_id": order.razorpay_order_id,
                },
            )
        )
        await session.commit()
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "RAZORPAY_SIGNATURE_INVALID"},
        )
    session.add(
        AuditEvent(
            tenant_id=order.tenant_id,
            correlation_id=uuid4(),
            event_kind="razorpay_callback_verified",
            payload={
                "razorpay_order_id": order.razorpay_order_id,
                "razorpay_payment_id": callback.razorpay_payment_id,
            },
        )
    )
    await session.commit()
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"detail": "RAZORPAY_CALLBACK_VERIFIED"},
    )
