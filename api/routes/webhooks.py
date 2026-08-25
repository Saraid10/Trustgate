from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_session
from mock_provider.signing import signature_is_valid
from models.domain import AuditEvent, Payment, ProviderEvent, Tenant
from models.locking import locked
from schemas.domain import ProviderWebhookEvent
from state_machine.transitions import StateMachineError, transition

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)
_TOLERANCE = timedelta(minutes=5)
_MAX_WEBHOOK_BODY_BYTES = 64 * 1024
_EVENT_TARGETS = {
    "payment.authorized": "PROVIDER_PENDING",
    "payment.captured": "CAPTURED",
    "payment.failed": "FAILED",
    "payment.refunded": "REFUNDED",
}


def _log_unattributed_rejection(request: Request, raw_body: bytes, reason: str) -> None:
    logger.warning(
        json.dumps(
            {
                "correlation_id": str(uuid4()),
                "reason": reason,
                "remote_ip": request.client.host if request.client else None,
                "raw_body_sha256": hashlib.sha256(raw_body).hexdigest(),
            }
        )
    )


def _audit_rejection(
    session: AsyncSession,
    *,
    tenant_id: object,
    correlation_id: object,
    reason: str,
    event: ProviderWebhookEvent,
    payment_request_id: UUID | None,
    payment_id: UUID | None,
) -> None:
    session.add(
        AuditEvent(
            tenant_id=tenant_id,
            payment_request_id=payment_request_id,
            payment_id=payment_id,
            correlation_id=correlation_id,
            event_kind="webhook_rejected",
            payload={
                "reason": reason,
                "provider_event_id": event.provider_event_id,
                "payment_id": str(event.payment_id),
            },
        )
    )


@router.post("/provider-events")
async def receive_provider_event(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> JSONResponse:
    """Verify raw provider bytes before parsing or trusting any claimed tenant identity."""

    content_length = request.headers.get("content-length")
    try:
        declared_body_size = int(content_length) if content_length is not None else None
    except ValueError:
        declared_body_size = _MAX_WEBHOOK_BODY_BYTES + 1
    if declared_body_size is not None and declared_body_size > _MAX_WEBHOOK_BODY_BYTES:
        _log_unattributed_rejection(request, b"", "WEBHOOK_BODY_TOO_LARGE")
        return JSONResponse(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            content={"detail": "WEBHOOK_BODY_TOO_LARGE"},
        )
    raw_body = await request.body()
    if len(raw_body) > _MAX_WEBHOOK_BODY_BYTES:
        _log_unattributed_rejection(request, raw_body, "WEBHOOK_BODY_TOO_LARGE")
        return JSONResponse(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            content={"detail": "WEBHOOK_BODY_TOO_LARGE"},
        )
    secret = os.getenv("PROVIDER_WEBHOOK_SECRET")
    signature = request.headers.get("X-Provider-Signature")
    if not secret or not signature_is_valid(raw_body, signature, secret):
        _log_unattributed_rejection(request, raw_body, "WEBHOOK_SIGNATURE_INVALID")
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "WEBHOOK_SIGNATURE_INVALID"},
        )
    try:
        event = ProviderWebhookEvent.model_validate_json(raw_body)
    except ValidationError:
        _log_unattributed_rejection(request, raw_body, "WEBHOOK_BODY_TAMPERED")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": "WEBHOOK_BODY_TAMPERED"},
        )

    correlation_id = uuid4()
    transaction = session.begin_nested() if session.in_transaction() else session.begin()
    response: JSONResponse | None = None
    async with transaction:
        tenant = await session.scalar(select(Tenant).where(Tenant.id == event.tenant_id))
        payment = await session.scalar(
            locked(
                select(Payment).where(
                    Payment.id == event.payment_id, Payment.tenant_id == event.tenant_id
                )
            )
        )
        # A nested transition can roll back and expire `payment`. Keep the identity values while
        # the locked row is known-good, so rejection auditing never triggers implicit async I/O.
        payment_request_id = payment.payment_request_id if payment is not None else None
        payment_id = payment.id if payment is not None else None
        if tenant is None or payment is None:
            if tenant is not None:
                _audit_rejection(
                    session,
                    tenant_id=tenant.id,
                    correlation_id=correlation_id,
                    reason="WEBHOOK_TENANT_MISMATCH",
                    event=event,
                    payment_request_id=payment_request_id,
                    payment_id=payment_id,
                )
            response = JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={"detail": "WEBHOOK_TENANT_MISMATCH"},
            )
        elif abs(datetime.now(UTC) - event.occurred_at) > _TOLERANCE:
            _audit_rejection(
                session,
                tenant_id=tenant.id,
                correlation_id=correlation_id,
                reason="WEBHOOK_TIMESTAMP_STALE",
                event=event,
                payment_request_id=payment_request_id,
                payment_id=payment_id,
            )
            response = JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                content={"detail": "WEBHOOK_TIMESTAMP_STALE"},
            )
        else:
            duplicate = await session.scalar(
                select(ProviderEvent).where(
                    ProviderEvent.tenant_id == tenant.id,
                    ProviderEvent.provider_event_id == event.provider_event_id,
                )
            )
            if duplicate is not None:
                _audit_rejection(
                    session,
                    tenant_id=tenant.id,
                    correlation_id=correlation_id,
                    reason="WEBHOOK_DUPLICATE_EVENT",
                    event=event,
                    payment_request_id=payment_request_id,
                    payment_id=payment_id,
                )
                response = JSONResponse(
                    status_code=status.HTTP_409_CONFLICT,
                    content={"detail": "WEBHOOK_DUPLICATE_EVENT"},
                )
            else:
                try:
                    async with session.begin_nested():
                        provider_event = ProviderEvent(
                            tenant_id=tenant.id,
                            provider_event_id=event.provider_event_id,
                            event_type=event.event_type,
                            payment_id=payment.id,
                            raw_payload=raw_body,
                            signature=signature,
                        )
                        session.add(provider_event)
                        if event.event_type == "payment.captured":
                            payment.captured_amount_minor = payment.authorized_amount_minor or 0
                        elif event.event_type == "payment.refunded":
                            payment.refunded_amount_minor = payment.captured_amount_minor
                        await transition(
                            session,
                            payment,
                            _EVENT_TARGETS[event.event_type],
                            reason=event.event_type,
                            correlation_id=correlation_id,
                        )
                except IntegrityError:
                    _audit_rejection(
                        session,
                        tenant_id=tenant.id,
                        correlation_id=correlation_id,
                        reason="WEBHOOK_DUPLICATE_EVENT",
                        event=event,
                        payment_request_id=payment_request_id,
                        payment_id=payment_id,
                    )
                    response = JSONResponse(
                        status_code=status.HTTP_409_CONFLICT,
                        content={"detail": "WEBHOOK_DUPLICATE_EVENT"},
                    )
                except StateMachineError as exc:
                    _audit_rejection(
                        session,
                        tenant_id=tenant.id,
                        correlation_id=correlation_id,
                        reason=exc.reason_code,
                        event=event,
                        payment_request_id=payment_request_id,
                        payment_id=payment_id,
                    )
                    response = JSONResponse(
                        status_code=status.HTTP_409_CONFLICT,
                        content={"detail": exc.reason_code},
                    )
                else:
                    provider_event.processed_at = datetime.now(UTC)
                    response = JSONResponse(
                        status_code=status.HTTP_202_ACCEPTED,
                        content={"event_id": event.provider_event_id},
                    )
        await session.flush()
    if response is None:
        raise RuntimeError("Webhook processing completed without a response.")
    return response
