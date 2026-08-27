"""Grant the human approval the demo's second flow waits on.

The build plan's three flows are a safe purchase, an approval-gated purchase, and a refused attack.
The middle one had no way to complete: the agent can reach `APPROVAL_REQUIRED` and stop there, and
nothing in the repository carried it further, so the flow could be described and not shown.

This is a separate command on purpose, and separate is the point. The approval is granted over the
same HTTP route an operator would use, holding a token the agent does not have, under an identity
the server refuses to accept if it matches the requester. Wiring approval into the buyer would have
made the demo shorter and the separation of duties fictional.

It is not part of the authorization path. It calls the API exactly as a human would, and the server
decides.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.runtime import load_local_env, run_async
from agent.stage import DEMO_TENANT_ID
from api.database import SessionLocal
from models.domain import Payment, PaymentRequest


class ApprovalUnavailableError(RuntimeError):
    """Raised with a message an operator can act on mid-demo."""


@dataclass(frozen=True)
class PendingApproval:
    payment_request_id: UUID
    actor_id: str
    amount_minor: int
    currency: str
    sku: str | None


async def find_pending_approval(session: AsyncSession, tenant_id: UUID) -> PendingApproval | None:
    """Find the newest purchase waiting on a human, so the demo needs no copied identifiers.

    Reading the state from the database rather than parsing it out of the agent's output keeps the
    two commands independent: the approval step works whether the purchase was made a second ago
    or in a previous take.
    """

    row = (
        await session.execute(
            select(PaymentRequest, Payment)
            .join(
                Payment,
                (Payment.payment_request_id == PaymentRequest.id)
                & (Payment.tenant_id == PaymentRequest.tenant_id),
            )
            .where(
                PaymentRequest.tenant_id == tenant_id,
                Payment.state == "APPROVAL_REQUIRED",
            )
            .order_by(PaymentRequest.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    request, _ = row
    return PendingApproval(
        payment_request_id=request.id,
        actor_id=request.actor_id,
        amount_minor=request.amount_minor,
        currency=request.currency,
        sku=request.catalog_sku,
    )


def _approver_token() -> str:
    token = os.getenv("DEMO_APPROVER_TOKEN")
    if not token:
        raise ApprovalUnavailableError(
            "DEMO_APPROVER_TOKEN is not set. Add it and DEMO_APPROVER_ID to .env, then recreate "
            "the api container so it reads them: docker compose up -d --force-recreate api"
        )
    return token


async def grant(
    *,
    base_url: str,
    tenant_id: UUID,
    payment_request_id: UUID,
    token: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, object]:
    """Grant approval over HTTP, exactly as an operator with the token would."""

    url = f"{base_url.rstrip('/')}/api/v1/approvals/{payment_request_id}/grant"
    headers = {"X-Tenant-Id": str(tenant_id), "X-Approver-Token": token}
    owned = client is None
    http = client or httpx.AsyncClient(timeout=10.0)
    try:
        response = await http.post(url, headers=headers)
    finally:
        if owned:
            await http.aclose()

    if response.status_code != 200:
        detail = response.json().get("detail", response.text)
        raise ApprovalUnavailableError(f"the server refused the approval: {detail}")
    body = response.json()
    if not isinstance(body, dict):
        # An explicit check rather than an assert: `python -O` strips assertions, and a narrowing
        # that vanishes under optimisation turns a clear error into a confusing one further down.
        raise ApprovalUnavailableError(
            f"the server returned {type(body).__name__} where an object was expected"
        )
    return body


async def _main(base_url: str, tenant_id: UUID, payment_request_id: UUID | None) -> None:
    token = _approver_token()

    if payment_request_id is None:
        async with SessionLocal() as session:
            pending = await find_pending_approval(session, tenant_id)
        if pending is None:
            # The tenant, for the same reason checkout names it: a stale MCP_TENANT_ID makes
            # "nothing is waiting" the right answer to the wrong question.
            raise ApprovalUnavailableError(
                f"nothing is waiting for approval in tenant {tenant_id}. Check MCP_TENANT_ID in "
                "your shell and in .env, then run a purchase above the approval threshold: "
                "python -m agent.demo 'Buy Team credits for the club.'"
            )
        payment_request_id = pending.payment_request_id
        print(f"  Tenant     {tenant_id}")
        print(
            f"  Approving {pending.sku or 'purchase'} "
            f"for {pending.currency} {pending.amount_minor / 100:,.2f}, "
            f"requested by {pending.actor_id}"
        )

    body = await grant(
        base_url=base_url,
        tenant_id=tenant_id,
        payment_request_id=payment_request_id,
        token=token,
    )
    approver = os.getenv("DEMO_APPROVER_ID", "unknown")
    print(f"  Granted by {approver}, who is not the requester.\n")
    print(json.dumps(body, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Grant the pending human approval for the demo tenant."
    )
    parser.add_argument(
        "payment_request_id",
        nargs="?",
        type=UUID,
        default=None,
        help="Which request to approve. Defaults to the newest one awaiting approval.",
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
        run_async(_main(args.base_url, tenant_id, args.payment_request_id))
    except ApprovalUnavailableError as exc:
        # A demo failure should say what to do next, not print a stack trace on camera.
        print(f"\n  {exc}\n", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
