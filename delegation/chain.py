"""Grant, resolve, revoke, and spend against multi-hop delegated authority.

The delegation literature models a capability as a set and attenuation as intersection, which is
right for permissions and wrong for money. Two children each permitted no more than their parent
satisfy every per-edge comparison and together spend twice what the parent held. Budgets add where
sets intersect, so a parent's budget is partitioned here: `allocated_minor` records what has been
promised downward and is claimed by conditional update, exactly as a daily spend reservation is.

Nothing in this module is trusted to be the only guard. `ck_delegation_budget_partitioned` refuses
an over-allocated parent row, and the `delegation_attenuates` trigger refuses a child wider than
its parent, so both survive every line here being wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from models.domain import AuditEvent, Delegation, DelegationSpend, SpendingPolicy
from models.locking import locked

MAX_DEPTH = 8
"""Mirrors `ck_delegation_depth_bounded`. A chain nobody can enumerate is a chain nobody audits."""


class DelegationRefused(Exception):
    """A delegation was refused, carrying the machine-readable reason it was refused for."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Bounds:
    """What a hop may do, and one thing it merely says.

    `budget_minor`, `max_amount_minor`, `expires_at`, and `allowed_skus` are enforced: the
    `delegation_attenuates` trigger refuses a child that widens any of them, and
    `delegation_bounds_are_frozen` refuses an update that widens them afterwards.

    `purpose` is not enforced and is not a bound. It is free text with no narrowing relation - one
    string is not "narrower" than another in any way a database can check - so a child may say
    whatever it likes about why it exists without changing a single thing it can spend on. What
    actually scopes a hop is `allowed_skus`. Purpose is recorded, immutable after grant, and
    carried into the evidence; treat it as testimony rather than as a control.
    """

    budget_minor: int
    max_amount_minor: int
    allowed_skus: tuple[str, ...]
    purpose: str
    expires_at: datetime


def _evidence(
    *,
    tenant_id: UUID,
    delegation_id: UUID,
    correlation_id: UUID | None,
    kind: str,
    payload: dict[str, object],
) -> AuditEvent:
    """One audit row for a delegation operation.

    `correlation_id` is optional here and should not be. Integration passes the correlation of the
    payment being authorized, which is what joins a delegation's evidence to the payment timeline
    it belongs to; a caller that omits it gets a fresh one and an event that is recorded but not
    joined. Made optional rather than required so the existing callers did not all have to be
    rewritten at once, and named in `docs/limitations.md` so the shortcut is not invisible.
    """

    return AuditEvent(
        tenant_id=tenant_id,
        delegation_id=delegation_id,
        correlation_id=correlation_id or uuid4(),
        event_kind=kind,
        payload=payload,
    )


async def grant_root(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    policy: SpendingPolicy,
    principal_actor_id: str,
    delegate_actor_id: str,
    bounds: Bounds,
    correlation_id: UUID | None = None,
) -> Delegation:
    """Cut the first hop directly from a policy, on behalf of the human principal.

    A root cannot exceed the policy it is cut from; below it the trigger takes over.
    """

    if bounds.budget_minor <= 0:
        raise DelegationRefused("DELEGATION_BUDGET_NOT_POSITIVE")
    if bounds.budget_minor > policy.max_daily_spend_minor:
        raise DelegationRefused("DELEGATION_EXCEEDS_POLICY_DAILY_LIMIT")
    if bounds.max_amount_minor > policy.max_amount_minor:
        raise DelegationRefused("DELEGATION_EXCEEDS_POLICY_PAYMENT_LIMIT")
    if bounds.expires_at > policy.expiry:
        raise DelegationRefused("DELEGATION_OUTLIVES_POLICY")

    root = Delegation(
        id=uuid4(),
        tenant_id=tenant_id,
        parent_id=None,
        depth=0,
        policy_id=policy.id,
        policy_version=policy.version,
        root_actor_id=principal_actor_id,
        delegator_actor_id=principal_actor_id,
        delegate_actor_id=delegate_actor_id,
        budget_minor=bounds.budget_minor,
        allocated_minor=0,
        spent_minor=0,
        max_amount_minor=bounds.max_amount_minor,
        allowed_skus=list(bounds.allowed_skus),
        purpose=bounds.purpose,
        expires_at=bounds.expires_at,
    )
    session.add(root)
    await session.flush()
    session.add(
        _evidence(
            tenant_id=tenant_id,
            delegation_id=root.id,
            correlation_id=correlation_id,
            kind="delegation_granted",
            payload={
                "depth": 0,
                "root_actor_id": principal_actor_id,
                "delegate_actor_id": delegate_actor_id,
                "budget_minor": bounds.budget_minor,
                "max_amount_minor": bounds.max_amount_minor,
                "allowed_skus": list(bounds.allowed_skus),
                "purpose": bounds.purpose,
                "policy_version": policy.version,
            },
        )
    )
    await session.flush()
    return root


