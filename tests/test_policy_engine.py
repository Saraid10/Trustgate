from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fixtures import FixtureData
from httpx import ASGITransport, AsyncClient
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app import app
from api.database import get_session
from models.domain import (
    Approval,
    AuditEvent,
    DailySpendReservation,
    Payment,
    PaymentRequest,
    PolicyMerchant,
    SpendingPolicy,
)
from policy_engine.evaluate import PolicyRules, evaluate_payment_request, evaluate_policy_rules
from state_machine.transitions import (
    ApprovalAlreadyConsumedError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
    ApprovalPolicyVersionMismatchError,
    ApprovalRequiredForAuthorizationError,
    transition,
)

OPTIMIZED_TRANSITION_SCRIPT = """
import asyncio
import json
import os
import sys
from uuid import uuid4
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from models.domain import AuditEvent, Merchant, Payment, PaymentRequest, Tenant
from state_machine.transitions import ApprovalRequiredForAuthorizationError, transition

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

async def main():
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.connect() as connection:
        outer = await connection.begin()
        session_factory = async_sessionmaker(bind=connection, expire_on_commit=False)
        async with session_factory() as session:
            tenant = Tenant(name=f"optimized-{uuid4()}")
            session.add(tenant)
            await session.flush()
            merchant = Merchant(tenant_id=tenant.id, name="optimized merchant", is_active=True)
            session.add(merchant)
            await session.flush()
            request = PaymentRequest(
                tenant_id=tenant.id, actor_id="actor", merchant_id=merchant.id,
                amount_minor=1, currency="INR", order_ref="optimized", idempotency_key=str(uuid4()),
            )
            session.add(request)
            await session.flush()
            payment = Payment(
                tenant_id=tenant.id, payment_request_id=request.id, state="APPROVAL_REQUIRED",
                authorized_amount_minor=None, captured_amount_minor=0, refunded_amount_minor=0,
            )
            session.add(payment)
            await session.flush()
            correlation_id = uuid4()
            raised = False
            try:
                await transition(
                    session,
                    payment,
                    "AUTHORIZED",
                    reason="optimized-test",
                    correlation_id=correlation_id,
                )
            except ApprovalRequiredForAuthorizationError:
                raised = True
            count = await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.correlation_id == correlation_id)
            )
            print(json.dumps({"raised": raised, "audit_count": count}))
        await outer.rollback()
    await engine.dispose()

asyncio.run(main())
"""


