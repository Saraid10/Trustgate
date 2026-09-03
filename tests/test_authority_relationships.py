"""Three authority relationships, asserted against the database rather than against the code.

Each of these was true before this file existed, and true only because the application happened to
be careful. That is the distinction this project keeps making everywhere else: a guard that lives
in Python is a guard someone can walk around by writing a different query.

So every test here writes directly, the way a careless caller or a future feature would, and
requires the schema to refuse it. None of them go through a route, because a route obeying the rule
is exactly the thing that was never in doubt.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fixtures import FixtureData
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from models.domain import Approval, AuthorizationDecision, Payment


@pytest.mark.asyncio
async def test_one_payment_request_cannot_have_two_payments(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Two payments for one purchase is two state machines nothing reconciles.

    Both could authorize. Both could reach the provider. Both hold and release budget. Every lock
    in this codebase takes *a* payment row for a request and would never learn the other existed,
    so this is not a tidiness constraint - it is the assumption the locking rests on.
    """

    with pytest.raises(DBAPIError) as raised:
        async with async_session.begin_nested():
            async_session.add(
                Payment(
                    id=uuid4(),
                    tenant_id=seeded_fixture_data.tenant_a.id,
                    payment_request_id=seeded_fixture_data.payment_request.id,
                    state="CREATED",
                    authorized_amount_minor=None,
                    captured_amount_minor=0,
                    refunded_amount_minor=0,
                )
            )
            await async_session.flush()

    assert "uq_payment_one_per_request" in str(raised.value)


@pytest.mark.asyncio
async def test_a_decision_cannot_cite_a_policy_version_that_does_not_exist(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """`CHECKOUT_AUTHORITY_POLICY_DRIFT` compares a stored version against the live policy.

    If the stored version names nothing, that comparison stops being a statement about a policy and
    becomes a statement about an integer - and it would still pass or fail confidently.
    """

    with pytest.raises(DBAPIError) as raised:
        async with async_session.begin_nested():
            async_session.add(
                AuthorizationDecision(
                    id=uuid4(),
                    tenant_id=seeded_fixture_data.tenant_a.id,
                    payment_request_id=seeded_fixture_data.payment_request.id,
                    decision="ALLOW",
                    reasons=[],
                    policy_version=9_999,
                    correlation_id=uuid4(),
                )
            )
            await async_session.flush()

    assert "fk_authorization_decision_policy_tenant" in str(raised.value)


@pytest.mark.asyncio
async def test_an_approval_cannot_cite_a_policy_version_that_does_not_exist(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The record of a human decision, auditable against the policy that human was working under.

    Written as a consumed approval on purpose. The fixture already holds an unconsumed one for this
    request, and `uq_approval_active_per_payment` would refuse a second before the policy key was
    ever consulted - which would leave this test green while proving the wrong constraint.
    """

    with pytest.raises(DBAPIError) as raised:
        async with async_session.begin_nested():
            async_session.add(
                Approval(
                    id=uuid4(),
                    tenant_id=seeded_fixture_data.tenant_a.id,
                    payment_request_id=seeded_fixture_data.payment_request.id,
                    policy_version=9_999,
                    granted_by="someone",
                    expires_at=datetime.now(UTC) + timedelta(minutes=10),
                    consumed_at=datetime.now(UTC),
                )
            )
            await async_session.flush()

    assert "fk_approval_policy_tenant" in str(raised.value)


@pytest.mark.asyncio
async def test_a_decision_cannot_cite_another_tenants_policy_version(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The composite half of the key, which a plain foreign key on `version` would have missed.

    Version numbers restart per tenant, so tenant B having a version 1 must not let tenant A's
    decision cite it. This is why the constraint carries `tenant_id` rather than the version alone.
    """

    other = seeded_fixture_data.tenant_b_policy.version

    with pytest.raises(DBAPIError) as raised:
        async with async_session.begin_nested():
            async_session.add(
                AuthorizationDecision(
                    id=uuid4(),
                    tenant_id=seeded_fixture_data.tenant_a.id,
                    payment_request_id=seeded_fixture_data.payment_request.id,
                    decision="ALLOW",
                    reasons=[],
                    # A real version, belonging to someone else. Only the pairing is wrong.
                    policy_version=other + 1_000,
                    correlation_id=uuid4(),
                )
            )
            await async_session.flush()

    assert "fk_authorization_decision_policy_tenant" in str(raised.value)


@pytest.mark.asyncio
async def test_the_ordinary_path_still_writes_all_three(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """A constraint that refuses everything also passes every test above it."""

    decision = AuthorizationDecision(
        id=uuid4(),
        tenant_id=seeded_fixture_data.tenant_a.id,
        payment_request_id=seeded_fixture_data.payment_request.id,
        decision="ALLOW",
        reasons=[],
        policy_version=seeded_fixture_data.tenant_a_policy.version,
        correlation_id=uuid4(),
    )
    async_session.add(decision)
    await async_session.flush()

    assert decision.id is not None