async def grant(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    parent_id: UUID,
    delegator_actor_id: str,
    delegate_actor_id: str,
    bounds: Bounds,
    correlation_id: UUID | None = None,
) -> Delegation:
    """Delegate part of an existing hop onward.

    The parent's allocation is claimed before the child exists. Two siblings racing for the same
    remaining budget both issue this update, and only one of them can find the room.
    """

    if bounds.budget_minor <= 0:
        # Every bound below is an upper bound, so a negative budget satisfies all of them and the
        # allocation claim then credits the parent on the way past.
        raise DelegationRefused("DELEGATION_BUDGET_NOT_POSITIVE")

    parent = await session.scalar(
        select(Delegation).where(Delegation.tenant_id == tenant_id, Delegation.id == parent_id)
    )
    if parent is None:
        raise DelegationRefused("DELEGATION_PARENT_NOT_FOUND")
    if parent.depth + 1 > MAX_DEPTH:
        raise DelegationRefused("DELEGATION_DEPTH_EXCEEDED")
    if bounds.budget_minor > parent.budget_minor:
        # A read, not bookkeeping: the parent is already loaded, and the trigger would refuse this
        # anyway as a raw database error. This turns it into a reason a caller can act on.
        raise DelegationRefused("DELEGATION_BUDGET_EXCEEDS_PARENT")

    child = Delegation(
        id=uuid4(),
        tenant_id=tenant_id,
        parent_id=parent_id,
        depth=parent.depth + 1,
        policy_id=parent.policy_id,
        policy_version=parent.policy_version,
        root_actor_id=parent.root_actor_id,
        delegator_actor_id=delegator_actor_id,
        delegate_actor_id=delegate_actor_id,
        budget_minor=bounds.budget_minor,
        allocated_minor=0,
        spent_minor=0,
        max_amount_minor=bounds.max_amount_minor,
        allowed_skus=list(bounds.allowed_skus),
        purpose=bounds.purpose,
        expires_at=bounds.expires_at,
    )
    # The parent's allocation is taken by `delegation_attenuates`, inside this insert, so that it
    # holds for any writer rather than for callers who remember to do the bookkeeping. Doing it
    # here as well would count it twice.
    savepoint = await session.begin_nested()
    try:
        session.add(child)
        await session.flush()
    except DBAPIError as exhausted:
        await savepoint.rollback()
        if "DELEGATION_BUDGET_EXHAUSTED" in str(exhausted):
            raise DelegationRefused("DELEGATION_BUDGET_EXHAUSTED") from exhausted
        raise
    await savepoint.commit()

    session.add(
        _evidence(
            tenant_id=tenant_id,
            delegation_id=child.id,
            correlation_id=correlation_id,
            kind="delegation_granted",
            payload={
                "depth": child.depth,
                "parent_id": str(parent_id),
                "root_actor_id": parent.root_actor_id,
                "delegator_actor_id": delegator_actor_id,
                "delegate_actor_id": delegate_actor_id,
                "budget_minor": bounds.budget_minor,
                "max_amount_minor": bounds.max_amount_minor,
                "allowed_skus": list(bounds.allowed_skus),
                "purpose": bounds.purpose,
            },
        )
    )
    await session.flush()
    return child


async def active_delegation_for(
    session: AsyncSession, *, tenant_id: UUID, actor_id: str, as_of: datetime | None = None
) -> Delegation | None:
    """The live delegation an actor holds, or None if it holds none.

    None is not a refusal. An actor with no delegation is an actor the chain has nothing to say
    about, and authorization treats it exactly as it did before this existed - which is what makes
    the whole suite a regression net for the wiring.

    `uq_delegation_one_live_per_actor` is why this returns one thing rather than picking from
    several. Expiry is filtered here rather than in the index because an index predicate cannot
    reference the current time.
    """

    now = as_of or datetime.now(UTC)
    held: Delegation | None = await session.scalar(
        select(Delegation).where(
            Delegation.tenant_id == tenant_id,
            Delegation.delegate_actor_id == actor_id,
            Delegation.revoked_at.is_(None),
            Delegation.expires_at > now,
        )
    )
    return held


