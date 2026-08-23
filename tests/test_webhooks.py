from __future__ import annotations

import json
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
from mock_provider.app import app as mock_provider_app
from mock_provider.signing import sign_payload, signature_is_valid
from models.domain import AuditEvent, Payment, ProviderEvent

WEBHOOK_SECRET = "slice-five-test-secret"  # noqa: S105


@pytest_asyncio.fixture
async def webhook_client(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("PROVIDER_WEBHOOK_SECRET", WEBHOOK_SECRET)

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def _body(
    data: FixtureData,
    *,
    event_type: str,
    event_id: UUID | None = None,
    tenant_id: UUID | None = None,
    payment_id: UUID | None = None,
    occurred_at: datetime | None = None,
) -> bytes:
    return json.dumps(
        {
            "event_id": str(event_id or uuid4()),
            "event_type": event_type,
            "tenant_id": str(tenant_id or data.tenant_a.id),
            "payment_id": str(payment_id or data.payment.id),
            "occurred_at": (occurred_at or datetime.now(UTC)).isoformat(),
        },
        separators=(",", ":"),
    ).encode("utf-8")


async def _post_signed(client: AsyncClient, body: bytes, signature_body: bytes | None = None):
    signed = signature_body or body
    return await client.post(
        "/api/v1/webhooks/provider-events",
        content=body,
        headers={"X-Provider-Signature": sign_payload(signed, WEBHOOK_SECRET)},
    )


@settings(max_examples=200, deadline=None)
@given(
    raw_body=st.binary(min_size=0, max_size=512),
    replacement=st.binary(min_size=0, max_size=512),
)
def test_signature_verification_rejects_changed_raw_bytes(
    raw_body: bytes, replacement: bytes
) -> None:
    signature = sign_payload(raw_body, WEBHOOK_SECRET)
    if replacement == raw_body:
        assert signature_is_valid(replacement, signature, WEBHOOK_SECRET)
    else:
        assert not signature_is_valid(replacement, signature, WEBHOOK_SECRET)


@pytest.mark.asyncio
async def test_mock_provider_posts_the_exact_signed_wire_body(
    monkeypatch: pytest.MonkeyPatch, seeded_fixture_data: FixtureData
) -> None:
    delivered: dict[str, object] = {}

    class FakeResponse:
        status_code = 202

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> FakeResponse:
            delivered["url"] = url
            delivered.update(kwargs)
            return FakeResponse()

    monkeypatch.setenv("PROVIDER_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("PROVIDER_CALLBACK_URL", "http://main.test/api/v1/webhooks/provider-events")
    monkeypatch.setattr("mock_provider.app.httpx.AsyncClient", FakeClient)
    async with AsyncClient(
        transport=ASGITransport(app=mock_provider_app), base_url="http://provider"
    ) as client:
        response = await client.post(
            "/mock-provider/simulate/payment.authorized",
            json={
                "tenant_id": str(seeded_fixture_data.tenant_a.id),
                "payment_id": str(seeded_fixture_data.payment.id),
            },
        )
    raw_body = delivered["content"]
    headers = delivered["headers"]
    assert response.status_code == 200
    assert delivered["url"] == "http://main.test/api/v1/webhooks/provider-events"
    assert isinstance(raw_body, bytes)
    assert isinstance(headers, dict)
    assert signature_is_valid(raw_body, headers["X-Provider-Signature"], WEBHOOK_SECRET)
    assert json.loads(raw_body)["event_type"] == "payment.authorized"


@pytest.mark.asyncio
async def test_valid_authorized_event_uses_raw_signature_and_moves_to_provider_pending(
    webhook_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    payment = seeded_fixture_data.payment
    payment.state = "AUTHORIZED"
    payment.authorized_amount_minor = 10_000
    await async_session.flush()
    body = _body(seeded_fixture_data, event_type="payment.authorized")

    response = await _post_signed(webhook_client, body)
    stored = await async_session.scalar(select(Payment).where(Payment.id == payment.id))
    provider_event = await async_session.scalar(
        select(ProviderEvent).where(
            ProviderEvent.tenant_id == seeded_fixture_data.tenant_a.id,
            ProviderEvent.provider_event_id == json.loads(body)["event_id"],
        )
    )
    assert response.status_code == 202
    assert stored is not None and stored.state == "PROVIDER_PENDING"
    assert provider_event is not None
    assert provider_event.provider_event_id == json.loads(body)["event_id"]
    assert provider_event.raw_payload == body
    assert provider_event.processed_at is not None


@pytest.mark.asyncio
async def test_forged_signature_is_logged_without_a_tenant_audit_event(
    webhook_client: AsyncClient,
    async_session: AsyncSession,
    seeded_fixture_data: FixtureData,
    caplog: pytest.LogCaptureFixture,
) -> None:
    body = _body(seeded_fixture_data, event_type="payment.authorized")
    before = await async_session.scalar(select(func.count()).select_from(AuditEvent))
    response = await webhook_client.post(
        "/api/v1/webhooks/provider-events",
        content=body,
        headers={"X-Provider-Signature": "forged"},
    )
    after = await async_session.scalar(select(func.count()).select_from(AuditEvent))
    assert response.status_code == 401
    assert response.json()["detail"] == "WEBHOOK_SIGNATURE_INVALID"
    assert after == before
    assert any("WEBHOOK_SIGNATURE_INVALID" in record.message for record in caplog.records)
    assert all(body.decode("utf-8") not in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_tampered_body_is_logged_without_a_tenant_audit_event(
    webhook_client: AsyncClient,
    async_session: AsyncSession,
    seeded_fixture_data: FixtureData,
) -> None:
    original = _body(seeded_fixture_data, event_type="payment.authorized")
    tampered = original.replace(b"authorized", b"captured__")
    before = await async_session.scalar(select(func.count()).select_from(AuditEvent))
    response = await _post_signed(webhook_client, tampered, signature_body=original)
    after = await async_session.scalar(select(func.count()).select_from(AuditEvent))
    assert response.status_code == 401
    assert response.json()["detail"] == "WEBHOOK_SIGNATURE_INVALID"
    assert after == before


@pytest.mark.asyncio
async def test_oversized_webhook_is_rejected_before_signature_processing(
    webhook_client: AsyncClient, async_session: AsyncSession
) -> None:
    before = await async_session.scalar(select(func.count()).select_from(AuditEvent))
    response = await webhook_client.post(
        "/api/v1/webhooks/provider-events",
        content=b"x" * (64 * 1024 + 1),
        headers={"X-Provider-Signature": "forged"},
    )
    after = await async_session.scalar(select(func.count()).select_from(AuditEvent))

    assert response.status_code == 413
    assert response.json()["detail"] == "WEBHOOK_BODY_TOO_LARGE"
    assert after == before


@pytest.mark.asyncio
async def test_stale_signed_event_is_tenant_audited_and_does_not_transition(
    webhook_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    payment = seeded_fixture_data.payment
    payment.state = "AUTHORIZED"
    payment.authorized_amount_minor = 10_000
    await async_session.flush()
    body = _body(
        seeded_fixture_data,
        event_type="payment.authorized",
        occurred_at=datetime.now(UTC) - timedelta(minutes=5, seconds=1),
    )
    response = await _post_signed(webhook_client, body)
    audit = await async_session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.tenant_id == seeded_fixture_data.tenant_a.id,
            AuditEvent.event_kind == "webhook_rejected",
        )
        .order_by(AuditEvent.created_at.desc())
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "WEBHOOK_TIMESTAMP_STALE"
    assert payment.state == "AUTHORIZED"
    assert audit is not None and audit.payload["reason"] == "WEBHOOK_TIMESTAMP_STALE"


@pytest.mark.asyncio
async def test_signed_tenant_mismatch_is_tenant_audited(
    webhook_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    body = _body(
        seeded_fixture_data,
        event_type="payment.authorized",
        tenant_id=seeded_fixture_data.tenant_b.id,
    )
    response = await _post_signed(webhook_client, body)
    audit = await async_session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.tenant_id == seeded_fixture_data.tenant_b.id,
            AuditEvent.event_kind == "webhook_rejected",
        )
        .order_by(AuditEvent.created_at.desc())
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "WEBHOOK_TENANT_MISMATCH"
    assert audit is not None and audit.payload["reason"] == "WEBHOOK_TENANT_MISMATCH"


@pytest.mark.asyncio
async def test_duplicate_event_is_audited_after_the_first_delivery(
    webhook_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    payment = seeded_fixture_data.payment
    payment.state = "AUTHORIZED"
    payment.authorized_amount_minor = 10_000
    await async_session.flush()
    body = _body(seeded_fixture_data, event_type="payment.authorized")
    first = await _post_signed(webhook_client, body)
    second = await _post_signed(webhook_client, body)
    audit = await async_session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.tenant_id == seeded_fixture_data.tenant_a.id,
            AuditEvent.event_kind == "webhook_rejected",
        )
        .order_by(AuditEvent.created_at.desc())
    )
    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["detail"] == "WEBHOOK_DUPLICATE_EVENT"
    assert audit is not None and audit.payload["reason"] == "WEBHOOK_DUPLICATE_EVENT"


@pytest.mark.asyncio
async def test_event_ordering_rejects_capture_before_authorized_provider_event(
    webhook_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    payment = seeded_fixture_data.payment
    payment.state = "AUTHORIZED"
    payment.authorized_amount_minor = 10_000
    await async_session.flush()
    payment_id = payment.id
    response = await _post_signed(
        webhook_client, _body(seeded_fixture_data, event_type="payment.captured")
    )
    stored = await async_session.scalar(select(Payment).where(Payment.id == payment_id))
    assert response.status_code == 409
    assert response.json()["detail"] == "ILLEGAL_STATE_TRANSITION"
    assert stored is not None and stored.state == "AUTHORIZED"


@pytest.mark.asyncio
async def test_valid_provider_lifecycle_captures_and_refunds_full_authorization(
    webhook_client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    payment = seeded_fixture_data.payment
    payment.state = "AUTHORIZED"
    payment.authorized_amount_minor = 10_000
    await async_session.flush()
    for event_type in ("payment.authorized", "payment.captured", "payment.refunded"):
        response = await _post_signed(
            webhook_client, _body(seeded_fixture_data, event_type=event_type)
        )
        assert response.status_code == 202
    stored = await async_session.scalar(select(Payment).where(Payment.id == payment.id))
    assert stored is not None
    assert stored.state == "REFUNDED"
    assert stored.captured_amount_minor == 10_000
    assert stored.refunded_amount_minor == 10_000
