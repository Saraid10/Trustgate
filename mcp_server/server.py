from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any, cast
from uuid import UUID, uuid4

from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import SessionLocal
from api.routes.catalog_payment_requests import create_catalog_payment_request_for_source
from models.domain import AuditEvent, AuthorizationDecision, CatalogItem, Merchant, Payment, Tenant
from schemas.domain import CatalogPaymentRequestCreate, PaymentRequestDecision

SessionFactory = Callable[[], AsyncSession]


async def _configured_tenant(session: AsyncSession) -> Tenant:
    configured_id = os.getenv("MCP_TENANT_ID")
    if configured_id is None:
        raise ValueError("MCP_TENANT_ID is not configured.")
    try:
        tenant_id = UUID(configured_id)
    except ValueError as exc:
        raise ValueError("MCP_TENANT_ID is not a UUID.") from exc
    tenant = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if tenant is None:
        raise ValueError("MCP_TENANT_ID does not identify a known tenant.")
    return tenant


def _configured_actor_id() -> str:
    actor_id = os.getenv("MCP_ACTOR_ID")
    if not actor_id:
        raise ValueError("MCP_ACTOR_ID is not configured.")
    return actor_id


def _parse_identifier(value: str) -> UUID | None:
    """Parse an agent-supplied identifier without trusting its shape.

    Tool arguments are untrusted input. An unparseable value should be refused the same way an
    unknown one is, rather than surfacing a parse error that distinguishes "malformed" from
    "belongs to someone else".
    """

    try:
        return UUID(value)
    except (ValueError, AttributeError):
        return None


def _result_data(result: PaymentRequestDecision | JSONResponse) -> dict[str, Any]:
    if isinstance(result, JSONResponse):
        return cast(dict[str, Any], json.loads(bytes(result.body)))
    return result.model_dump(mode="json")