async def resolve_chain(
    session: AsyncSession, *, tenant_id: UUID, delegation_id: UUID
) -> list[Delegation]:
    """Walk from a hop to its root, returning the chain root-first.

    A hop is only as good as everything above it, so the whole path is fetched in one statement
    rather than trusting the leaf to describe its own ancestry.
    """

    leaf = (
        select(Delegation)
        .where(Delegation.tenant_id == tenant_id, Delegation.id == delegation_id)
        .cte(name="chain", recursive=True)
    )
    parent = select(Delegation).join(
        leaf,
        (Delegation.tenant_id == leaf.c.tenant_id) & (Delegation.id == leaf.c.parent_id),
    )
    walk = leaf.union_all(parent)

    # populate_existing, for the reason `models.locking.locked` uses it. `delegation_attenuates`
    # updates a parent's allocation from inside the insert that creates its child, so a parent
    # already in the identity map is stale the moment a hop is granted below it - and a chain read
    # afterwards would report the allocation it had before, which is the number a caller is asking
    # about. Same fault as the row lock that returned a cached row; different place.
    rows = (
        (
            await session.execute(
                select(Delegation)
                .from_statement(select(walk))
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    chain = sorted(rows, key=lambda hop: hop.depth)
    if not chain:
        raise DelegationRefused("DELEGATION_NOT_FOUND")
    if chain[0].depth != 0 or chain[0].parent_id is not None:
        raise DelegationRefused("DELEGATION_CHAIN_BROKEN")
    if len(chain) != chain[-1].depth + 1:
        raise DelegationRefused("DELEGATION_CHAIN_BROKEN")
    return chain


async def revoke(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    delegation_id: UUID,
    at: datetime | None = None,
    correlation_id: UUID | None = None,
) -> None:
    """Revoke one hop. Everything below it dies with it, without being touched.

    Descendants are left alone deliberately. A signed capability has to be hunted down and recalled
    because it carries its own authority; a hop here is re-derived from its whole chain every time
    it is spent, so revoking an ancestor is already the end of the branch.
    """

    revoked = await session.execute(
        update(Delegation)
        .where(
            Delegation.tenant_id == tenant_id,
            Delegation.id == delegation_id,
            Delegation.revoked_at.is_(None),
        )
        # func.now() is Postgres's clock, and `created_at` came from the same one. A host
        # datetime here is a bet on two machines agreeing about the time, which this project has
        # already measured them not doing - and `ck_delegation_revocation_after_creation` is what
        # would fail, intermittently, somewhere else.
        .values(revoked_at=at or func.now())
    )
    if int(getattr(revoked, "rowcount", 0)) != 1:
        raise DelegationRefused("DELEGATION_ALREADY_REVOKED")

    session.add(
        _evidence(
            tenant_id=tenant_id,
            delegation_id=delegation_id,
            correlation_id=correlation_id,
            kind="delegation_revoked",
            payload={"delegation_id": str(delegation_id)},
        )
    )
    await session.flush()


async def spend(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    delegation_id: UUID,
    amount_minor: int,
    sku: str,
    reference: UUID,
    as_of: datetime | None = None,
    correlation_id: UUID | None = None,
) -> None:
    """Spend against a hop, after every hop above it has agreed.

    `reference` is the caller's idempotency key: spending twice under one reference charges once.

    Raises rather than returning a flag: a refusal here has a reason, and a caller that ignores a
    boolean spends money it was told it could not.
    """

    if amount_minor <= 0:
        # A negative spend is a refund nobody authorized: it passes every upper bound and the
        # atomic claim subtracts from `spent_minor`, handing budget back.
        raise DelegationRefused("DELEGATION_AMOUNT_NOT_POSITIVE")

    now = as_of or datetime.now(UTC)
    chain = await resolve_chain(session, tenant_id=tenant_id, delegation_id=delegation_id)

    # Validating an ancestor by reading it, then claiming from the leaf, leaves a gap a concurrent
    # revoke fits through: the claim below re-checks `revoked_at` on the leaf and on nothing above
    # it. Locking the whole chain closes the gap. Root first, because two spends sharing ancestors
    # that lock in opposite orders deadlock.
    chain = sorted(
        (
            await session.scalars(
                locked(
                    select(Delegation)
                    .where(
                        Delegation.tenant_id == tenant_id,
                        Delegation.id.in_([hop.id for hop in chain]),
                    )
                    .order_by(Delegation.depth)
                )
            )
        ).all(),
        key=lambda hop: hop.depth,
    )

    current = await session.scalar(
        select(SpendingPolicy).where(
            SpendingPolicy.tenant_id == tenant_id,
            SpendingPolicy.version == chain[0].policy_version,
        )
    )
    if current is None or current.expiry <= now:
        raise DelegationRefused("DELEGATION_POLICY_DRIFT")

    for hop in chain:
        if hop.revoked_at is not None:
            raise DelegationRefused("DELEGATION_REVOKED")
        if hop.expires_at <= now:
            raise DelegationRefused("DELEGATION_EXPIRED")
        if amount_minor > hop.max_amount_minor:
            raise DelegationRefused("DELEGATION_AMOUNT_EXCEEDS_HOP_LIMIT")
        if sku not in hop.allowed_skus:
            raise DelegationRefused("DELEGATION_SKU_OUT_OF_SCOPE")

    # The ledger row goes in first, and conflicts if this reference has been spent before. Doing
    # the claim first and recording afterwards would let two concurrent retries both claim before
    # either discovered the other. Both statements sit in a savepoint so a refused spend cannot
    # leave the row behind for a caller that carries on in the same transaction.
    savepoint = await session.begin_nested()
    try:
        recorded = await session.scalar(
            insert(DelegationSpend)
            .values(
                id=uuid4(),
                tenant_id=tenant_id,
                delegation_id=delegation_id,
                reference=reference,
                amount_minor=amount_minor,
                sku=sku,
            )
            .on_conflict_do_nothing(constraint="uq_delegation_spend_reference")
            .returning(DelegationSpend.id)
        )
        if recorded is None:
            # This reference has already been spent. A genuine retry must not charge again and
            # must not look different to the caller from the attempt that worked - but a reference
            # belongs to one request, not to whatever arrives carrying it next. Treating a
            # mismatched reuse as a retry reports success for a spend that never happened.
            existing = await session.scalar(
                select(DelegationSpend).where(
                    DelegationSpend.tenant_id == tenant_id,
                    DelegationSpend.reference == reference,
                )
            )
            if (
                existing is None
                or existing.delegation_id != delegation_id
                or existing.amount_minor != amount_minor
                or existing.sku != sku
            ):
                raise DelegationRefused("DELEGATION_REFERENCE_REUSED")
            await savepoint.commit()
            return

        claimed = await session.execute(
            update(Delegation)
            .where(
                Delegation.tenant_id == tenant_id,
                Delegation.id == delegation_id,
                Delegation.revoked_at.is_(None),
                Delegation.allocated_minor + Delegation.spent_minor + amount_minor
                <= Delegation.budget_minor,
            )
            .values(spent_minor=Delegation.spent_minor + amount_minor)
        )
        if int(getattr(claimed, "rowcount", 0)) != 1:
            raise DelegationRefused("DELEGATION_BUDGET_EXHAUSTED")

        session.add(
            _evidence(
                tenant_id=tenant_id,
                delegation_id=delegation_id,
                correlation_id=correlation_id,
                kind="delegation_spent",
                payload={
                    "reference": str(reference),
                    "amount_minor": amount_minor,
                    "sku": sku,
                    "chain": [str(hop.id) for hop in chain],
                    "root_actor_id": chain[0].root_actor_id,
                },
            )
        )
        await session.flush()
    except BaseException:
        await savepoint.rollback()
        raise
    await savepoint.commit()


async def release(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    reference: UUID,
    at: datetime | None = None,
    correlation_id: UUID | None = None,
) -> None:
    """Give back a spend whose payment did not happen.

    Budget reserved for a payment that is later denied, cancelled, or failed has to return, or a
    chain silently shrinks every time something goes wrong. `released_at IS NULL` in the predicate
    is what stops the same spend being given back twice.
    """

    released = (
        await session.execute(
            update(DelegationSpend)
            .where(
                DelegationSpend.tenant_id == tenant_id,
                DelegationSpend.reference == reference,
                DelegationSpend.released_at.is_(None),
            )
            # Postgres's clock, for the reason `revoke` uses it:
            # ck_delegation_spend_release_after_creation compares this against a server default.
            .values(released_at=at or func.now())
            .returning(DelegationSpend.delegation_id, DelegationSpend.amount_minor)
        )
    ).first()
    if released is None:
        raise DelegationRefused("DELEGATION_SPEND_NOT_RELEASABLE")

    delegation_id, amount_minor = released
    given_back = await session.execute(
        update(Delegation)
        .where(Delegation.tenant_id == tenant_id, Delegation.id == delegation_id)
        .values(spent_minor=Delegation.spent_minor - amount_minor)
    )
    if int(getattr(given_back, "rowcount", 0)) != 1:
        raise DelegationRefused("DELEGATION_NOT_FOUND")

    session.add(
        _evidence(
            tenant_id=tenant_id,
            delegation_id=delegation_id,
            correlation_id=correlation_id,
            kind="delegation_released",
            payload={"reference": str(reference), "amount_minor": amount_minor},
        )
    )
    await session.flush()
