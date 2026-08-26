"""A read-only demonstration console.

Three properties are deliberate, and each is enforced rather than intended.

**It cannot act.** Every route here is a GET that renders rows which already exist. The console
has no path to authorize, approve, consume an authority, or call a provider. A console with an
approve button would be a new authority surface, and the project's central claim - that authority
is held by the server and reachable only through the checked paths - would need an asterisk. A
demonstration of a safety property must not weaken it.

**It is off unless asked for.** `ENABLE_CONSOLE=true` is required, matching how the legacy
payment-request route is gated. A demo surface that ships reachable is a demo surface someone
deploys by accident.

**Tenant identity comes from the path.** Browsers cannot set `X-Tenant-Id`, so the API's header
identity is unreachable from a browser and the receipt could not be opened during a demo at all.
The path carries it instead, exactly as the checkout page already carries an order id. This is the
same testbed-grade identity the rest of the project documents as not being production
authentication - moved, not weakened - and it is why the console is gated. It also means the
tenant id appears on screen during a recording, which is acceptable only because every tenant here
is synthetic.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.console_view import ConsoleEntry, render_console
from api.database import get_session
from api.receipt import render_receipt
from api.routes.evidence import build_payment_request_evidence
from models.domain import (
    Approval,
    AuditEvent,
    AuthorizationDecision,
    Payment,
    PaymentRequest,
    RazorpayOrder,
    Tenant,
)

router = APIRouter(prefix="/console", tags=["console"])

# Bounded so a long-lived demo tenant cannot render an unbounded page.
_TIMELINE_LIMIT = 50

_RECEIPT_HREF = "/console/{tenant_id}/requests/{payment_request_id}"

# Refusals that happen before a payment request exists. They leave an audit event and nothing else,
# so a timeline assembled only from requests would be silent exactly where the most important row
# belongs - an attack turned away at the boundary.
_BOUNDARY_REFUSAL_KINDS = ("catalog_purchase_rejected", "payment_request_rejected")

# Both console pages render catalog text written by third parties. Every value goes through
# `html.escape`, and these headers are what stands behind that if it ever does not: with no script
# source permitted at all, an escaping mistake stops being executable.
#
# `no-referrer` earns its place separately. The tenant id is in the path, so any outbound
# navigation would put it in a Referer header. There is nothing to click today, which is the
# cheapest moment to make sure there never is.
_PAGE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def _require_console_enabled() -> None:
    if os.getenv("ENABLE_CONSOLE") != "true":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


async def _load_tenant(session: AsyncSession, tenant_id: UUID) -> Tenant:
    tenant = await session.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if tenant is None:
        # The same refusal an unknown tenant gets on the API surface, for the same reason: a
        # distinct answer here would say which tenant ids exist.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="unknown tenant")
    return tenant


async def _timeline(session: AsyncSession, tenant_id: UUID) -> list[ConsoleEntry]:
    """Assemble the timeline for one tenant.

    Every query is filtered on `tenant_id` rather than relying on a parent row already being
    tenant-scoped. Reaching a payment through its request would be correct today and would stay
    correct only for as long as nobody changes the join, which is the kind of guarantee this
    project prefers not to depend on.

    The related rows are read in four queries rather than four per request. The earlier shape cost
    a round trip per column per row, so a page of fifty attempts issued two hundred queries to
    render one screen - bounded, and still the wrong shape to leave in a repository people read.
    """

    requests = (
        await session.scalars(
            select(PaymentRequest)
            .where(PaymentRequest.tenant_id == tenant_id)
            .order_by(PaymentRequest.created_at.desc())
            .limit(_TIMELINE_LIMIT)
        )
    ).all()
    request_ids = [request.id for request in requests]

    payments: dict[UUID, Payment] = {}
    decisions: dict[UUID, AuthorizationDecision] = {}
    approvals: dict[UUID, Approval] = {}
    orders: dict[UUID, RazorpayOrder] = {}

    if request_ids:
        for loaded_payment in (
            await session.scalars(
                select(Payment).where(
                    Payment.tenant_id == tenant_id,
                    Payment.payment_request_id.in_(request_ids),
                )
            )
        ).all():
            payments[loaded_payment.payment_request_id] = loaded_payment

        # Newest first, so the first one seen for a request is the one that governs.
        for loaded_decision in (
            await session.scalars(
                select(AuthorizationDecision)
                .where(
                    AuthorizationDecision.tenant_id == tenant_id,
                    AuthorizationDecision.payment_request_id.in_(request_ids),
                )
                .order_by(AuthorizationDecision.created_at.desc())
            )
        ).all():
            decisions.setdefault(loaded_decision.payment_request_id, loaded_decision)

        for loaded_approval in (
            await session.scalars(
                select(Approval).where(
                    Approval.tenant_id == tenant_id,
                    Approval.payment_request_id.in_(request_ids),
                    Approval.consumed_at.is_not(None),
                )
            )
        ).all():
            approvals.setdefault(loaded_approval.payment_request_id, loaded_approval)

        payment_ids = [loaded.id for loaded in payments.values()]
        if payment_ids:
            for loaded_order in (
                await session.scalars(
                    select(RazorpayOrder).where(
                        RazorpayOrder.tenant_id == tenant_id,
                        RazorpayOrder.payment_id.in_(payment_ids),
                    )
                )
            ).all():
                orders.setdefault(loaded_order.payment_id, loaded_order)

    entries: list[ConsoleEntry] = list(await _boundary_refusals(session, tenant_id))
    for request in requests:
        decision = decisions.get(request.id)
        payment = payments.get(request.id)
        approval = approvals.get(request.id)
        order = orders.get(payment.id) if payment is not None else None
        entries.append(
            ConsoleEntry(
                payment_request_id=request.id,
                requested_at=request.created_at,
                actor_id=request.actor_id,
                source=request.source,
                sku=request.catalog_sku,
                quantity=request.quantity,
                purpose=request.purpose,
                merchant_display_name=request.merchant_display_name,
                amount_minor=request.amount_minor,
                currency=request.currency,
                decision=decision.decision if decision else None,
                reasons=tuple(decision.reasons) if decision else (),
                approval_granted_by=approval.granted_by if approval else None,
                payment_state=payment.state if payment else None,
                provider_order_id=order.razorpay_order_id if order else None,
                provider_state=order.provider_state if order else None,
            )
        )
    # One timeline, newest first, and bounded once rather than twice. Both sources cap themselves
    # at the limit, so merging them without this could render twice the page anyone asked for.
    entries.sort(key=lambda entry: entry.requested_at, reverse=True)
    return entries[:_TIMELINE_LIMIT]


async def _boundary_refusals(session: AsyncSession, tenant_id: UUID) -> list[ConsoleEntry]:
    """Read attempts that were refused before any payment request was written.

    The audit payload is the only record of what was asked for, because nothing else was created.
    Its keys are read defensively: an event whose shape changes should lose a detail from a demo
    row, not remove the row and with it the evidence that the attempt was refused.
    """

    events = (
        await session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.tenant_id == tenant_id,
                AuditEvent.event_kind.in_(_BOUNDARY_REFUSAL_KINDS),
                AuditEvent.payment_request_id.is_(None),
            )
            .order_by(AuditEvent.created_at.desc())
            .limit(_TIMELINE_LIMIT)
        )
    ).all()

    entries: list[ConsoleEntry] = []
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        quantity = payload.get("requested_quantity")
        reason = payload.get("reason")
        entries.append(
            ConsoleEntry(
                payment_request_id=None,
                requested_at=event.created_at,
                actor_id=str(payload.get("actor_id", "agent")),
                source="MCP_AGENT",
                sku=str(payload["sku"]) if payload.get("sku") is not None else None,
                quantity=int(quantity) if isinstance(quantity, int) else None,
                purpose=None,
                merchant_display_name=None,
                amount_minor=None,
                currency="INR",
                decision="REFUSED",
                reasons=(str(reason),) if reason is not None else (),
                approval_granted_by=None,
                payment_state=None,
                provider_order_id=None,
                provider_state=None,
            )
        )
    return entries


@router.get("/{tenant_id}", response_class=HTMLResponse)
async def render_tenant_console(
    tenant_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[None, Depends(_require_console_enabled)] = None,
) -> HTMLResponse:
    """Render every recent purchase attempt for one tenant, newest first."""

    tenant = await _load_tenant(session, tenant_id)
    entries = await _timeline(session, tenant_id)
    return HTMLResponse(
        headers=_PAGE_HEADERS,
        content=render_console(
            tenant_id=tenant.id,
            tenant_name=tenant.name,
            entries=entries,
            receipt_href=_RECEIPT_HREF.format(
                tenant_id=tenant.id, payment_request_id="{payment_request_id}"
            ),
            generated_at=datetime.now(UTC),
        ),
    )


@router.get("/{tenant_id}/requests/{payment_request_id}", response_class=HTMLResponse)
async def render_console_receipt(
    tenant_id: UUID,
    payment_request_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _: Annotated[None, Depends(_require_console_enabled)] = None,
) -> HTMLResponse:
    """Render the existing receipt for one attempt, reachable from a browser.

    The same `render_receipt` the API serves, over the same assembled evidence. A second renderer
    would be a second opinion about what happened, and two of those is one too many.
    """

    tenant = await _load_tenant(session, tenant_id)
    evidence = await build_payment_request_evidence(
        session, tenant=tenant, payment_request_id=payment_request_id
    )
    return HTMLResponse(content=render_receipt(evidence), headers=_PAGE_HEADERS)
