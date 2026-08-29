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
from models.domain import AuditEvent, Delegation, DelegationSpend, SpendingPolicy

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
async def test_an_over_wide_child_is_named_before_the_database_has_to_say_it(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """A reason a caller can act on, for a refusal the trigger would make anyway.

    The parent is already loaded by this point, so comparing against it costs a read and nothing
    else. It is not where the rule lives - `test_the_trigger_refuses_an_over_wide_child_written_
    around_the_claim` covers that - it is just the difference between a reason and a stack trace.
    """

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

    assert refused.value.reason == "DELEGATION_BUDGET_EXCEEDS_PARENT"


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


# --- evidence ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_delegation_operation_leaves_evidence(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The rest of this project records what it did and why. So does this now."""

    tenant_id = seeded_fixture_data.tenant_a.id
    root = await _root(async_session, seeded_fixture_data, budget=50_000)
    child = await grant(
        async_session,
        tenant_id=tenant_id,
        parent_id=root.id,
        delegator_actor_id="agent-a",
        delegate_actor_id="agent-b",
        bounds=_bounds(budget=20_000),
    )
    reference = uuid4()
    await spend(
        async_session,
        tenant_id=tenant_id,
        delegation_id=child.id,
        amount_minor=5_000,
        sku="CLOUD-STARTER",
        reference=reference,
        as_of=NOW,
    )
    await release(async_session, tenant_id=tenant_id, reference=reference)
    await revoke(async_session, tenant_id=tenant_id, delegation_id=child.id)

    kinds = (
        (
            await async_session.execute(
                select(AuditEvent.event_kind).where(
                    AuditEvent.tenant_id == tenant_id, AuditEvent.delegation_id.is_not(None)
                )
            )
        )
        .scalars()
        .all()
    )

    assert sorted(kinds) == [
        "delegation_granted",
        "delegation_granted",
        "delegation_released",
        "delegation_revoked",
        "delegation_spent",
    ]


@pytest.mark.asyncio
async def test_a_spend_records_the_chain_that_authorized_it(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The attribution question, answered from the evidence rather than reconstructed.

    Given a spend, the audit row names every hop that agreed to it and the human at the root. That
    is what the delegation literature calls the accountability chain, and it is the thing a
    one-hop mandate cannot answer past the first hop.
    """

    tenant_id = seeded_fixture_data.tenant_a.id
    root = await _root(async_session, seeded_fixture_data, budget=50_000)
    child = await grant(
        async_session,
        tenant_id=tenant_id,
        parent_id=root.id,
        delegator_actor_id="agent-a",
        delegate_actor_id="agent-b",
        bounds=_bounds(budget=20_000),
    )
    await spend(
        async_session,
        tenant_id=tenant_id,
        delegation_id=child.id,
        amount_minor=5_000,
        sku="CLOUD-STARTER",
        reference=uuid4(),
        as_of=NOW,
    )

    event = await async_session.scalar(
        select(AuditEvent).where(
            AuditEvent.tenant_id == tenant_id, AuditEvent.event_kind == "delegation_spent"
        )
    )

    assert event is not None
    assert event.delegation_id == child.id
    assert event.payload["chain"] == [str(root.id), str(child.id)]
    assert event.payload["root_actor_id"] == "human-principal"


@pytest.mark.asyncio
async def test_a_refused_spend_leaves_no_evidence_of_spending(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Evidence of a spend that did not happen is worse than none."""

    tenant_id = seeded_fixture_data.tenant_a.id
    root = await _root(async_session, seeded_fixture_data, budget=10_000)

    with pytest.raises(DelegationRefused):
        await spend(
            async_session,
            tenant_id=tenant_id,
            delegation_id=root.id,
            amount_minor=99_000,
            sku="CLOUD-STARTER",
            reference=uuid4(),
            as_of=NOW,
        )

    spent_events = await async_session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.tenant_id == tenant_id, AuditEvent.event_kind == "delegation_spent")
    )
    assert spent_events == 0