@pytest_asyncio.fixture
async def api_client(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("DEMO_APPROVER_TOKEN", "test-approver-token")
    monkeypatch.setenv("DEMO_APPROVER_ID", "test-human-reviewer")
    monkeypatch.setenv("ENABLE_LEGACY_PAYMENT_REQUEST_API", "true")

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def _headers(data: FixtureData) -> dict[str, str]:
    return {"X-Tenant-Id": str(data.tenant_a.id)}


def _approver_headers(data: FixtureData) -> dict[str, str]:
    return {**_headers(data), "X-Approver-Token": "test-approver-token"}


def _payload(data: FixtureData, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "actor_id": data.tenant_a_actor_two,
        "merchant_id": str(data.tenant_a_allowed_merchant.id),
        "amount_minor": 20_000,
        "currency": "INR",
        "order_ref": f"order-{uuid4()}",
        "idempotency_key": str(uuid4()),
    }
    payload.update(overrides)
    return payload


async def _publish_tenant_a_policy(
    session: AsyncSession,
    data: FixtureData,
    *,
    max_daily_spend_minor: int | None = None,
    expiry: datetime | None = None,
) -> SpendingPolicy:
    current = data.tenant_a_policy
    policy = SpendingPolicy(
        tenant_id=data.tenant_a.id,
        version=current.version + 1,
        max_amount_minor=current.max_amount_minor,
        currency=current.currency,
        max_daily_spend_minor=(
            max_daily_spend_minor
            if max_daily_spend_minor is not None
            else current.max_daily_spend_minor
        ),
        expiry=expiry if expiry is not None else current.expiry,
        approval_required_above_minor=current.approval_required_above_minor,
    )
    session.add(policy)
    await session.flush()
    session.add(
        PolicyMerchant(
            tenant_id=data.tenant_a.id,
            policy_id=policy.id,
            merchant_id=data.tenant_a_allowed_merchant.id,
        )
    )
    await session.flush()
    return policy


@settings(max_examples=300, deadline=None)
@given(
    max_amount=st.integers(min_value=0, max_value=100_000),
    daily_limit=st.integers(min_value=0, max_value=200_000),
    daily_spend=st.integers(min_value=0, max_value=200_000),
    amount=st.integers(min_value=0, max_value=100_000),
    threshold=st.one_of(st.none(), st.integers(min_value=0, max_value=100_000)),
)
def test_generated_policy_amount_and_daily_rules_are_complete(
    max_amount: int,
    daily_limit: int,
    daily_spend: int,
    amount: int,
    threshold: int | None,
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    result = evaluate_policy_rules(
        PolicyRules(1, max_amount, "INR", daily_limit, now + timedelta(days=1), threshold),
        merchant_is_allowed=True,
        daily_spend_minor=daily_spend,
        amount_minor=amount,
        currency="INR",
        as_of=now,
    )
    expected_reasons = []
    if amount > max_amount:
        expected_reasons.append("AMOUNT_EXCEEDS_LIMIT")
    if daily_spend + amount > daily_limit:
        expected_reasons.append("DAILY_LIMIT_EXCEEDED")
    if expected_reasons:
        assert result.decision == "DENY"
        assert result.reasons == expected_reasons
    elif threshold is not None and amount > threshold:
        assert result.decision == "REQUIRE_APPROVAL"
        assert result.reasons == ["APPROVAL_REQUIRED"]
    else:
        assert result.decision == "ALLOW"
        assert result.reasons == []


@settings(max_examples=300, deadline=None)
@given(
    wrong_currency=st.booleans(),
    merchant_is_allowed=st.booleans(),
    expired=st.booleans(),
)
def test_generated_policy_denial_precedence_is_stable(
    wrong_currency: bool, merchant_is_allowed: bool, expired: bool
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    result = evaluate_policy_rules(
        PolicyRules(
            2,
            100,
            "INR",
            100,
            now - timedelta(seconds=1) if expired else now + timedelta(days=1),
            10,
        ),
        merchant_is_allowed=merchant_is_allowed,
        daily_spend_minor=0,
        amount_minor=20,
        currency="USD" if wrong_currency else "INR",
        as_of=now,
    )
    if expired:
        assert result.decision == "DENY"
        assert result.reasons == ["POLICY_EXPIRED"]
    elif wrong_currency or not merchant_is_allowed:
        expected = []
        if wrong_currency:
            expected.append("CURRENCY_NOT_ALLOWED")
        if not merchant_is_allowed:
            expected.append("MERCHANT_NOT_ALLOWED")
        assert result.decision == "DENY"
        assert result.reasons == expected
    else:
        assert result.decision == "REQUIRE_APPROVAL"
        assert result.reasons == ["APPROVAL_REQUIRED"]


@pytest.mark.asyncio
async def test_policy_allows_an_in_limit_allowed_merchant(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    result = await evaluate_payment_request(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        actor_id=seeded_fixture_data.tenant_a_actor_two,
        merchant_id=seeded_fixture_data.tenant_a_allowed_merchant.id,
        amount_minor=20_000,
        currency="INR",
    )
    assert result.decision == "ALLOW"
    assert result.reasons == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("amount_minor", "currency", "merchant_attribute", "expected_reason"),
    [
        (20_000, "USD", "tenant_a_allowed_merchant", "CURRENCY_NOT_ALLOWED"),
        (100_001, "INR", "tenant_a_allowed_merchant", "AMOUNT_EXCEEDS_LIMIT"),
        (20_000, "INR", "tenant_a_blocked_merchant", "MERCHANT_NOT_ALLOWED"),
    ],
)
async def test_policy_denies_each_direct_rule(
    async_session: AsyncSession,
    seeded_fixture_data: FixtureData,
    amount_minor: int,
    currency: str,
    merchant_attribute: str,
    expected_reason: str,
) -> None:
    merchant = getattr(seeded_fixture_data, merchant_attribute)
    result = await evaluate_payment_request(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        actor_id=seeded_fixture_data.tenant_a_actor_two,
        merchant_id=merchant.id,
        amount_minor=amount_minor,
        currency=currency,
    )
    assert result.decision == "DENY"
    assert expected_reason in result.reasons


@pytest.mark.asyncio
async def test_policy_requires_approval_above_its_threshold(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    result = await evaluate_payment_request(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        actor_id=seeded_fixture_data.tenant_a_actor_two,
        merchant_id=seeded_fixture_data.tenant_a_allowed_merchant.id,
        amount_minor=50_001,
        currency="INR",
    )
    assert result.decision == "REQUIRE_APPROVAL"
    assert result.reasons == ["APPROVAL_REQUIRED"]


@pytest.mark.asyncio
async def test_policy_enforces_utc_daily_allow_spend(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    await _publish_tenant_a_policy(async_session, seeded_fixture_data, max_daily_spend_minor=15_000)
    result = await evaluate_payment_request(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        actor_id=seeded_fixture_data.tenant_a_actor_one,
        merchant_id=seeded_fixture_data.tenant_a_allowed_merchant.id,
        amount_minor=5_001,
        currency="INR",
    )
    assert result.reasons == ["DAILY_LIMIT_EXCEEDED"]


@pytest.mark.asyncio
async def test_policy_allows_the_exact_daily_spend_boundary(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    await _publish_tenant_a_policy(async_session, seeded_fixture_data, max_daily_spend_minor=20_000)
    result = await evaluate_payment_request(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        actor_id=seeded_fixture_data.tenant_a_actor_one,
        merchant_id=seeded_fixture_data.tenant_a_allowed_merchant.id,
        amount_minor=10_000,
        currency="INR",
    )
    assert result.decision == "ALLOW"


@pytest.mark.asyncio
async def test_policy_rejects_expired_current_policy(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    await _publish_tenant_a_policy(
        async_session,
        seeded_fixture_data,
        expiry=datetime.now(UTC) - timedelta(seconds=1),
    )
    result = await evaluate_payment_request(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        actor_id=seeded_fixture_data.tenant_a_actor_two,
        merchant_id=seeded_fixture_data.tenant_a_allowed_merchant.id,
        amount_minor=1,
        currency="INR",
    )
    assert result.decision == "DENY"
    assert result.reasons == ["POLICY_EXPIRED"]


@pytest.mark.asyncio
async def test_policy_without_a_tenant_configuration_fails_closed(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    result = await evaluate_payment_request(
        async_session,
        tenant_id=uuid4(),
        actor_id=seeded_fixture_data.tenant_a_actor_two,
        merchant_id=seeded_fixture_data.tenant_a_allowed_merchant.id,
        amount_minor=1,
        currency="INR",
    )
    assert result.decision == "DENY"
    # Not POLICY_EXPIRED. There is no policy here, and saying one expired sends whoever reads it
    # looking for a date that was never set. Both fail closed; only the diagnosis differs.
    assert result.reasons == ["POLICY_NOT_FOUND"]
    assert result.policy_version == 0


@pytest.mark.asyncio
async def test_policy_denies_an_inactive_but_otherwise_allowed_merchant(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    seeded_fixture_data.tenant_a_allowed_merchant.is_active = False
    await async_session.flush()
    result = await evaluate_payment_request(
        async_session,
        tenant_id=seeded_fixture_data.tenant_a.id,
        actor_id=seeded_fixture_data.tenant_a_actor_two,
        merchant_id=seeded_fixture_data.tenant_a_allowed_merchant.id,
        amount_minor=20_000,
        currency="INR",
    )
    assert result.decision == "DENY"
    assert result.reasons == ["MERCHANT_NOT_ALLOWED"]


@pytest.mark.asyncio
async def test_payment_request_uses_trusted_tenant_and_creates_authorized_payment(
    api_client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    response = await api_client.post(
        "/api/v1/payment-requests",
        json=_payload(seeded_fixture_data),
        headers=_headers(seeded_fixture_data),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["decision"] == "ALLOW"
    assert body["next_state"] == "AUTHORIZED"


@pytest.mark.asyncio
async def test_payment_request_cannot_use_a_merchant_from_another_tenant(
    api_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    payment_request_count_before = await async_session.scalar(
        select(func.count())
        .select_from(PaymentRequest)
        .where(PaymentRequest.tenant_id == seeded_fixture_data.tenant_a.id)
    )
    response = await api_client.post(
        "/api/v1/payment-requests",
        json=_payload(
            seeded_fixture_data,
            merchant_id=str(seeded_fixture_data.tenant_b_allowed_merchant.id),
        ),
        headers=_headers(seeded_fixture_data),
    )
    payment_request_count_after = await async_session.scalar(
        select(func.count())
        .select_from(PaymentRequest)
        .where(PaymentRequest.tenant_id == seeded_fixture_data.tenant_a.id)
    )
    audit_events = list(
        await async_session.scalars(
            select(AuditEvent).where(
                AuditEvent.tenant_id == seeded_fixture_data.tenant_a.id,
                AuditEvent.event_kind == "payment_request_rejected",
            )
        )
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "CROSS_TENANT_ACCESS_DENIED"}
    assert payment_request_count_after == payment_request_count_before
    assert len(audit_events) == 1
    assert audit_events[0].payload == {
        "reason": "CROSS_TENANT_ACCESS_DENIED",
        "merchant_id": str(seeded_fixture_data.tenant_b_allowed_merchant.id),
    }


@pytest.mark.asyncio
async def test_unknown_or_body_supplied_tenant_is_rejected(
    api_client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    unknown = await api_client.post(
        "/api/v1/payment-requests",
        json=_payload(seeded_fixture_data),
        headers={"X-Tenant-Id": str(uuid4())},
    )
    body_tenant = await api_client.post(
        "/api/v1/payment-requests",
        json=_payload(seeded_fixture_data, tenant_id=str(seeded_fixture_data.tenant_b.id)),
        headers=_headers(seeded_fixture_data),
    )
    assert unknown.status_code == 403
    assert body_tenant.status_code == 422


@pytest.mark.asyncio
async def test_idempotency_replays_same_payload_and_audits_collision(
    api_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    payload = _payload(seeded_fixture_data)
    first = await api_client.post(
        "/api/v1/payment-requests", json=payload, headers=_headers(seeded_fixture_data)
    )
    replay = await api_client.post(
        "/api/v1/payment-requests", json=payload, headers=_headers(seeded_fixture_data)
    )
    collision = await api_client.post(
        "/api/v1/payment-requests",
        json={**payload, "amount_minor": 20_001},
        headers=_headers(seeded_fixture_data),
    )
    audit_count = await async_session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.event_kind == "idempotency_key_collision")
    )
    assert replay.status_code == 201
    assert replay.json() == first.json()
    assert collision.status_code == 409
    assert collision.json() == first.json()
    assert audit_count == 1


@pytest.mark.asyncio
async def test_approval_grant_and_atomic_consumption(
    api_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    created = await api_client.post(
        "/api/v1/payment-requests",
        json=_payload(seeded_fixture_data, amount_minor=50_001),
        headers=_headers(seeded_fixture_data),
    )
    request_id = created.json()["payment_request_id"]
    grant = await api_client.post(
        f"/api/v1/approvals/{request_id}/grant",
        headers=_approver_headers(seeded_fixture_data),
    )
    payment = await async_session.scalar(
        select(Payment).where(
            Payment.tenant_id == seeded_fixture_data.tenant_a.id,
            Payment.payment_request_id == UUID(request_id),
        )
    )
    assert created.json()["decision"] == "REQUIRE_APPROVAL"
    assert grant.status_code == 200
    assert payment is not None and payment.state == "AUTHORIZED"
    approval = await async_session.scalar(
        select(Approval).where(Approval.id == UUID(grant.json()["approval_id"]))
    )
    assert approval is not None
    assert approval.consumed_at is not None


@pytest.mark.asyncio
async def test_approval_cannot_be_granted_twice_or_from_another_tenant(
    api_client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    created = await api_client.post(
        "/api/v1/payment-requests",
        json=_payload(seeded_fixture_data, amount_minor=50_001),
        headers=_headers(seeded_fixture_data),
    )
    request_id = created.json()["payment_request_id"]
    grant = await api_client.post(
        f"/api/v1/approvals/{request_id}/grant",
        headers=_approver_headers(seeded_fixture_data),
    )
    duplicate = await api_client.post(
        f"/api/v1/approvals/{request_id}/grant",
        headers=_approver_headers(seeded_fixture_data),
    )
    cross_tenant = await api_client.post(
        f"/api/v1/approvals/{request_id}/grant",
        headers={
            "X-Tenant-Id": str(seeded_fixture_data.tenant_b.id),
            "X-Approver-Token": "test-approver-token",
        },
    )
    assert grant.status_code == 200
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "APPROVAL_NOT_REQUIRED"
    assert cross_tenant.status_code == 404


@pytest.mark.asyncio
async def test_approval_grant_requires_a_separate_approver_token(
    api_client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    created = await api_client.post(
        "/api/v1/payment-requests",
        json=_payload(seeded_fixture_data, amount_minor=50_001),
        headers=_headers(seeded_fixture_data),
    )
    response = await api_client.post(
        f"/api/v1/approvals/{created.json()['payment_request_id']}/grant",
        headers=_headers(seeded_fixture_data),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_approval_required_request_reserves_daily_budget_atomically(
    api_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    await _publish_tenant_a_policy(async_session, seeded_fixture_data, max_daily_spend_minor=60_000)
    first = await api_client.post(
        "/api/v1/payment-requests",
        json=_payload(seeded_fixture_data, amount_minor=50_001),
        headers=_headers(seeded_fixture_data),
    )
    second = await api_client.post(
        "/api/v1/payment-requests",
        json=_payload(seeded_fixture_data, amount_minor=10_000),
        headers=_headers(seeded_fixture_data),
    )
    reservation = await async_session.scalar(
        select(DailySpendReservation).where(
            DailySpendReservation.tenant_id == seeded_fixture_data.tenant_a.id,
            DailySpendReservation.actor_id == seeded_fixture_data.tenant_a_actor_two,
        )
    )

    assert first.status_code == 201
    assert first.json()["decision"] == "REQUIRE_APPROVAL"
    assert second.status_code == 201
    assert second.json()["decision"] == "DENY"
    assert second.json()["reasons"] == ["DAILY_LIMIT_EXCEEDED"]
    assert reservation is not None
    assert reservation.reserved_amount_minor == 50_001


@pytest.mark.asyncio
async def test_missing_approval_id_raises_and_writes_exactly_one_audit_event(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    payment = seeded_fixture_data.payment
    payment.state = "APPROVAL_REQUIRED"
    correlation_id = uuid4()
    with pytest.raises(ApprovalRequiredForAuthorizationError):
        await transition(
            async_session,
            payment,
            "AUTHORIZED",
            reason="approval_granted",
            correlation_id=correlation_id,
        )
    events = list(
        await async_session.scalars(
            select(AuditEvent).where(AuditEvent.correlation_id == correlation_id)
        )
    )
    assert len(events) == 1
    assert events[0].payload == {
        "reason": "APPROVAL_REQUIRED_MISSING",
        "payment_id": str(payment.id),
    }


@pytest.mark.parametrize("optimized", [False, True])
def test_missing_approval_behavior_is_identical_under_python_optimized_mode(
    optimized: bool,
) -> None:
    command = [sys.executable]
    if optimized:
        command.append("-O")
    command.extend(["-c", textwrap.dedent(OPTIMIZED_TRANSITION_SCRIPT)])
    completed = subprocess.run(  # noqa: S603
        command,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "DATABASE_URL": os.getenv(
                "DATABASE_URL",
                "postgresql+psycopg://payment_safety:payment_safety@127.0.0.1:5432/payment_safety",
            ),
        },
    )
    assert json.loads(completed.stdout) == {"raised": True, "audit_count": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("approval_id", "mutation", "error_type"),
    [
        (None, "none", ApprovalNotFoundError),
        ("consumed", "none", ApprovalAlreadyConsumedError),
        ("expired", "expired", ApprovalExpiredError),
        ("mismatch", "mismatch", ApprovalPolicyVersionMismatchError),
    ],
)
async def test_approval_consumption_rejects_invalid_approval_states(
    async_session: AsyncSession,
    seeded_fixture_data: FixtureData,
    approval_id: str | None,
    mutation: str,
    error_type: type[Exception],
) -> None:
    payment = seeded_fixture_data.payment
    payment.state = "APPROVAL_REQUIRED"
    approval = seeded_fixture_data.unconsumed_approval
    if approval_id is None:
        selected_id = uuid4()
    elif approval_id == "consumed":
        selected_id = seeded_fixture_data.consumed_approval.id
    else:
        selected_id = approval.id
    if mutation == "expired":
        approval.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    if mutation == "mismatch":
        approval.policy_version += 1
    await async_session.flush()
    with pytest.raises(error_type):
        await transition(
            async_session,
            payment,
            "AUTHORIZED",
            reason="approval_granted",
            correlation_id=uuid4(),
            approval_id=selected_id,
        )


@pytest.mark.asyncio
async def test_internal_policy_route_requires_token_and_creates_next_version(
    api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, seeded_fixture_data: FixtureData
) -> None:
    monkeypatch.setenv("INTERNAL_ADMIN_TOKEN", "test-token")
    payload = {
        "tenant_id": str(seeded_fixture_data.tenant_a.id),
        "max_amount_minor": 90_000,
        "currency": "INR",
        "max_daily_spend_minor": 190_000,
        "expiry": (datetime.now(UTC) + timedelta(days=15)).isoformat(),
        "approval_required_above_minor": 40_000,
        "allowed_merchant_ids": [str(seeded_fixture_data.tenant_a_allowed_merchant.id)],
    }
    denied = await api_client.post("/internal/policies", json=payload)
    created = await api_client.post(
        "/internal/policies", json=payload, headers={"X-Internal-Admin-Token": "test-token"}
    )
    assert denied.status_code == 422
    assert created.status_code == 201
    assert created.json()["version"] == 2


@pytest.mark.asyncio
async def test_internal_policy_route_rejects_an_invalid_token(
    api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, seeded_fixture_data: FixtureData
) -> None:
    monkeypatch.setenv("INTERNAL_ADMIN_TOKEN", "test-token")
    response = await api_client.post(
        "/internal/policies",
        json={
            "tenant_id": str(seeded_fixture_data.tenant_a.id),
            "max_amount_minor": 1,
            "currency": "INR",
            "max_daily_spend_minor": 1,
            "expiry": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "allowed_merchant_ids": [str(seeded_fixture_data.tenant_a_allowed_merchant.id)],
        },
        headers={"X-Internal-Admin-Token": "wrong-token"},
    )
    assert response.status_code == 403
