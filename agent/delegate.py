"""Walk a delegation chain on screen, including the two things it refuses.

Four hops, one spend, and two refusals that are the point of the whole feature. The first refusal
is a sibling asking for budget its parent has already promised away - which every per-edge
comparison in the capability literature would allow, because those compare a child against its
parent and never the children against each other. The second is a hop dying because something
above it was revoked, without its own row ever being written to.

Run `python -m agent.stage` first: this uses that tenant and its policy, and clears only its own
delegations so it can be run between takes.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.demo_catalog import STARTER_SKU, TEAM_SKU
from agent.runtime import load_local_env, run_async
from agent.stage import DEMO_TENANT_ID
from api.database import SessionLocal
from delegation.chain import (
    MAX_DEPTH,
    Bounds,
    DelegationRefused,
    grant,
    grant_root,
    resolve_chain,
    revoke,
    spend,
)
from models.domain import Delegation, SpendingPolicy

PRINCIPAL = "finance-lead@demo"
BUYER = "demo-buyer-agent"
PROCUREMENT = "demo-procurement-agent"
RENEWALS = "demo-renewals-agent"
OVERFLOW = "demo-overflow-agent"


def _rupees(minor: int) -> str:
    return f"INR {minor / 100:,.2f}"


@dataclass(frozen=True)
class Demonstration:
    """What the walkthrough actually observed.

    Returned rather than only printed, because a demo that quietly stops demonstrating its point
    still prints beautifully. The test asserts on these.
    """

    scope_refusal: str | None
    sibling_refusal: str | None
    revocation_refusal: str | None
    descendant_revoked_at: datetime | None
    root_free_minor: int


async def _clear(session: AsyncSession) -> None:
    """Deepest hop first: a delegation points at its parent with RESTRICT."""

    for depth in range(MAX_DEPTH, -1, -1):
        await session.execute(
            delete(Delegation).where(
                Delegation.tenant_id == DEMO_TENANT_ID, Delegation.depth == depth
            )
        )


async def _policy(session: AsyncSession) -> SpendingPolicy:
    policy = await session.scalar(
        select(SpendingPolicy)
        .where(SpendingPolicy.tenant_id == DEMO_TENANT_ID)
        .order_by(SpendingPolicy.version.desc())
    )
    if policy is None:
        raise SystemExit(
            "No demo policy found. Run `python -m agent.stage` first - it creates the tenant "
            "and the policy this chain is cut from."
        )
    return policy


async def _print_tree(session: AsyncSession, leaf_id: UUID) -> None:
    chain = await resolve_chain(session, tenant_id=DEMO_TENANT_ID, delegation_id=leaf_id)
    for hop in chain:
        room = hop.budget_minor - hop.allocated_minor - hop.spent_minor
        print(
            f"    {'  ' * hop.depth}{'|_ ' if hop.depth else ''}{hop.delegate_actor_id:<24}"
            f"holds {_rupees(hop.budget_minor):>14}"
            f"   promised down {_rupees(hop.allocated_minor):>14}"
            f"   spent {_rupees(hop.spent_minor):>13}"
            f"   free {_rupees(room):>14}"
        )


async def demonstrate(session: AsyncSession) -> Demonstration:
    """Build a chain, spend inside it, and provoke the two refusals that are the point."""

    policy = await _policy(session)
    # Same reason as the ids below: a rollback expires this row too, and its expiry is
    # read while building bounds long after the first refusal.
    policy_expiry = policy.expiry
    policy_cap = policy.max_amount_minor
    policy_daily = policy.max_daily_spend_minor
    await _clear(session)
    await session.flush()

    print()
    print("A human delegates a budget, and every hop below narrows it.")
    print()

    root = await grant_root(
        session,
        tenant_id=DEMO_TENANT_ID,
        policy=policy,
        principal_actor_id=PRINCIPAL,
        delegate_actor_id=BUYER,
        bounds=Bounds(
            budget_minor=policy_daily,
            max_amount_minor=policy_cap,
            allowed_skus=(STARTER_SKU, TEAM_SKU),
            purpose="cloud credits for the robotics club",
            expires_at=policy_expiry,
        ),
    )
    procurement = await grant(
        session,
        tenant_id=DEMO_TENANT_ID,
        parent_id=root.id,
        delegator_actor_id=BUYER,
        delegate_actor_id=PROCUREMENT,
        bounds=Bounds(
            budget_minor=120_000,
            max_amount_minor=80_000,
            allowed_skus=(STARTER_SKU, TEAM_SKU),
            purpose="cloud credits",
            expires_at=policy_expiry,
        ),
    )
    renewals = await grant(
        session,
        tenant_id=DEMO_TENANT_ID,
        parent_id=procurement.id,
        delegator_actor_id=PROCUREMENT,
        delegate_actor_id=RENEWALS,
        bounds=Bounds(
            budget_minor=60_000,
            max_amount_minor=40_000,
            allowed_skus=(STARTER_SKU,),
            purpose="starter renewals only",
            expires_at=policy_expiry,
        ),
    )
    await session.flush()

    # Every refusal below rolls back, which expires these rows. Reading an expired attribute
    # afterwards is a lazy load, and a lazy load from a sync frame is a MissingGreenlet - so
    # what the rest of this needs is held as plain values from here on.
    root_id, procurement_id, renewals_id = root.id, procurement.id, renewals.id
    root_budget = root.budget_minor

    await _print_tree(session, renewals_id)

    print()
    print(f"  The leaf spends {_rupees(40_000)} on {STARTER_SKU}, inside every bound above it.")
    await spend(
        session,
        tenant_id=DEMO_TENANT_ID,
        delegation_id=renewals_id,
        amount_minor=40_000,
        sku=STARTER_SKU,
    )
    await session.flush()
    await _print_tree(session, renewals_id)

    print()
    print("  The leaf was narrowed to starter renewals, so the team SKU is out of scope:")
    attempt = await session.begin_nested()
    try:
        await spend(
            session,
            tenant_id=DEMO_TENANT_ID,
            delegation_id=renewals_id,
            amount_minor=10_000,
            sku=TEAM_SKU,
        )
        await attempt.commit()
        scope_refusal = None
        print("    NOT REFUSED - the scope narrowing did not hold")
    except DelegationRefused as refused:
        await attempt.rollback()
        scope_refusal = refused.reason
        print(f"    refused: {refused.reason}")

    print()
    print("  Now the finding. The root holds", _rupees(root_budget), end=" ")
    print(f"and has promised {_rupees(120_000)} to {PROCUREMENT}.")
    print(f"  A second child asks for another {_rupees(120_000)}.")
    print("  Per-edge narrowing allows it: the child is no wider than its parent.")
    attempt = await session.begin_nested()
    try:
        await grant(
            session,
            tenant_id=DEMO_TENANT_ID,
            parent_id=root_id,
            delegator_actor_id=BUYER,
            delegate_actor_id=OVERFLOW,
            bounds=Bounds(
                budget_minor=120_000,
                max_amount_minor=80_000,
                allowed_skus=(STARTER_SKU,),
                purpose="overflow",
                expires_at=policy_expiry,
            ),
        )
        await attempt.commit()
        sibling_refusal = None
        print("    NOT REFUSED - the siblings together now hold more than their parent")
    except DelegationRefused as refused:
        await attempt.rollback()
        sibling_refusal = refused.reason
        print(f"    refused: {refused.reason}")
        print("    The budget was partitioned, not compared. Siblings share one pool.")

    print()
    print(f"  What is actually left fits: {_rupees(80_000)}.")
    await grant(
        session,
        tenant_id=DEMO_TENANT_ID,
        parent_id=root_id,
        delegator_actor_id=BUYER,
        delegate_actor_id=OVERFLOW,
        bounds=Bounds(
            budget_minor=80_000,
            max_amount_minor=80_000,
            allowed_skus=(STARTER_SKU,),
            purpose="overflow",
            expires_at=policy_expiry,
        ),
    )
    await session.flush()
    await _print_tree(session, renewals_id)

    print()
    print(f"  The human revokes {PROCUREMENT}, in the middle of the chain.")
    await revoke(session, tenant_id=DEMO_TENANT_ID, delegation_id=procurement_id)
    await session.flush()

    print(f"  {RENEWALS} is not touched, and is not told. Its next spend:")
    attempt = await session.begin_nested()
    try:
        await spend(
            session,
            tenant_id=DEMO_TENANT_ID,
            delegation_id=renewals_id,
            amount_minor=1_000,
            sku=STARTER_SKU,
        )
        await attempt.commit()
        revocation_refusal = None
        print("    NOT REFUSED - revocation did not reach the branch")
    except DelegationRefused as refused:
        await attempt.rollback()
        revocation_refusal = refused.reason
        print(f"    refused: {refused.reason}")

    still_open = await session.scalar(
        select(Delegation.revoked_at).where(Delegation.id == renewals_id)
    )
    print(f"    its own revoked_at is still {still_open}")
    print()
    print("  A signed capability would have to be hunted down and recalled. This one is")
    print("  re-derived from its whole chain on every spend, so cutting a link above it is")
    print("  already the end of the branch.")
    print()

    root_row = await session.scalar(select(Delegation).where(Delegation.id == root_id))
    if root_row is None:
        # Not an assert: `python -O` strips those, and this repository has a test saying so.
        raise RuntimeError("the root delegation disappeared during the walkthrough")
    return Demonstration(
        scope_refusal=scope_refusal,
        sibling_refusal=sibling_refusal,
        revocation_refusal=revocation_refusal,
        descendant_revoked_at=still_open,
        root_free_minor=(root_row.budget_minor - root_row.allocated_minor - root_row.spent_minor),
    )


async def _run() -> None:
    async with SessionLocal() as session:
        await demonstrate(session)
        await session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    load_local_env()
    print(f"tenant {DEMO_TENANT_ID}   {datetime.now(UTC).isoformat(timespec='seconds')}")
    run_async(_run())


if __name__ == "__main__":
    main()
