"""Before-and-after state comparison for adversarial scenarios.

An endpoint returning a rejection is only the first of the three things a scenario must prove. The
other two are that no provider order was created and that no payment moved to a state it had no
authority to reach. Both are properties of what changed, not of what was returned, so every
scenario captures tenant state before the attack and compares it afterwards.

A sequential assertion on the response alone cannot distinguish "the request was refused" from
"the request was refused and something was written anyway".

These checks raise `ScenarioViolation` rather than using bare `assert`. This module is shipped
library code, and `python -O` strips assertions: an assert-based harness would report every
scenario as passing under optimisation while verifying nothing. The same reasoning applies here
as to the state machine's approval requirement.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.domain import CheckoutAuthority, Payment, PaymentRequest, RazorpayOrder

# States a payment can only reach through policy authorisation, human approval, a consumed
# checkout authority, or a verified provider event. An attack must never introduce one.
AUTHORITY_BEARING_STATES = frozenset(
    {"AUTHORIZED", "PROVIDER_PENDING", "CAPTURED", "REFUNDED", "PARTIALLY_REFUNDED"}
)


class ScenarioViolation(AssertionError):
    """An adversarial scenario changed state it had no authority to change.

    Subclasses `AssertionError` so pytest reports it as a normal failure, but it is raised
    explicitly and therefore survives `python -O`.
    """


@dataclass(frozen=True)
class TenantSnapshot:
    """Everything an attack could unsafely create or advance, for one tenant."""

    payment_states: dict[UUID, str]
    payment_request_ids: frozenset[UUID]
    provider_order_ids: frozenset[UUID]
    consumed_authority_ids: frozenset[UUID]

    @property
    def authority_bearing_payments(self) -> frozenset[UUID]:
        return frozenset(
            payment_id
            for payment_id, state in self.payment_states.items()
            if state in AUTHORITY_BEARING_STATES
        )


async def snapshot_tenant(session: AsyncSession, tenant_id: UUID) -> TenantSnapshot:
    """Capture the tenant-scoped state an adversarial scenario must leave undisturbed."""

    payments = (
        await session.execute(
            select(Payment.id, Payment.state).where(Payment.tenant_id == tenant_id)
        )
    ).all()
    request_ids = (
        await session.scalars(
            select(PaymentRequest.id).where(PaymentRequest.tenant_id == tenant_id)
        )
    ).all()
    order_ids = (
        await session.scalars(select(RazorpayOrder.id).where(RazorpayOrder.tenant_id == tenant_id))
    ).all()
    consumed = (
        await session.scalars(
            select(CheckoutAuthority.id).where(
                CheckoutAuthority.tenant_id == tenant_id,
                CheckoutAuthority.used_at.is_not(None),
            )
        )
    ).all()
    return TenantSnapshot(
        payment_states={payment_id: state for payment_id, state in payments},
        payment_request_ids=frozenset(request_ids),
        provider_order_ids=frozenset(order_ids),
        consumed_authority_ids=frozenset(consumed),
    )


def assert_no_provider_order_created(before: TenantSnapshot, after: TenantSnapshot) -> None:
    """The second required assertion: no unsafe provider order exists."""

    created = after.provider_order_ids - before.provider_order_ids
    if created:
        raise ScenarioViolation(f"attack created provider order(s): {sorted(map(str, created))}")


def assert_no_illegal_state_transition(before: TenantSnapshot, after: TenantSnapshot) -> None:
    """The third required assertion: nothing gained payment authority it did not have.

    Creating a payment is permitted — a denied request still records one. Moving a payment into a
    state that only policy, approval, authority, or a verified provider event can grant is not.
    """

    gained = after.authority_bearing_payments - before.authority_bearing_payments
    if gained:
        detail = ", ".join(
            f"{payment_id}={after.payment_states[payment_id]}" for payment_id in gained
        )
        raise ScenarioViolation(
            f"attack advanced payment(s) into an authority-bearing state: {detail}"
        )
    changed = {
        payment_id: (state, after.payment_states.get(payment_id))
        for payment_id, state in before.payment_states.items()
        if after.payment_states.get(payment_id) != state
    }
    if changed:
        raise ScenarioViolation(f"attack changed existing payment state(s): {changed}")


def assert_no_authority_consumed(before: TenantSnapshot, after: TenantSnapshot) -> None:
    consumed = after.consumed_authority_ids - before.consumed_authority_ids
    if consumed:
        raise ScenarioViolation(f"attack consumed checkout authority: {sorted(map(str, consumed))}")


def assert_attack_created_nothing(before: TenantSnapshot, after: TenantSnapshot) -> None:
    """The strictest form: the attack left no trace in the tenant's payment records at all."""

    assert_no_provider_order_created(before, after)
    assert_no_illegal_state_transition(before, after)
    assert_no_authority_consumed(before, after)
    created = after.payment_request_ids - before.payment_request_ids
    if created:
        raise ScenarioViolation(f"attack created payment request(s): {sorted(map(str, created))}")


def assert_attack_gained_no_authority(before: TenantSnapshot, after: TenantSnapshot) -> None:
    """The permissive form, for attacks that legitimately record a denied request.

    A rejected-but-recorded request is the audited outcome for some scenarios. What must never
    happen is that the recorded request carries authority, spends money, or reaches a provider.
    """

    assert_no_provider_order_created(before, after)
    assert_no_illegal_state_transition(before, after)
    assert_no_authority_consumed(before, after)