@pytest.mark.asyncio
async def test_a_correlation_id_is_carried_into_the_evidence(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """What joins a delegation's evidence to the payment timeline it belongs to."""

    tenant_id = seeded_fixture_data.tenant_a.id
    correlation_id = uuid4()
    root = await _root(async_session, seeded_fixture_data, budget=50_000)
    await spend(
        async_session,
        tenant_id=tenant_id,
        delegation_id=root.id,
        amount_minor=5_000,
        sku="CLOUD-STARTER",
        reference=uuid4(),
        as_of=NOW,
        correlation_id=correlation_id,
    )

    event = await async_session.scalar(
        select(AuditEvent).where(
            AuditEvent.tenant_id == tenant_id, AuditEvent.event_kind == "delegation_spent"
        )
    )
    assert event is not None
    assert event.correlation_id == correlation_id


# --- bounds are fixed at grant ------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("budget_minor", 999_999),
        ("max_amount_minor", 999_999),
        ("allowed_skus", ["CLOUD-STARTER", "CLOUD-TEAM", "ANYTHING"]),
        ("expires_at", datetime(2036, 1, 1, tzinfo=UTC)),
        ("depth", 5),
        ("delegate_actor_id", "someone-else"),
        ("root_actor_id", "someone-else"),
        ("policy_version", 99),
        ("purpose", "something else entirely"),
    ],
    ids=[
        "budget",
        "payment-cap",
        "scope",
        "expiry",
        "depth",
        "delegate",
        "root-principal",
        "policy-version",
        "purpose",
    ],
)
async def test_a_granted_hop_cannot_be_widened_afterwards(
    async_session: AsyncSession, seeded_fixture_data: FixtureData, column: str, value: object
) -> None:
    """The attenuation trigger fires on INSERT, which left UPDATE wide open.

    A hop granted a budget of 1,000 was rewritten to 999,999 with its scope widened and its expiry
    pushed a decade out, and nothing objected - so a child could be widened past its parent and
    take the chain with it. Bounds are fixed at grant now.
    """

    root = await _root(async_session, seeded_fixture_data, budget=50_000)

    with pytest.raises(DBAPIError) as raised:
        async with async_session.begin_nested():
            await async_session.execute(
                update(Delegation).where(Delegation.id == root.id).values(**{column: value})
            )

    assert "bounds are fixed at grant" in str(raised.value)


