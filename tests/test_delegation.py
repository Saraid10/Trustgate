"""Delegated authority narrows at every hop, and the hops below a node cannot outgrow it.

Two separate properties, and the second is the one that gets missed. Per-edge narrowing is what
every capability system means by attenuation: a child may do no more than its parent. Aggregate
partitioning is what money additionally requires, because two children that each satisfy per-edge
narrowing still spend twice the parent's budget between them.

`test_two_children_cannot_together_outspend_their_parent` is the test that separates them. Delete
the aggregate claim and every other test in this file still passes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fixtures import FixtureData
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from delegation.chain import (
    Bounds,
    DelegationRefused,
    grant,
    grant_root,
    release,
    resolve_chain,
    revoke,
    spend,
)
from models.domain import Delegation, DelegationSpend, SpendingPolicy

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
"""Pinned, for the reason the daily-spend race test is pinned: a clock read is not a fixture."""

SKUS = ("CLOUD-STARTER", "CLOUD-TEAM")


def _bounds(
    *,
    budget: int,
    cap: int = 100_000,
    skus: tuple[str, ...] = SKUS,
    days: int = 7,
    purpose: str = "procurement",
) -> Bounds:
    return Bounds(
        budget_minor=budget,
        max_amount_minor=cap,
        allowed_skus=skus,
        purpose=purpose,
        expires_at=NOW + timedelta(days=days),
    )


async def _root(session: AsyncSession, data: FixtureData, *, budget: int = 100_000) -> Delegation:
    return await grant_root(
        session,
        tenant_id=data.tenant_a.id,
        policy=data.tenant_a_policy,
        principal_actor_id="human-principal",
        delegate_actor_id="agent-a",
        bounds=_bounds(budget=budget),
    )


# --- the finding ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_children_cannot_together_outspend_their_parent(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Per-edge narrowing is satisfied twice over and the parent is still overdrawn.

    Each child asks for exactly the parent's budget, which no comparison against the parent
    forbids. Only a partition does.
    """

    root = await _root(async_session, seeded_fixture_data, budget=50_000)

    await grant(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        parent_id=root.id,
        delegator_actor_id="agent-a",
        delegate_actor_id="agent-b",
        bounds=_bounds(budget=50_000),
    )

    with pytest.raises(DelegationRefused) as refused:
        await grant(
            async_session,
            tenant_id=seeded_fixture_data.tenant_a.id,
            parent_id=root.id,
            delegator_actor_id="agent-a",
            delegate_actor_id="agent-c",
            bounds=_bounds(budget=50_000),
        )

    assert refused.value.reason == "DELEGATION_BUDGET_EXHAUSTED"


@pytest.mark.asyncio
async def test_the_database_refuses_an_overdrawn_parent_even_without_the_application(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The conditional claim is the first guard, not the only one.

    This writes the allocation the way a careless caller would - read, add, write - and the row's
    own constraint refuses it.
    """

    root = await _root(async_session, seeded_fixture_data, budget=50_000)

    with pytest.raises(DBAPIError) as raised:
        async with async_session.begin_nested():
            await async_session.execute(
                update(Delegation).where(Delegation.id == root.id).values(allocated_minor=60_000)
            )

    assert "ck_delegation_budget_partitioned" in str(raised.value)


@pytest.mark.asyncio
async def test_a_node_cannot_spend_what_it_has_already_promised_downward(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Allocation and spending draw on one budget, not two."""

    root = await _root(async_session, seeded_fixture_data, budget=50_000)
    await grant(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        parent_id=root.id,
        delegator_actor_id="agent-a",
        delegate_actor_id="agent-b",
        bounds=_bounds(budget=40_000),
    )

    with pytest.raises(DelegationRefused) as refused:
        await spend(
            async_session,
            tenant_id=seeded_fixture_data.tenant_a.id,
            delegation_id=root.id,
            amount_minor=20_000,
            reference=uuid4(),
            sku="CLOUD-STARTER",
            as_of=NOW,
        )

    assert refused.value.reason == "DELEGATION_BUDGET_EXHAUSTED"


# --- per-edge narrowing, enforced by the trigger -------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bounds", "expected"),
    [
        (_bounds(budget=10_000, cap=200_000), "per-payment cap may not exceed its parent"),
        (_bounds(budget=10_000, days=99), "may not outlive its parent"),
        (_bounds(budget=10_000, skus=("CLOUD-STARTER", "CLOUD-ENTERPRISE")), "may not widen"),
    ],
    ids=["payment-cap", "expiry", "scope"],
)
async def test_a_child_may_not_widen_its_parent(
    async_session: AsyncSession,
    seeded_fixture_data: FixtureData,
    bounds: Bounds,
    expected: str,
) -> None:
    """Every dimension narrows, and the database is what says so."""

    root = await _root(async_session, seeded_fixture_data)

    with pytest.raises(DBAPIError) as raised:
        async with async_session.begin_nested():
            await grant(
                async_session,
                tenant_id=seeded_fixture_data.tenant_a.id,
                parent_id=root.id,
                delegator_actor_id="agent-a",
                delegate_actor_id="agent-b",
                bounds=bounds,
            )

    assert expected in str(raised.value)


