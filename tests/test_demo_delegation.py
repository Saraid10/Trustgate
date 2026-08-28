"""The delegation walkthrough must keep demonstrating the thing it claims to demonstrate.

A demo that stops proving its point does not fail. It prints the same headings, the tree still
renders, and the refusal it was built around quietly stops happening. That is worth a test for the
same reason the mutation suite exists: the failure mode is silence, not noise.

`demonstrate` therefore returns what it observed instead of only printing it.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.delegate import OVERFLOW, PROCUREMENT, RENEWALS, demonstrate
from agent.stage import DEMO_TENANT_ID, stage_demo
from models.domain import Delegation


async def test_the_walkthrough_provokes_all_three_refusals(async_session: AsyncSession) -> None:
    await stage_demo(async_session)

    observed = await demonstrate(async_session)

    assert observed.scope_refusal == "DELEGATION_SKU_OUT_OF_SCOPE"
    assert observed.sibling_refusal == "DELEGATION_BUDGET_EXHAUSTED"
    assert observed.revocation_refusal == "DELEGATION_REVOKED"


async def test_the_revoked_hop_kills_a_descendant_it_never_wrote_to(
    async_session: AsyncSession,
) -> None:
    """The claim the closing narration makes, asserted rather than narrated."""

    await stage_demo(async_session)

    observed = await demonstrate(async_session)

    assert observed.revocation_refusal == "DELEGATION_REVOKED"
    assert observed.descendant_revoked_at is None, (
        "the descendant was written to; the point of the closing beat is that it was not"
    )


async def test_the_root_ends_fully_allocated_and_not_overdrawn(
    async_session: AsyncSession,
) -> None:
    """The sibling that fits takes exactly what is left, and no more exists to take."""

    await stage_demo(async_session)

    observed = await demonstrate(async_session)

    assert observed.root_free_minor == 0


async def test_the_walkthrough_builds_the_chain_it_narrates(async_session: AsyncSession) -> None:
    """Four hops: the root, two under it, and one under the first of those."""

    await stage_demo(async_session)

    await demonstrate(async_session)

    hops = (
        await async_session.execute(
            select(Delegation.delegate_actor_id, Delegation.depth)
            .where(Delegation.tenant_id == DEMO_TENANT_ID)
            .order_by(Delegation.depth)
        )
    ).all()
    by_actor = {actor: depth for actor, depth in hops}

    assert by_actor[PROCUREMENT] == 1
    assert by_actor[OVERFLOW] == 1, "the sibling that fits sits beside the first, not below it"
    assert by_actor[RENEWALS] == 2


async def test_running_it_twice_films_one_chain_not_two(async_session: AsyncSession) -> None:
    """It clears its own delegations, for the same reason staging clears the timeline."""

    await stage_demo(async_session)
    await demonstrate(async_session)
    await demonstrate(async_session)

    hops = await async_session.scalar(
        select(func.count()).select_from(Delegation).where(Delegation.tenant_id == DEMO_TENANT_ID)
    )

    assert hops == 4, f"a second run left {hops} hops behind instead of four"