@pytest.mark.asyncio
async def test_a_revoked_hop_cannot_be_revived(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Authority coming back from the dead is worse than authority that never ended."""

    root = await _root(async_session, seeded_fixture_data, budget=50_000)
    await revoke(async_session, tenant_id=seeded_fixture_data.tenant_a.id, delegation_id=root.id)

    with pytest.raises(DBAPIError) as raised:
        async with async_session.begin_nested():
            await async_session.execute(
                update(Delegation).where(Delegation.id == root.id).values(revoked_at=None)
            )

    assert "cannot be revived" in str(raised.value)


@pytest.mark.asyncio
async def test_the_things_a_hop_does_after_being_granted_still_change(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Freezing the bounds must not freeze the accounting, or nothing works at all."""

    tenant_id = seeded_fixture_data.tenant_a.id
    root = await _root(async_session, seeded_fixture_data, budget=50_000)
    await grant(
        async_session,
        tenant_id=tenant_id,
        parent_id=root.id,
        delegator_actor_id="agent-a",
        delegate_actor_id="agent-b",
        bounds=_bounds(budget=20_000),
    )
    await spend(
        async_session,
        tenant_id=tenant_id,
        delegation_id=root.id,
        amount_minor=1_000,
        sku="CLOUD-STARTER",
        reference=uuid4(),
        as_of=NOW,
    )
    await revoke(async_session, tenant_id=tenant_id, delegation_id=root.id)

    row = await async_session.scalar(select(Delegation).where(Delegation.id == root.id))
    assert row is not None
    assert (row.allocated_minor, row.spent_minor) == (20_000, 1_000)
    assert row.revoked_at is not None


@pytest.mark.asyncio
async def test_reusing_a_reference_for_a_different_spend_is_refused(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """An idempotency key belongs to one request, not to whatever arrives with it next.

    Treating a mismatched reuse as a successful retry reports success for a spend that never
    happened, and the caller has no way to tell.
    """

    tenant_id = seeded_fixture_data.tenant_a.id
    root = await _root(async_session, seeded_fixture_data, budget=50_000)
    reference = uuid4()
    await spend(
        async_session,
        tenant_id=tenant_id,
        delegation_id=root.id,
        amount_minor=5_000,
        sku="CLOUD-STARTER",
        reference=reference,
        as_of=NOW,
    )

    with pytest.raises(DelegationRefused) as refused:
        await spend(
            async_session,
            tenant_id=tenant_id,
            delegation_id=root.id,
            amount_minor=9_000,
            sku="CLOUD-STARTER",
            reference=reference,
            as_of=NOW,
        )

    assert refused.value.reason == "DELEGATION_REFERENCE_REUSED"
    spent = await async_session.scalar(
        select(Delegation.spent_minor).where(Delegation.id == root.id)
    )
    assert spent == 5_000


# --- what a writer who skips this module can still not do ---------------------------------------


def _raw_child(parent: Delegation, *, name: str, budget: int) -> Delegation:
    """A child written the way `grant` never would: straight in, no bookkeeping."""

    return Delegation(
        id=uuid4(),
        tenant_id=parent.tenant_id,
        parent_id=parent.id,
        depth=parent.depth + 1,
        policy_id=parent.policy_id,
        policy_version=parent.policy_version,
        root_actor_id=parent.root_actor_id,
        delegator_actor_id=parent.delegate_actor_id,
        delegate_actor_id=name,
        budget_minor=budget,
        allocated_minor=0,
        spent_minor=0,
        max_amount_minor=parent.max_amount_minor,
        allowed_skus=list(parent.allowed_skus),
        purpose="written around the module",
        expires_at=parent.expires_at,
    )


@pytest.mark.asyncio
async def test_siblings_written_straight_to_the_database_cannot_outgrow_their_parent(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The aggregate has to hold for any writer, not for callers who do the bookkeeping.

    It did not. `allocated_minor` was only ever moved by `grant`, so an insert that skipped this
    module left it at zero and `ck_delegation_budget_partitioned` had nothing to object to: three
    children of 1,000 were written under a parent holding 1,000, and every per-edge check passed
    them on the way in. The trigger takes the allocation itself now.
    """

    root = await _root(async_session, seeded_fixture_data, budget=50_000)

    async with async_session.begin_nested():
        async_session.add(_raw_child(root, name="first", budget=50_000))
        await async_session.flush()

    with pytest.raises(DBAPIError) as raised:
        async with async_session.begin_nested():
            async_session.add(_raw_child(root, name="second", budget=50_000))
            await async_session.flush()

    assert "DELEGATION_BUDGET_EXHAUSTED" in str(raised.value)

    allocated = await async_session.scalar(
        select(Delegation.allocated_minor).where(Delegation.id == root.id)
    )
    assert allocated == 50_000, "the database did not take the allocation itself"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("budget_minor", 99_999_999, "budget may not exceed the policy daily limit"),
        ("max_amount_minor", 99_999_999, "per-payment cap may not exceed the policy"),
        ("expires_at", datetime(2036, 1, 1, tzinfo=UTC), "may not outlive the policy"),
    ],
    ids=["budget", "payment-cap", "expiry"],
)
async def test_a_root_written_straight_to_the_database_cannot_exceed_its_policy(
    async_session: AsyncSession,
    seeded_fixture_data: FixtureData,
    field: str,
    value: object,
    expected: str,
) -> None:
    """The trigger used to return immediately for a root, so its bounds lived only in Python.

    A root of 99,999,999 was accepted against a policy capping 200,000.
    """

    policy = seeded_fixture_data.tenant_a_policy
    fields = {
        "budget_minor": 10_000,
        "max_amount_minor": 10_000,
        "expires_at": NOW + timedelta(days=1),
    }
    fields[field] = value

    with pytest.raises(DBAPIError) as raised:
        async with async_session.begin_nested():
            async_session.add(
                Delegation(
                    id=uuid4(),
                    tenant_id=seeded_fixture_data.tenant_a.id,
                    parent_id=None,
                    depth=0,
                    policy_id=policy.id,
                    policy_version=policy.version,
                    root_actor_id="human-principal",
                    delegator_actor_id="human-principal",
                    delegate_actor_id="written-around",
                    allocated_minor=0,
                    spent_minor=0,
                    allowed_skus=["CLOUD-STARTER"],
                    purpose="written around the module",
                    **fields,
                )
            )
            await async_session.flush()

    assert expected in str(raised.value)
