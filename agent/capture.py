"""Carry a paid order to `CAPTURED` by delivering the two signed events Razorpay would send.

The provider half of this project has always ended one step short on a laptop. An order is created
against the real Razorpay Test Mode API, a human pays it on Razorpay's own page, the browser
callback comes back and verifies - and the payment stays `AUTHORIZED`, because
`verify_callback` deliberately refuses to treat a browser callback as capture evidence. Only a
signed server-to-server event may move money, and Razorpay's servers cannot reach `127.0.0.1`.

So the last step was done by hand in August, preserved as `docs/evidence/m3-webhook-lifecycle.json`,
and never made repeatable. This is that step, as a command.

**What this is honest about.** These events are constructed here and signed with this project's own
`RAZORPAY_WEBHOOK_SECRET`. Razorpay did not send them. The *order* is provider-originated - it has
a real `order_...` id from the live Test Mode API - and the delivery is local. That distinction is
printed every run rather than left in a document, because a demo command that lets its operator
imply otherwise is a demo command that will eventually be used to imply otherwise.

What it does establish is everything between the signature and the state: the verification over raw
bytes, the event-identity derivation that keeps `payment.authorized` and `payment.captured` from
deduplicating against each other, the amount cross-check against the server-derived order, and the
state machine carrying `AUTHORIZED` -> `PROVIDER_PENDING` -> `CAPTURED`.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.runtime import load_local_env, run_async
from agent.stage import DEMO_TENANT_ID
from api.database import SessionLocal
from models.domain import Payment, RazorpayOrder

# In Razorpay's order, and in this order for a reason: `payment.authorized` moves the payment to
# PROVIDER_PENDING and `payment.captured` moves it to CAPTURED. Delivering only the capture would
# be refused by the state machine, which does not accept AUTHORIZED -> CAPTURED directly.
_LIFECYCLE = ("payment.authorized", "payment.captured")


class CaptureUnavailableError(RuntimeError):
    """Raised with a message an operator can act on mid-demo."""


async def find_capturable_order(
    session: AsyncSession, tenant_id: UUID
) -> tuple[RazorpayOrder, Payment] | None:
    """The newest confirmed order whose payment has not yet been carried to a settled state.

    Read from the database rather than passed in, for the reason `agent.approve` reads its pending
    approval: the command has to work whether the order was created a second ago or in the take
    before this one, without an identifier copied between terminals on camera.
    """

    row = (
        await session.execute(
            select(RazorpayOrder, Payment)
            .join(
                Payment,
                (Payment.id == RazorpayOrder.payment_id)
                & (Payment.tenant_id == RazorpayOrder.tenant_id),
            )
            .where(
                RazorpayOrder.tenant_id == tenant_id,
                RazorpayOrder.razorpay_order_id.is_not(None),
                Payment.state.in_(("AUTHORIZED", "PROVIDER_PENDING")),
            )
            .order_by(RazorpayOrder.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    order, payment = row
    return order, payment


def _webhook_secret() -> str:
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")
    if not secret:
        raise CaptureUnavailableError(
            "RAZORPAY_WEBHOOK_SECRET is not set. Add it to .env, then recreate the api container "
            "so it reads it: docker compose up -d --force-recreate api"
        )
    return secret


def build_event(
    *,
    event: str,
    razorpay_order_id: str,
    razorpay_payment_id: str,
    amount_minor: int,
    currency: str,
    created_at: int | None = None,
) -> bytes:
    """One Razorpay webhook body, shaped as the provider sends it.

    Serialised once and signed over exactly these bytes. Building the body and then re-serialising
    it before signing would sign a different message than the one delivered, which is the mistake
    the route's own comment warns about from the receiving side.

    Both lifecycle events carry the same payment id, because Razorpay reports them that way - and
    that is precisely the case `webhook_event_identity` exists to handle.
    """

    return json.dumps(
        {
            "entity": "event",
            "event": event,
            "created_at": created_at
            if created_at is not None
            else int(datetime.now(UTC).timestamp()),
            "contains": ["payment"],
            "payload": {
                "payment": {
                    "entity": {
                        "id": razorpay_payment_id,
                        "order_id": razorpay_order_id,
                        "amount": amount_minor,
                        "currency": currency,
                        "status": "captured" if event == "payment.captured" else "authorized",
                    }
                }
            },
        },
        separators=(",", ":"),
    ).encode()


def sign(body: bytes, secret: str) -> str:
    """The signature Razorpay computes, over the raw bytes, with the shared secret."""

    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def deliver(
    *,
    base_url: str,
    body: bytes,
    signature: str,
    client: httpx.AsyncClient | None = None,
) -> tuple[int, object]:
    """Post one signed event to the webhook route, exactly as the provider would."""

    url = f"{base_url.rstrip('/')}/api/v1/razorpay/webhook"
    owned = client is None
    http = client or httpx.AsyncClient(timeout=10.0)
    try:
        response = await http.post(
            url,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": signature,
            },
        )
    finally:
        if owned:
            await http.aclose()
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, response.text


async def _main(base_url: str, tenant_id: UUID) -> None:
    secret = _webhook_secret()

    async with SessionLocal() as session:
        found = await find_capturable_order(session, tenant_id)
    if found is None:
        raise CaptureUnavailableError(
            f"no paid order is waiting to be captured in tenant {tenant_id}. Check MCP_TENANT_ID "
            "in your shell, then run the checkout above it: python -m agent.checkout --open"
        )
    order, payment = found
    razorpay_order_id = order.razorpay_order_id
    if razorpay_order_id is None:
        raise CaptureUnavailableError(
            "the newest order has no provider id yet, so the provider call did not complete. "
            "Re-run: python -m agent.checkout --open"
        )

    # One payment id across both events, which is what Razorpay does and what makes the
    # event-identity derivation worth having.
    razorpay_payment_id = f"pay_{uuid4().hex[:14]}"

    print(f"  Tenant     {tenant_id}")
    print(f"  Order      {razorpay_order_id}  (created by the real Razorpay Test Mode API)")
    print(f"  Payment    {payment.state} -> delivering {len(_LIFECYCLE)} signed events\n")

    async with httpx.AsyncClient(timeout=10.0) as client:
        for event in _LIFECYCLE:
            body = build_event(
                event=event,
                razorpay_order_id=razorpay_order_id,
                razorpay_payment_id=razorpay_payment_id,
                amount_minor=order.amount_minor,
                currency=order.currency,
            )
            status_code, detail = await deliver(
                base_url=base_url, body=body, signature=sign(body, secret), client=client
            )
            if status_code != 202:
                raise CaptureUnavailableError(
                    f"the server refused {event}: {status_code} {json.dumps(detail)}"
                )
            print(f"  {event:<20} accepted  {json.dumps(detail)}")

    async with SessionLocal() as session:
        settled = await session.scalar(select(Payment.state).where(Payment.id == payment.id))
    print(f"\n  Payment is now {settled}.")
    print(
        "\n  Say this out loud: these two events were signed here with this project's own\n"
        "  webhook secret and posted locally. Razorpay's servers cannot reach a laptop. The\n"
        "  order is provider-originated; the delivery is not. What this proves is the\n"
        "  signature check, the event identity, the amount cross-check, and the state machine.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Deliver the two signed Razorpay events that carry a paid order to CAPTURED. "
            "The events are signed locally; Razorpay does not send them."
        )
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Where the API lives.")
    parser.add_argument(
        "--tenant-id",
        type=UUID,
        default=None,
        help="Defaults to MCP_TENANT_ID, then to the fixed demo tenant.",
    )
    args = parser.parse_args()

    load_local_env()
    env_tenant = os.getenv("MCP_TENANT_ID")
    tenant_id = args.tenant_id or (UUID(env_tenant) if env_tenant else DEMO_TENANT_ID)

    try:
        run_async(_main(args.base_url, tenant_id))
    except CaptureUnavailableError as exc:
        # A demo failure should say what to do next, not print a stack trace on camera.
        print(f"\n  {exc}\n", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
