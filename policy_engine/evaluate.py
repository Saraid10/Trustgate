from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.domain import (
    DailySpendReservation,
    Merchant,
    PolicyMerchant,
    SpendingPolicy,
)


@dataclass(frozen=True)
class PolicyDecision:
    decision: str
    reasons: list[str]
    policy_version: int


@dataclass(frozen=True)
class PolicyRules:
    version: int
    max_amount_minor: int
    currency: str
    max_daily_spend_minor: int
    expiry: datetime
    approval_required_above_minor: int | None


def evaluate_policy_rules(
    rules: PolicyRules | None,
    *,
    merchant_is_allowed: bool,
    daily_spend_minor: int,
    amount_minor: int,
    currency: str,
    as_of: datetime,
) -> PolicyDecision:
    """Apply policy rules without database access so the decision is exhaustively testable."""

    if rules is None:
        # Distinct from expiry on purpose. Both fail closed, and only the diagnosis differs - but
        # telling an operator a policy expired when there is no policy sends them hunting for a
        # date nobody ever set.
        return PolicyDecision("DENY", ["POLICY_NOT_FOUND"], 0)
    if rules.expiry <= as_of:
        return PolicyDecision("DENY", ["POLICY_EXPIRED"], rules.version)

    reasons: list[str] = []
    if currency != rules.currency:
        reasons.append("CURRENCY_NOT_ALLOWED")
    if not merchant_is_allowed:
        reasons.append("MERCHANT_NOT_ALLOWED")
    if amount_minor > rules.max_amount_minor:
        reasons.append("AMOUNT_EXCEEDS_LIMIT")
    if daily_spend_minor + amount_minor > rules.max_daily_spend_minor:
        reasons.append("DAILY_LIMIT_EXCEEDED")
    if reasons:
        return PolicyDecision("DENY", reasons, rules.version)
    if (
        rules.approval_required_above_minor is not None
        and amount_minor > rules.approval_required_above_minor
    ):
        return PolicyDecision("REQUIRE_APPROVAL", ["APPROVAL_REQUIRED"], rules.version)
    return PolicyDecision("ALLOW", [], rules.version)


async def evaluate_payment_request(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    actor_id: str,
    merchant_id: UUID,
    amount_minor: int,
    currency: str,
    as_of: datetime | None = None,
) -> PolicyDecision:
    """Evaluate the newest immutable tenant policy against a requested payment."""

    now = as_of or datetime.now(UTC)
    policy = await session.scalar(
        select(SpendingPolicy)
        .where(SpendingPolicy.tenant_id == tenant_id)
        .order_by(SpendingPolicy.version.desc())
        .limit(1)
    )
    if policy is None:
        return evaluate_policy_rules(
            None,
            merchant_is_allowed=False,
            daily_spend_minor=0,
            amount_minor=amount_minor,
            currency=currency,
            as_of=now,
        )
    merchant_is_allowed = await session.scalar(
        select(PolicyMerchant.policy_id)
        .join(
            Merchant,
            (Merchant.id == PolicyMerchant.merchant_id)
            & (Merchant.tenant_id == PolicyMerchant.tenant_id),
        )
        .where(
            PolicyMerchant.tenant_id == tenant_id,
            PolicyMerchant.policy_id == policy.id,
            PolicyMerchant.merchant_id == merchant_id,
            Merchant.tenant_id == tenant_id,
            Merchant.is_active.is_(True),
        )
    )
    daily_spend = await session.scalar(
        select(DailySpendReservation.reserved_amount_minor).where(
            DailySpendReservation.tenant_id == tenant_id,
            DailySpendReservation.actor_id == actor_id,
            DailySpendReservation.spend_date == now.date(),
        )
    )
    rules = PolicyRules(
        version=policy.version,
        max_amount_minor=policy.max_amount_minor,
        currency=policy.currency,
        max_daily_spend_minor=policy.max_daily_spend_minor,
        expiry=policy.expiry,
        approval_required_above_minor=policy.approval_required_above_minor,
    )
    return evaluate_policy_rules(
        rules,
        merchant_is_allowed=merchant_is_allowed is not None,
        daily_spend_minor=int(daily_spend or 0),
        amount_minor=amount_minor,
        currency=currency,
        as_of=now,
    )


async def reserve_daily_spend(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    actor_id: str,
    amount_minor: int,
    policy_version: int,
    as_of: datetime | None = None,
) -> bool:
    """Atomically reserve budget after a non-denied policy evaluation.

    The conditional upsert is the concurrency boundary: only one competing request can reserve
    the final remaining amount for the same tenant, actor, and UTC day.
    """

    policy = await session.scalar(
        select(SpendingPolicy).where(
            SpendingPolicy.tenant_id == tenant_id,
            SpendingPolicy.version == policy_version,
        )
    )
    if policy is None:
        return False
    spend_date: date = (as_of or datetime.now(UTC)).date()
    statement = (
        insert(DailySpendReservation)
        .values(
            tenant_id=tenant_id,
            actor_id=actor_id,
            spend_date=spend_date,
            reserved_amount_minor=amount_minor,
        )
        .on_conflict_do_update(
            constraint="uq_daily_spend_reservation_actor_day",
            set_={
                "reserved_amount_minor": DailySpendReservation.reserved_amount_minor + amount_minor,
                "updated_at": datetime.now(UTC),
            },
            where=(
                DailySpendReservation.reserved_amount_minor + amount_minor
                <= policy.max_daily_spend_minor
            ),
        )
        .returning(DailySpendReservation.id)
    )
    return (await session.scalar(statement)) is not None


async def release_daily_spend(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    actor_id: str,
    amount_minor: int,
    spend_date: date,
) -> bool:
    """Return reserved budget when a request reaches a state that will never spend it.

    Reserving on `REQUIRE_APPROVAL` stops an approved high-value request from bypassing the daily
    limit, but a reservation that is never released turns an abandoned approval into a lockout: an
    agent acting entirely within its permitted contract can exhaust an actor's day by requesting
    approvals nobody grants. Releasing on terminal states makes the reservation reflect money that
    can still be spent rather than money that was once contemplated.

    The subtraction is floored at zero and applied in one statement, so a double release cannot
    drive the reservation negative and hand an actor extra budget.
    """

    if amount_minor <= 0:
        return False
    statement = (
        update(DailySpendReservation)
        .where(
            DailySpendReservation.tenant_id == tenant_id,
            DailySpendReservation.actor_id == actor_id,
            DailySpendReservation.spend_date == spend_date,
        )
        .values(
            reserved_amount_minor=func.greatest(
                DailySpendReservation.reserved_amount_minor - amount_minor, 0
            ),
            updated_at=datetime.now(UTC),
        )
        .returning(DailySpendReservation.id)
    )
    return (await session.scalar(statement)) is not None
