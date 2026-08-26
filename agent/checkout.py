"""Take an authorized purchase as far as a payable checkout page.

The demo stopped at `AUTHORIZED`, which is the interesting state and not a finished story. Getting
from there to a page a human can pay on takes two calls the repository had no driver for: issue a
checkout authority, then create a provider order from the snapshot that authority is bound to.

Both calls go over HTTP with the tenant header, exactly as an operator's own tooling would, so this
adds no path to authority that did not already exist. It cannot authorize anything: it asks the
server to issue a permission for a payment the server already authorized, and the server refuses if
the policy moved, the purchase changed, or the authority was already spent.

The URL it prints is the checkout page. Opening it and paying is a human's job, which is the point.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from dataclasses import dataclass
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.runtime import load_local_env, run_async
from agent.stage import DEMO_TENANT_ID
from api.database import SessionLocal
from models.domain import Payment, PaymentRequest, RazorpayOrder


class CheckoutUnavailableError(RuntimeError):
    """Raised with a message an operator can act on mid-demo."""


@dataclass(frozen=True)
class Payable:
    payment_request_id: UUID
    payment_id: UUID
    amount_minor: int
    currency: str
    sku: str | None


async def find_payable(session: AsyncSession, tenant_id: UUID) -> Payable | None:
    """Find the newest authorized purchase that has not yet reached the provider.

    Authorized, and with no provider order already created from it. A purchase that has one is
    finished with this step, and offering it again would invite a second order for one
    authorization - which the server would refuse, but only after the demo had asked for it.
    """

    row = (
        await session.execute(
            select(PaymentRequest, Payment)
            .join(
                Payment,
                (Payment.payment_request_id == PaymentRequest.id)
                & (Payment.tenant_id == PaymentRequest.tenant_id),
            )
            .outerjoin(
                RazorpayOrder,
                (RazorpayOrder.payment_id == Payment.id)
                & (RazorpayOrder.tenant_id == Payment.tenant_id),
            )
            .where(
                PaymentRequest.tenant_id == tenant_id,
                Payment.state == "AUTHORIZED",
                RazorpayOrder.id.is_(None),
            )
            .order_by(PaymentRequest.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    request, payment = row
    return Payable(
        payment_request_id=request.id,
        payment_id=payment.id,
        amount_minor=request.amount_minor,
        currency=request.currency,
        sku=request.catalog_sku,
    )


async def _post(
    client: httpx.AsyncClient, url: str, tenant_id: UUID, *, step: str
) -> dict[str, object]:
    response = await client.post(url, headers={"X-Tenant-Id": str(tenant_id)})
    if response.status_code not in {200, 201}:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise CheckoutUnavailableError(f"{step} was refused: {detail}")
    body = response.json()
    assert isinstance(body, dict)
    return body


async def prepare_checkout(
    *,
    base_url: str,
    tenant_id: UUID,
    payment_request_id: UUID,
    client: httpx.AsyncClient | None = None,
) -> dict[str, object]:
    """Issue an authority and create the provider order it permits."""

    root = base_url.rstrip("/")
    owned = client is None
    http = client or httpx.AsyncClient(timeout=30.0)
    try:
        authority = await _post(
            http,
            f"{root}/api/v1/checkout-authorities/{payment_request_id}",
            tenant_id,
            step="issuing the checkout authority",
        )
        authority_id = authority["checkout_authority_id"]
        order = await _post(
            http,
            f"{root}/api/v1/razorpay/checkout-authorities/{authority_id}/orders",
            tenant_id,
            step="creating the provider order",
        )
    finally:
        if owned:
            await http.aclose()

    return {
        "checkout_authority_id": authority_id,
        "expires_at": authority.get("expires_at"),
        "razorpay_order_id": order["razorpay_order_id"],
        "amount_minor": order["amount_minor"],
        "currency": order["currency"],
        "checkout_url": f"{root}/checkout/{order['razorpay_order_id']}",
    }


async def _main(base_url: str, tenant_id: UUID, request_id: UUID | None, open_page: bool) -> None:
    if request_id is None:
        async with SessionLocal() as session:
            payable = await find_payable(session, tenant_id)
        if payable is None:
            # Naming the tenant is the whole point of this message. "Nothing is waiting" is true
            # and useless when the reason is that the search looked somewhere else.
            raise CheckoutUnavailableError(
                f"no authorized purchase is waiting for checkout in tenant {tenant_id}. "
                "Check MCP_TENANT_ID in your shell and in .env - a stale value here sends one "
                "command's purchase to one tenant and this search to another. Then run: "
                "python -m agent.demo 'Buy Starter credits for the robotics club.'"
            )
        request_id = payable.payment_request_id
        print(f"  Tenant           {tenant_id}")
        print(
            f"  Taking {payable.sku or 'purchase'} "
            f"for {payable.currency} {payable.amount_minor / 100:,.2f} to checkout"
        )

    result = await prepare_checkout(
        base_url=base_url, tenant_id=tenant_id, payment_request_id=request_id
    )

    print(f"\n  Provider order   {result['razorpay_order_id']}")
    print(f"  Authority spent  {result['checkout_authority_id']}")
    print(f"\n  Pay here:        {result['checkout_url']}")
    print("\n  Razorpay Test Mode card: 4111 1111 1111 1111, any future expiry, any CVV.\n")

    if open_page:
        webbrowser.open(str(result["checkout_url"]))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Issue a checkout authority, create the provider order, print the pay link."
    )
    parser.add_argument(
        "payment_request_id",
        nargs="?",
        type=UUID,
        default=None,
        help="Which request to take to checkout. Defaults to the newest authorized one.",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Where the API lives.")
    parser.add_argument(
        "--tenant-id",
        type=UUID,
        default=None,
        help="Defaults to MCP_TENANT_ID, then to the fixed demo tenant.",
    )
    parser.add_argument("--open", action="store_true", help="Open the checkout page in a browser.")
    parser.add_argument("--json", action="store_true", help="Emit the result as JSON.")
    args = parser.parse_args()

    load_local_env()
    env_tenant = os.getenv("MCP_TENANT_ID")
    tenant_id = args.tenant_id or (UUID(env_tenant) if env_tenant else DEMO_TENANT_ID)

    try:
        if args.json:
            run_async(_emit_json(args.base_url, tenant_id, args.payment_request_id))
        else:
            run_async(_main(args.base_url, tenant_id, args.payment_request_id, args.open))
    except CheckoutUnavailableError as exc:
        # A demo failure should say what to do next, not print a stack trace on camera.
        print(f"\n  {exc}\n", file=sys.stderr)
        raise SystemExit(1) from exc


async def _emit_json(base_url: str, tenant_id: UUID, request_id: UUID | None) -> None:
    if request_id is None:
        async with SessionLocal() as session:
            payable = await find_payable(session, tenant_id)
        if payable is None:
            raise CheckoutUnavailableError("no authorized purchase is waiting for checkout")
        request_id = payable.payment_request_id
    result = await prepare_checkout(
        base_url=base_url, tenant_id=tenant_id, payment_request_id=request_id
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
