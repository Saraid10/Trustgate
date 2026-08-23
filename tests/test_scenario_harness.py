"""Prove the scenario harness actually fails when an attack succeeds.

Every Tier A scenario ends in a harness assertion. If those assertions could not raise, the whole
suite would pass vacuously while claiming to prove that attacks change nothing. These tests feed
the harness snapshots representing a successful attack and require it to object.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from scenarios.tier_a.harness import (
    ScenarioViolation,
    TenantSnapshot,
    assert_attack_created_nothing,
    assert_attack_gained_no_authority,
    assert_no_authority_consumed,
    assert_no_illegal_state_transition,
    assert_no_provider_order_created,
)

PAYMENT = uuid4()
REQUEST = uuid4()
ORDER = uuid4()
AUTHORITY = uuid4()


def _snapshot(
    *,
    payment_states: dict[UUID, str] | None = None,
    requests: frozenset[UUID] = frozenset(),
    orders: frozenset[UUID] = frozenset(),
    consumed: frozenset[UUID] = frozenset(),
) -> TenantSnapshot:
    return TenantSnapshot(
        payment_states=payment_states or {},
        payment_request_ids=requests,
        provider_order_ids=orders,
        consumed_authority_ids=consumed,
    )


def test_a_created_provider_order_is_caught() -> None:
    before = _snapshot()
    after = _snapshot(orders=frozenset({ORDER}))

    with pytest.raises(ScenarioViolation, match="provider order"):
        assert_no_provider_order_created(before, after)


def test_a_payment_advanced_into_an_authority_bearing_state_is_caught() -> None:
    before = _snapshot(payment_states={PAYMENT: "CREATED"})
    after = _snapshot(payment_states={PAYMENT: "CAPTURED"})

    with pytest.raises(ScenarioViolation, match="authority-bearing state"):
        assert_no_illegal_state_transition(before, after)


def test_a_changed_payment_state_is_caught_even_without_authority() -> None:
    before = _snapshot(payment_states={PAYMENT: "CREATED"})
    after = _snapshot(payment_states={PAYMENT: "DENIED"})

    with pytest.raises(ScenarioViolation, match="changed existing payment state"):
        assert_no_illegal_state_transition(before, after)


def test_a_consumed_checkout_authority_is_caught() -> None:
    before = _snapshot()
    after = _snapshot(consumed=frozenset({AUTHORITY}))

    with pytest.raises(ScenarioViolation, match="consumed checkout authority"):
        assert_no_authority_consumed(before, after)


def test_a_created_payment_request_is_caught_by_the_strict_assertion() -> None:
    before = _snapshot()
    after = _snapshot(requests=frozenset({REQUEST}))

    with pytest.raises(ScenarioViolation, match="payment request"):
        assert_attack_created_nothing(before, after)


def test_the_permissive_assertion_allows_a_recorded_but_denied_request() -> None:
    """A denied request is legitimately recorded; only gained authority is a failure."""

    before = _snapshot()
    after = _snapshot(
        requests=frozenset({REQUEST}),
        payment_states={PAYMENT: "DENIED"},
    )

    assert_attack_gained_no_authority(before, after)


def test_the_permissive_assertion_still_rejects_a_new_authorized_payment() -> None:
    before = _snapshot()
    after = _snapshot(payment_states={PAYMENT: "AUTHORIZED"})

    with pytest.raises(ScenarioViolation, match="authority-bearing state"):
        assert_attack_gained_no_authority(before, after)


def test_an_unchanged_snapshot_passes_every_assertion() -> None:
    snapshot = _snapshot(
        payment_states={PAYMENT: "CREATED"},
        requests=frozenset({REQUEST}),
    )

    assert_attack_created_nothing(snapshot, snapshot)
