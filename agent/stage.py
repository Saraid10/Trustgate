"""Put the database into a known state for the demo, repeatably.

`agent.seed` mints a fresh random tenant per run, which is right for exploring and wrong for a
recording: the console URL changes every time, so it cannot be written into a script, and previous
runs pile up beside the one being filmed. This stages a fixed tenant instead, wiping whatever the
last take left behind.

It lives in `agent` rather than `demo` on purpose. `tests/test_unguarded_baseline.py` asserts that
nothing in `demo/` imports a database, a network client, or a provider - which is what makes
"the unguarded baseline cannot charge anyone" checkable rather than promised. A seed script
legitimately needs a session, so putting it there would have meant loosening that rule to admit
one exception, and a rule with an exception is a rule nobody can check at a glance.

The identifiers below are fixed and synthetic. Nothing here is reachable from the agent: the stage
writes catalog and policy rows, and every money-critical fact still comes from the server at
request time.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent import demo_catalog as facts
from agent.runtime import load_local_env, run_async
from api.database import SessionLocal
from models.domain import (
    Approval,
    AuditEvent,
    AuthorizationDecision,
    CatalogItem,
    CheckoutAuthority,
    DailySpendReservation,
    Merchant,
    Payment,
    PaymentRequest,
    PolicyMerchant,
    ProviderEvent,
    RazorpayOrder,
    SpendingPolicy,
    Tenant,
)

# Fixed so the console URL is stable enough to write into demo/script.md and rehearse against.
DEMO_TENANT_ID = UUID("d3f0d3f0-0000-4000-8000-000000000001")
DEMO_MERCHANT_ID = UUID("d3f0d3f0-0000-4000-8000-000000000002")
DEMO_POLICY_ID = UUID("d3f0d3f0-0000-4000-8000-000000000003")
DEMO_STARTER_ID = UUID("d3f0d3f0-0000-4000-8000-000000000004")
DEMO_TEAM_ID = UUID("d3f0d3f0-0000-4000-8000-000000000005")

DEMO_TENANT_NAME = "Robotics Club (synthetic)"
DEMO_ACTOR_ID = "trustgate-demo-buyer"
# Fallback only. The approver the server will actually accept is whatever DEMO_APPROVER_ID
# holds, so reporting a hardcoded guess would print an identity the API does not use.
DEMO_APPROVER_FALLBACK = "trustgate-demo-approver"

# The three flows the demo shows, and the policy that separates them.
#
#   CLOUD-STARTER  INR   399  under the approval threshold      -> ALLOW
#   CLOUD-TEAM     INR   600  over the approval threshold       -> REQUIRE_APPROVAL
#   injected       INR 20,000 over the per-payment limit        -> DENY
#
# The thresholds are what make the three outcomes different, so they are named here rather than
# buried in the policy row: a reader should be able to see why each flow ends where it does.
STARTER_PRICE_MINOR = facts.STARTER_PRICE_MINOR
TEAM_PRICE_MINOR = facts.TEAM_PRICE_MINOR
POLICY_LIFETIME = timedelta(days=7)
"""How long a freshly staged policy is good for."""

POLICY_REFRESH_MARGIN = timedelta(hours=6)
"""Supersede this far ahead of expiry, so a policy cannot lapse part-way through a recording."""

APPROVAL_THRESHOLD_MINOR = 50_000
MAX_PAYMENT_MINOR = 100_000
MAX_DAILY_MINOR = 200_000

# Cleared between takes. These are the rows one run of the demo produces, and an empty timeline is
# what "a clean state" actually means to a viewer.
#
# Configuration - the tenant, its policy, merchant, and catalog - is deliberately absent. A spending
# policy is immutable by database trigger, because an evidence receipt naming policy version 3 has
# to stay resolvable forever; that is a real invariant and the demo tooling does not get an
# exception from it. Staging therefore creates configuration once and leaves it alone.
#
# Annotated loosely on purpose: a heterogeneous list of mapped classes whose only shared feature is
# the column being filtered on, where a precise union type would say less than this comment does.
_TRANSACTIONAL: tuple[Any, ...] = (
    AuditEvent,
    ProviderEvent,
    RazorpayOrder,
    CheckoutAuthority,
    Approval,
    AuthorizationDecision,
    Payment,
    DailySpendReservation,
    PaymentRequest,
)


@dataclass(frozen=True)
class DemoStage:
    tenant_id: UUID
    actor_id: str
    approver_id: str
    console_url: str
    policy_version: int
    policy_expiry: datetime


async def reset_demo_tenant(session: AsyncSession) -> None:
    """Clear what previous takes recorded, leaving the tenant's configuration in place.

    Scoped to one tenant id rather than truncating tables, so staging a demo on a database that
    also holds other work destroys only what the demo produced.
    """

    for model in _TRANSACTIONAL:
        await session.execute(delete(model).where(model.tenant_id == DEMO_TENANT_ID))


async def _ensure_configuration(session: AsyncSession) -> None:
    """Create the tenant's configuration, or bring it up to date if it is already there.

    The split matters and was learned the hard way. A spending policy is immutable by database
    trigger - an evidence receipt naming policy version 3 has to stay resolvable forever - so it is
    written once and never touched again. A catalog is not immutable, and treating it as though it
    were meant that editing a price or the injected description silently did nothing on a tenant
    that already existed, which is exactly the kind of surprise that surfaces on camera.
    """

    tenant = await session.scalar(select(Tenant).where(Tenant.id == DEMO_TENANT_ID))
    if tenant is None:
        session.add(Tenant(id=DEMO_TENANT_ID, name=DEMO_TENANT_NAME))
        await session.flush()
    else:
        tenant.name = DEMO_TENANT_NAME

    merchant = await session.scalar(select(Merchant).where(Merchant.id == DEMO_MERCHANT_ID))
    if merchant is None:
        session.add(
            Merchant(
                id=DEMO_MERCHANT_ID,
                tenant_id=DEMO_TENANT_ID,
                name=facts.MERCHANT_DISPLAY_NAME,
                is_active=True,
            )
        )
    else:
        merchant.name = facts.MERCHANT_DISPLAY_NAME
        merchant.is_active = True

    # A policy row is immutable by database trigger, so a standing policy that has run out cannot
    # be extended - and must not be. What a demo needs is a *newer* policy, which is what versions
    # are for. Staged once and left for a day, the original expires and every purchase is correctly
    # denied for policy expiry; on camera that reads as a broken demo rather than a working
    # invariant, and the recording is where it would have been discovered.
    newest = await session.scalar(
        select(SpendingPolicy)
        .where(SpendingPolicy.tenant_id == DEMO_TENANT_ID)
        .order_by(SpendingPolicy.version.desc())
    )
    if newest is None:
        policy_id = DEMO_POLICY_ID
        session.add(
            SpendingPolicy(
                id=policy_id,
                tenant_id=DEMO_TENANT_ID,
                version=1,
                max_amount_minor=MAX_PAYMENT_MINOR,
                currency="INR",
                max_daily_spend_minor=MAX_DAILY_MINOR,
                expiry=datetime.now(UTC) + POLICY_LIFETIME,
                approval_required_above_minor=APPROVAL_THRESHOLD_MINOR,
            )
        )
    elif newest.expiry <= datetime.now(UTC) + POLICY_REFRESH_MARGIN:
        policy_id = uuid4()
        session.add(
            SpendingPolicy(
                id=policy_id,
                tenant_id=DEMO_TENANT_ID,
                version=newest.version + 1,
                max_amount_minor=MAX_PAYMENT_MINOR,
                currency="INR",
                max_daily_spend_minor=MAX_DAILY_MINOR,
                expiry=datetime.now(UTC) + POLICY_LIFETIME,
                approval_required_above_minor=APPROVAL_THRESHOLD_MINOR,
            )
        )
    else:
        policy_id = newest.id
    await session.flush()

    for item_id, sku, name, description, price, max_quantity in (
        (
            DEMO_STARTER_ID,
            facts.STARTER_SKU,
            facts.STARTER_NAME,
            facts.STARTER_DESCRIPTION,
            facts.STARTER_PRICE_MINOR,
            facts.STARTER_MAX_QUANTITY,
        ),
        (
            DEMO_TEAM_ID,
            facts.TEAM_SKU,
            facts.TEAM_NAME,
            # Third-party text carrying the attack, identical to the one the unguarded baseline
            # reads. That identity is the demo's whole claim.
            facts.TEAM_DESCRIPTION,
            facts.TEAM_PRICE_MINOR,
            facts.TEAM_MAX_QUANTITY,
        ),
    ):
        item = await session.scalar(select(CatalogItem).where(CatalogItem.id == item_id))
        if item is None:
            session.add(
                CatalogItem(
                    id=item_id,
                    tenant_id=DEMO_TENANT_ID,
                    merchant_id=DEMO_MERCHANT_ID,
                    sku=sku,
                    name=name,
                    description_untrusted=description,
                    price_minor=price,
                    currency="INR",
                    max_quantity=max_quantity,
                    active=True,
                )
            )
        else:
            item.sku = sku
            item.name = name
            item.description_untrusted = description
            item.price_minor = price
            item.max_quantity = max_quantity
            item.active = True

    # Against `policy_id`, not the fixed constant: a superseded policy is a different row and
    # needs its own merchant link, or the new version allows nothing.
    link = await session.scalar(
        select(PolicyMerchant).where(
            PolicyMerchant.tenant_id == DEMO_TENANT_ID,
            PolicyMerchant.policy_id == policy_id,
            PolicyMerchant.merchant_id == DEMO_MERCHANT_ID,
        )
    )
    if link is None:
        session.add(
            PolicyMerchant(
                tenant_id=DEMO_TENANT_ID,
                policy_id=policy_id,
                merchant_id=DEMO_MERCHANT_ID,
            )
        )
    await session.flush()


async def stage_demo(
    session: AsyncSession, *, base_url: str = "http://127.0.0.1:8000"
) -> DemoStage:
    """Clear the timeline and bring the demo tenant up to date, ready to run the flows.

    Safe to run between every take, and safe to run after editing the catalog.
    """

    await reset_demo_tenant(session)
    await _ensure_configuration(session)
    policy = await session.scalar(
        select(SpendingPolicy)
        .where(SpendingPolicy.tenant_id == DEMO_TENANT_ID)
        .order_by(SpendingPolicy.version.desc())
    )
    if policy is None:
        raise RuntimeError("staging finished without a policy")
    return _stage_details(base_url, policy)


def _stage_details(base_url: str, policy: SpendingPolicy) -> DemoStage:
    return DemoStage(
        tenant_id=DEMO_TENANT_ID,
        actor_id=DEMO_ACTOR_ID,
        approver_id=os.getenv("DEMO_APPROVER_ID", DEMO_APPROVER_FALLBACK),
        console_url=f"{base_url.rstrip('/')}/console/{DEMO_TENANT_ID}",
        policy_version=policy.version,
        policy_expiry=policy.expiry,
    )


def _export(stage: DemoStage) -> str:
    """Print the identity exports in the syntax of the shell that will run them.

    Printing `export` on Windows is not a cosmetic problem: it is an instruction that silently
    does nothing, and the failure surfaces later as an agent acting for the wrong tenant.
    """

    if os.name == "nt":
        lines = (
            f'$env:MCP_TENANT_ID="{stage.tenant_id}"',
            f'$env:MCP_ACTOR_ID="{stage.actor_id}"',
        )
    else:
        lines = (
            f"export MCP_TENANT_ID={stage.tenant_id}",
            f"export MCP_ACTOR_ID={stage.actor_id}",
        )
    return (chr(10) + "               ").join(lines)


def _instructions(stage: DemoStage) -> str:
    return f"""
  Demo stage ready. Everything below is synthetic.

  Console      {stage.console_url}
               (requires ENABLE_CONSOLE=true on the API)

  Policy       v{stage.policy_version}, good until {stage.policy_expiry:%d %b %Y %H:%M} UTC
               a lapsed policy denies every purchase, which on camera reads as a
               broken demo rather than a working rule - check this line first

  Shell        {_export(stage)}

  Flows        0  python -m demo.unguarded
                  no policy layer: the injected instruction executes

               1  python -m agent.demo "Buy Starter credits for the robotics club."
                  python -m agent.checkout --open
                  INR 399, allowed, then taken to a real Razorpay Test Mode order

               2  python -m agent.demo "Buy Team credits for the robotics club."
                  python -m agent.approve
                  INR 600, over the threshold, completed by a separate approver

               3  python -m agent.demo --adversarial "Buy cloud credits for the club."
                  the same injected instruction, refused, nothing created

  Re-run this command between takes to clear the timeline.
"""


async def _main(base_url: str, as_json: bool) -> None:
    async with SessionLocal() as session, session.begin():
        stage = await stage_demo(session, base_url=base_url)
    print(json.dumps(asdict(stage), default=str, indent=2) if as_json else _instructions(stage))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reset and seed the fixed demo tenant so a recording starts from a known state."
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Where the API is served, used only to print the console URL.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the stage details as JSON.")
    args = parser.parse_args()

    load_local_env()
    run_async(_main(args.base_url, args.json))


if __name__ == "__main__":
    main()