@pytest.mark.asyncio
async def test_an_over_wide_child_is_refused_before_the_trigger_is_reached(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Budget is the one dimension two guards both cover, and the claim gets there first."""

    root = await _root(async_session, seeded_fixture_data, budget=100_000)

    with pytest.raises(DelegationRefused) as refused:
        await grant(
            async_session,
            tenant_id=seeded_fixture_data.tenant_a.id,
            parent_id=root.id,
            delegator_actor_id="agent-a",
            delegate_actor_id="agent-b",
            bounds=_bounds(budget=200_000),
        )

    assert refused.value.reason == "DELEGATION_BUDGET_EXHAUSTED"


@pytest.mark.asyncio
async def test_the_trigger_refuses_an_over_wide_child_written_around_the_claim(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The second guard, reached by writing the row the way `grant` never would.

    Without this the budget edge rests entirely on application code, and the whole argument of
    this project is that an invariant resting on application code is an invariant on trust.
    """

    root = await _root(async_session, seeded_fixture_data, budget=100_000)

    with pytest.raises(DBAPIError) as raised:
        async with async_session.begin_nested():
            async_session.add(
                Delegation(
                    id=uuid4(),
                    tenant_id=seeded_fixture_data.tenant_a.id,
                    parent_id=root.id,
                    depth=1,
                    policy_id=root.policy_id,
                    policy_version=root.policy_version,
                    root_actor_id=root.root_actor_id,
                    delegator_actor_id="agent-a",
                    delegate_actor_id="agent-b",
                    budget_minor=200_000,
                    allocated_minor=0,
                    spent_minor=0,
                    max_amount_minor=root.max_amount_minor,
                    allowed_skus=list(SKUS),
                    purpose="written around the claim",
                    expires_at=root.expires_at,
                )
            )
            await async_session.flush()

    assert "budget may not exceed its parent" in str(raised.value)


@pytest.mark.asyncio
async def test_only_the_holder_of_a_delegation_may_delegate_it_onward(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Otherwise any actor could mint a hop under someone else's authority."""

    root = await _root(async_session, seeded_fixture_data)

    with pytest.raises(DBAPIError) as raised:
        async with async_session.begin_nested():
            await grant(
                async_session,
                tenant_id=seeded_fixture_data.tenant_a.id,
                parent_id=root.id,
                delegator_actor_id="agent-impostor",
                delegate_actor_id="agent-b",
                bounds=_bounds(budget=10_000),
            )

    assert "only the holder of a delegation may delegate it onward" in str(raised.value)


# --- the chain -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_chain_resolves_root_first_and_keeps_its_principal(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    root = await _root(async_session, seeded_fixture_data)
    child = await grant(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        parent_id=root.id,
        delegator_actor_id="agent-a",
        delegate_actor_id="agent-b",
        bounds=_bounds(budget=40_000),
    )
    grandchild = await grant(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        parent_id=child.id,
        delegator_actor_id="agent-b",
        delegate_actor_id="agent-c",
        bounds=_bounds(budget=10_000),
    )

    chain = await resolve_chain(
        async_session, tenant_id=seeded_fixture_data.tenant_a.id, delegation_id=grandchild.id
    )

    assert [hop.depth for hop in chain] == [0, 1, 2]
    assert {hop.root_actor_id for hop in chain} == {"human-principal"}


@pytest.mark.asyncio
async def test_revoking_an_ancestor_stops_a_descendant_that_was_never_touched(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """This is what a signed capability cannot do.

    A token that carries its own authority has to be hunted down and recalled. A hop re-derives
    its authority from the whole chain every time it spends, so cutting the chain above it is
    already the end of the branch - no revocation list, no recall.
    """

    root = await _root(async_session, seeded_fixture_data)
    child = await grant(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        parent_id=root.id,
        delegator_actor_id="agent-a",
        delegate_actor_id="agent-b",
        bounds=_bounds(budget=40_000),
    )
    grandchild = await grant(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        parent_id=child.id,
        delegator_actor_id="agent-b",
        delegate_actor_id="agent-c",
        bounds=_bounds(budget=10_000),
    )

    await spend(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        delegation_id=grandchild.id,
        amount_minor=1_000,
        reference=uuid4(),
        sku="CLOUD-STARTER",
        as_of=NOW,
    )

    await revoke(async_session, tenant_id=seeded_fixture_data.tenant_a.id, delegation_id=child.id)

    with pytest.raises(DelegationRefused) as refused:
        await spend(
            async_session,
            tenant_id=seeded_fixture_data.tenant_a.id,
            delegation_id=grandchild.id,
            amount_minor=1_000,
            reference=uuid4(),
            sku="CLOUD-STARTER",
            as_of=NOW,
        )

    assert refused.value.reason == "DELEGATION_REVOKED"
    still_open = await async_session.scalar(
        select(Delegation.revoked_at).where(Delegation.id == grandchild.id)
    )
    assert still_open is None, "the descendant died without being written to"


@pytest.mark.asyncio
async def test_a_hop_is_bound_by_the_narrowest_cap_above_it(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """A leaf cannot spend what an ancestor would have refused."""

    root = await _root(async_session, seeded_fixture_data)
    child = await grant(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        parent_id=root.id,
        delegator_actor_id="agent-a",
        delegate_actor_id="agent-b",
        bounds=_bounds(budget=40_000, cap=5_000),
    )
    grandchild = await grant(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        parent_id=child.id,
        delegator_actor_id="agent-b",
        delegate_actor_id="agent-c",
        bounds=_bounds(budget=20_000, cap=5_000),
    )

    with pytest.raises(DelegationRefused) as refused:
        await spend(
            async_session,
            tenant_id=seeded_fixture_data.tenant_a.id,
            delegation_id=grandchild.id,
            amount_minor=9_000,
            reference=uuid4(),
            sku="CLOUD-STARTER",
            as_of=NOW,
        )

    assert refused.value.reason == "DELEGATION_AMOUNT_EXCEEDS_HOP_LIMIT"


@pytest.mark.asyncio
async def test_a_purpose_narrowed_at_one_hop_stays_narrowed_below_it(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The consent scope travels the chain instead of being re-widened at the bottom."""

    root = await _root(async_session, seeded_fixture_data)
    child = await grant(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        parent_id=root.id,
        delegator_actor_id="agent-a",
        delegate_actor_id="agent-b",
        bounds=_bounds(budget=40_000, skus=("CLOUD-STARTER",)),
    )

    with pytest.raises(DelegationRefused) as refused:
        await spend(
            async_session,
            tenant_id=seeded_fixture_data.tenant_a.id,
            delegation_id=child.id,
            amount_minor=1_000,
            reference=uuid4(),
            sku="CLOUD-TEAM",
            as_of=NOW,
        )

    assert refused.value.reason == "DELEGATION_SKU_OUT_OF_SCOPE"


@pytest.mark.asyncio
async def test_an_expired_hop_stops_the_branch_below_it(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    root = await _root(async_session, seeded_fixture_data)
    child = await grant(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        parent_id=root.id,
        delegator_actor_id="agent-a",
        delegate_actor_id="agent-b",
        bounds=_bounds(budget=40_000, days=2),
    )

    with pytest.raises(DelegationRefused) as refused:
        await spend(
            async_session,
            tenant_id=seeded_fixture_data.tenant_a.id,
            delegation_id=child.id,
            amount_minor=1_000,
            reference=uuid4(),
            sku="CLOUD-STARTER",
            as_of=NOW + timedelta(days=3),
        )

    assert refused.value.reason == "DELEGATION_EXPIRED"


# --- the root and the policy behind it -----------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bounds", "reason"),
    [
        (_bounds(budget=999_000), "DELEGATION_EXCEEDS_POLICY_DAILY_LIMIT"),
        (_bounds(budget=1_000, cap=999_000), "DELEGATION_EXCEEDS_POLICY_PAYMENT_LIMIT"),
        (_bounds(budget=1_000, days=999), "DELEGATION_OUTLIVES_POLICY"),
    ],
    ids=["daily-limit", "payment-limit", "expiry"],
)
async def test_a_root_cannot_exceed_the_policy_it_is_cut_from(
    async_session: AsyncSession,
    seeded_fixture_data: FixtureData,
    bounds: Bounds,
    reason: str,
) -> None:
    with pytest.raises(DelegationRefused) as refused:
        await grant_root(
            async_session,
            tenant_id=seeded_fixture_data.tenant_a.id,
            policy=seeded_fixture_data.tenant_a_policy,
            principal_actor_id="human-principal",
            delegate_actor_id="agent-a",
            bounds=bounds,
        )

    assert refused.value.reason == reason


@pytest.mark.asyncio
async def test_a_chain_dies_when_the_policy_it_was_cut_from_is_superseded(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Revocation that reaches work already in flight, which a signed mandate cannot offer."""

    root = await _root(async_session, seeded_fixture_data)
    await async_session.execute(
        text("ALTER TABLE spending_policy DISABLE TRIGGER spending_policy_immutable")
    )
    policy = seeded_fixture_data.tenant_a_policy
    await async_session.execute(
        update(SpendingPolicy).where(SpendingPolicy.id == policy.id).values(version=2)
    )
    await async_session.execute(
        text("ALTER TABLE spending_policy ENABLE TRIGGER spending_policy_immutable")
    )

    with pytest.raises(DelegationRefused) as refused:
        await spend(
            async_session,
            tenant_id=seeded_fixture_data.tenant_a.id,
            delegation_id=root.id,
            amount_minor=1_000,
            reference=uuid4(),
            sku="CLOUD-STARTER",
            as_of=NOW,
        )

    assert refused.value.reason == "DELEGATION_POLICY_DRIFT"


@pytest.mark.asyncio
async def test_a_hop_that_does_not_exist_is_not_a_chain(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    with pytest.raises(DelegationRefused) as refused:
        await resolve_chain(
            async_session, tenant_id=seeded_fixture_data.tenant_a.id, delegation_id=uuid4()
        )

    assert refused.value.reason == "DELEGATION_NOT_FOUND"


@pytest.mark.asyncio
async def test_revoking_twice_is_refused_rather_than_silently_accepted(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    root = await _root(async_session, seeded_fixture_data)
    await revoke(async_session, tenant_id=seeded_fixture_data.tenant_a.id, delegation_id=root.id)

    with pytest.raises(DelegationRefused) as refused:
        await revoke(
            async_session, tenant_id=seeded_fixture_data.tenant_a.id, delegation_id=root.id
        )

    assert refused.value.reason == "DELEGATION_ALREADY_REVOKED"


@pytest.mark.asyncio
async def test_a_successful_spend_is_recorded_against_the_hop_that_made_it(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    root = await _root(async_session, seeded_fixture_data)
    child = await grant(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        parent_id=root.id,
        delegator_actor_id="agent-a",
        delegate_actor_id="agent-b",
        bounds=_bounds(budget=40_000),
    )

    await spend(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        delegation_id=child.id,
        amount_minor=7_000,
        reference=uuid4(),
        sku="CLOUD-STARTER",
        as_of=NOW,
    )

    spent = await async_session.scalar(
        select(Delegation.spent_minor).where(Delegation.id == child.id)
    )
    root_spent = await async_session.scalar(
        select(Delegation.spent_minor).where(Delegation.id == root.id)
    )
    assert spent == 7_000
    assert root_spent == 0, "the leaf spent it, not the root"


# --- what integration will hand it -----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [-100, 0], ids=["negative", "zero"])
async def test_a_spend_must_be_a_positive_amount(
    async_session: AsyncSession, seeded_fixture_data: FixtureData, amount: int
) -> None:
    """A negative spend is a refund nobody authorized.

    Every bound here is an upper bound, so a negative amount passes all of them and the atomic
    claim then subtracts from `spent_minor`, handing budget back. Nothing in this module is the
    right place to decide that a caller meant it.
    """

    root = await _root(async_session, seeded_fixture_data, budget=50_000)
    await spend(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        delegation_id=root.id,
        amount_minor=10_000,
        reference=uuid4(),
        sku="CLOUD-STARTER",
        as_of=NOW,
    )

    with pytest.raises(DelegationRefused) as refused:
        await spend(
            async_session,
            tenant_id=seeded_fixture_data.tenant_a.id,
            delegation_id=root.id,
            amount_minor=amount,
            reference=uuid4(),
            sku="CLOUD-STARTER",
            as_of=NOW,
        )

    assert refused.value.reason == "DELEGATION_AMOUNT_NOT_POSITIVE"
    still_spent = await async_session.scalar(
        select(Delegation.spent_minor).where(Delegation.id == root.id)
    )
    assert still_spent == 10_000, "the refused spend moved the budget anyway"


@pytest.mark.asyncio
async def test_a_grant_must_carry_a_positive_budget(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """A negative child budget would credit its parent's allocation on the way past."""

    root = await _root(async_session, seeded_fixture_data, budget=50_000)

    with pytest.raises(DelegationRefused) as refused:
        await grant(
            async_session,
            tenant_id=seeded_fixture_data.tenant_a.id,
            parent_id=root.id,
            delegator_actor_id="agent-a",
            delegate_actor_id="agent-b",
            bounds=_bounds(budget=-10_000),
        )

    assert refused.value.reason == "DELEGATION_BUDGET_NOT_POSITIVE"


# --- idempotency and release ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spending_twice_under_one_reference_charges_once(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """An authorization path that retries must not charge the chain twice.

    A retry is not an error and must not look like one: the second call returns exactly as the
    first did, and the budget moves once.
    """

    root = await _root(async_session, seeded_fixture_data, budget=50_000)
    reference = uuid4()

    for _ in range(3):
        await spend(
            async_session,
            tenant_id=seeded_fixture_data.tenant_a.id,
            delegation_id=root.id,
            amount_minor=10_000,
            sku="CLOUD-STARTER",
            reference=reference,
            as_of=NOW,
        )

    spent = await async_session.scalar(
        select(Delegation.spent_minor).where(Delegation.id == root.id)
    )
    ledger = await async_session.scalar(
        select(func.count())
        .select_from(DelegationSpend)
        .where(DelegationSpend.reference == reference)
    )
    assert spent == 10_000, "a retry charged the chain again"
    assert ledger == 1


@pytest.mark.asyncio
async def test_a_released_spend_returns_the_budget_to_the_hop(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Budget held for a payment that never happened has to come back."""

    root = await _root(async_session, seeded_fixture_data, budget=50_000)
    reference = uuid4()
    await spend(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        delegation_id=root.id,
        amount_minor=30_000,
        sku="CLOUD-STARTER",
        reference=reference,
        as_of=NOW,
    )

    await release(async_session, tenant_id=seeded_fixture_data.tenant_a.id, reference=reference)

    spent = await async_session.scalar(
        select(Delegation.spent_minor).where(Delegation.id == root.id)
    )
    assert spent == 0


@pytest.mark.asyncio
async def test_a_spend_cannot_be_released_twice(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Otherwise a chain grows every time a release is retried."""

    root = await _root(async_session, seeded_fixture_data, budget=50_000)
    reference = uuid4()
    await spend(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        delegation_id=root.id,
        amount_minor=30_000,
        sku="CLOUD-STARTER",
        reference=reference,
        as_of=NOW,
    )
    await release(async_session, tenant_id=seeded_fixture_data.tenant_a.id, reference=reference)

    with pytest.raises(DelegationRefused) as refused:
        await release(async_session, tenant_id=seeded_fixture_data.tenant_a.id, reference=reference)

    assert refused.value.reason == "DELEGATION_SPEND_NOT_RELEASABLE"
    spent = await async_session.scalar(
        select(Delegation.spent_minor).where(Delegation.id == root.id)
    )
    assert spent == 0, "a second release credited the hop again"


@pytest.mark.asyncio
async def test_releasing_a_reference_that_never_spent_is_refused(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    await _root(async_session, seeded_fixture_data, budget=50_000)

    with pytest.raises(DelegationRefused) as refused:
        await release(async_session, tenant_id=seeded_fixture_data.tenant_a.id, reference=uuid4())

    assert refused.value.reason == "DELEGATION_SPEND_NOT_RELEASABLE"


@pytest.mark.asyncio
async def test_a_refused_spend_leaves_no_ledger_row_behind(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The ledger row is written before the claim, so a refusal has to take it back.

    A caller that catches the refusal and carries on in the same transaction would otherwise have
    burned the reference: the retry would find a conflict and report success for a spend that
    never happened.
    """

    root = await _root(async_session, seeded_fixture_data, budget=10_000)
    reference = uuid4()

    with pytest.raises(DelegationRefused) as refused:
        await spend(
            async_session,
            tenant_id=seeded_fixture_data.tenant_a.id,
            delegation_id=root.id,
            amount_minor=99_000,
            sku="CLOUD-STARTER",
            reference=reference,
            as_of=NOW,
        )
    assert refused.value.reason == "DELEGATION_BUDGET_EXHAUSTED"

    orphan = await async_session.scalar(
        select(func.count())
        .select_from(DelegationSpend)
        .where(DelegationSpend.reference == reference)
    )
    assert orphan == 0, "the refused spend burned its reference"