def create_mcp_server(session_factory: SessionFactory = SessionLocal) -> FastMCP:
    """Create the local stdio server with only the project-approved safe tools."""

    server = FastMCP(
        "MCP Payment Safety Testbed",
        instructions=(
            "Tenant and actor identities are server configured. The agent can select catalog SKU, "
            "quantity, and purpose. It cannot supply a merchant, price, currency, actor, or order "
            "reference. It can request human review and read the tenant catalog and status; it "
            "cannot authorize, capture, or call a provider."
        ),
    )

    @server.tool(description="List active synthetic catalog items for the configured tenant.")
    async def list_catalog() -> dict[str, list[dict[str, object]]]:
        async with session_factory() as session:
            async with session.begin():
                tenant = await _configured_tenant(session)
                rows = list(
                    await session.execute(
                        select(CatalogItem, Merchant.name)
                        .join(
                            Merchant,
                            (Merchant.id == CatalogItem.merchant_id)
                            & (Merchant.tenant_id == CatalogItem.tenant_id),
                        )
                        .where(CatalogItem.tenant_id == tenant.id, CatalogItem.active.is_(True))
                        .order_by(CatalogItem.sku)
                    )
                )
                return {
                    "items": [
                        {
                            "sku": item.sku,
                            "name": item.name,
                            "merchant_display_name": merchant_name,
                            "description": item.description_untrusted,
                            "price_minor": item.price_minor,
                            "currency": item.currency,
                            "max_quantity": item.max_quantity,
                        }
                        for item, merchant_name in rows
                    ]
                }

    @server.tool(
        name="create_payment_request",
        description="Create a catalog-backed request from a SKU, quantity, and business purpose.",
    )
    async def create_payment_request_tool(
        sku: str,
        quantity: int,
        purpose: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request = CatalogPaymentRequestCreate(
            sku=sku,
            quantity=quantity,
            purpose=purpose,
            idempotency_key=idempotency_key,
        )
        async with session_factory() as session:
            async with session.begin():
                tenant = await _configured_tenant(session)
                result = _result_data(
                    await create_catalog_payment_request_for_source(
                        request,
                        tenant,
                        session,
                        actor_id=_configured_actor_id(),
                        source="MCP_AGENT",
                    )
                )
                if "payment_request_id" not in result:
                    return result
                payment = await session.scalar(
                    select(Payment).where(
                        Payment.tenant_id == tenant.id,
                        Payment.payment_request_id == UUID(str(result["payment_request_id"])),
                    )
                )
                if payment is None:
                    raise RuntimeError("Created payment request is missing its payment record.")
                return {**result, "payment_id": str(payment.id)}

    @server.tool(
        description="Read the stored policy decision for a configured-tenant payment request."
    )
    async def evaluate_payment_policy(payment_request_id: str) -> dict[str, Any]:
        async with session_factory() as session:
            async with session.begin():
                tenant = await _configured_tenant(session)
                identifier = _parse_identifier(payment_request_id)
                if identifier is None:
                    return {"found": False, "reason": "CROSS_TENANT_ACCESS_DENIED"}
                decision = await session.scalar(
                    select(AuthorizationDecision)
                    .where(
                        AuthorizationDecision.tenant_id == tenant.id,
                        AuthorizationDecision.payment_request_id == identifier,
                    )
                    .order_by(AuthorizationDecision.created_at.desc())
                    .limit(1)
                )
                if decision is None:
                    return {"found": False, "reason": "CROSS_TENANT_ACCESS_DENIED"}
                return {
                    "found": True,
                    "decision": decision.decision,
                    "reasons": decision.reasons,
                    "policy_version": decision.policy_version,
                }

    @server.tool(
        description="Request separate human review for an approval-required payment request."
    )
    async def request_user_approval(payment_request_id: str) -> dict[str, Any]:
        async with session_factory() as session:
            async with session.begin():
                tenant = await _configured_tenant(session)
                identifier = _parse_identifier(payment_request_id)
                payment = (
                    await session.scalar(
                        select(Payment).where(
                            Payment.tenant_id == tenant.id,
                            Payment.payment_request_id == identifier,
                        )
                    )
                    if identifier is not None
                    else None
                )
                if payment is None or payment.state != "APPROVAL_REQUIRED":
                    reason = "APPROVAL_NOT_REQUIRED"
                    session.add(
                        AuditEvent(
                            tenant_id=tenant.id,
                            payment_request_id=(
                                payment.payment_request_id if payment is not None else None
                            ),
                            payment_id=payment.id if payment is not None else None,
                            correlation_id=uuid4(),
                            event_kind="mcp_request_rejected",
                            payload={"reason": reason, "payment_request_id": payment_request_id},
                        )
                    )
                    return {"ok": False, "reason": reason}
                session.add(
                    AuditEvent(
                        tenant_id=tenant.id,
                        payment_request_id=(
                            payment.payment_request_id if payment is not None else None
                        ),
                        payment_id=payment.id if payment is not None else None,
                        correlation_id=uuid4(),
                        event_kind="human_approval_requested",
                        payload={"payment_request_id": payment_request_id},
                    )
                )
                return {"ok": True, "status": "PENDING_HUMAN_APPROVAL"}

    @server.tool(
        description="Read the configured tenant's payment status without exposing other tenants."
    )
    async def get_payment_status(payment_id: str) -> dict[str, Any]:
        async with session_factory() as session:
            async with session.begin():
                tenant = await _configured_tenant(session)
                identifier = _parse_identifier(payment_id)
                payment = (
                    await session.scalar(
                        select(Payment).where(
                            Payment.id == identifier, Payment.tenant_id == tenant.id
                        )
                    )
                    if identifier is not None
                    else None
                )
                if payment is None:
                    session.add(
                        AuditEvent(
                            tenant_id=tenant.id,
                            correlation_id=uuid4(),
                            event_kind="mcp_request_rejected",
                            payload={
                                "reason": "CROSS_TENANT_ACCESS_DENIED",
                                "payment_id": payment_id,
                            },
                        )
                    )
                    return {"found": False, "reason": "CROSS_TENANT_ACCESS_DENIED"}
                return {
                    "found": True,
                    "payment_id": str(payment.id),
                    "payment_request_id": str(payment.payment_request_id),
                    "state": payment.state,
                    "authorized_amount_minor": payment.authorized_amount_minor,
                    "captured_amount_minor": payment.captured_amount_minor,
                    "refunded_amount_minor": payment.refunded_amount_minor,
                }

    return server


mcp = create_mcp_server()


if __name__ == "__main__":
    mcp.run(transport="stdio")
