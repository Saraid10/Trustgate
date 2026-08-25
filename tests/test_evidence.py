"""The evidence receipt: what was proposed, what was authorized, what the provider did.

The separation is the point. A receipt that merged the agent's proposal with the server's derived
facts would hide exactly the boundary this project exists to demonstrate, so these tests assert
the stages stay distinguishable and that a denied attempt is evidenced as thoroughly as an
approved one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from fixtures import FixtureData
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.app import app
from api.database import get_session
from models.domain import CatalogItem, PaymentRequest


@pytest_asyncio.fixture
async def client(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("TRUSTGATE_API_ACTOR_ID", "evidence-test-actor")

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


def _headers(data: FixtureData) -> dict[str, str]:
    return {"X-Tenant-Id": str(data.tenant_a.id)}


def _purchase(**overrides: object) -> dict[str, object]:
    return {
        "sku": "CLOUD-STARTER",
        "quantity": 1,
        "purpose": "Provision an isolated build environment.",
        "idempotency_key": str(uuid4()),
        **overrides,
    }


async def _create_request(client: AsyncClient, data: FixtureData, **overrides: object) -> str:
    response = await client.post(
        "/api/v1/catalog-payment-requests",
        json=_purchase(**overrides),
        headers=_headers(data),
    )
    assert response.status_code in {200, 201}
    return str(response.json()["payment_request_id"])


async def test_receipt_separates_the_proposal_from_the_server_derived_facts(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    request_id = await _create_request(client, seeded_fixture_data)

    response = await client.get(
        f"/api/v1/payment-requests/{request_id}/evidence", headers=_headers(seeded_fixture_data)
    )
    body = response.json()

    assert response.status_code == 200
    # The agent chose these.
    assert body["proposed"]["sku"] == "CLOUD-STARTER"
    assert body["proposed"]["quantity"] == 1
    assert body["proposed"]["source"] == "API"
    # The server determined these; none of them appear in the proposal stage.
    assert body["derived"]["amount_minor"] == 39_900
    assert body["derived"]["currency"] == "INR"
    assert body["derived"]["merchant_display_name"] == "A Allowed One"
    assert set(body["proposed"]) & {"amount_minor", "currency", "merchant_id"} == set()


async def test_receipt_records_the_policy_and_decision_that_authorized_the_request(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    request_id = await _create_request(client, seeded_fixture_data)

    body = (
        await client.get(
            f"/api/v1/payment-requests/{request_id}/evidence",
            headers=_headers(seeded_fixture_data),
        )
    ).json()

    assert body["decision"]["decision"] == "ALLOW"
    assert body["decision"]["reasons"] == []
    assert body["policy"]["version"] == body["decision"]["policy_version"]
    assert body["policy"]["max_amount_minor"] == 100_000
    assert body["payment"]["state"] == "AUTHORIZED"


async def test_a_denied_request_is_evidenced_as_fully_as_an_allowed_one(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Denied attempts are the cases the attack suite must be able to show."""

    async_session.add(
        CatalogItem(
            id=uuid4(),
            tenant_id=seeded_fixture_data.tenant_a.id,
            merchant_id=seeded_fixture_data.tenant_a_blocked_merchant.id,
            sku="EVIDENCE-BLOCKED",
            name="Blocked Merchant Item",
            description_untrusted="Synthetic item bound to a merchant outside the active policy.",
            price_minor=10_000,
            currency="INR",
            max_quantity=1,
            active=True,
        )
    )
    await async_session.flush()
    request_id = await _create_request(client, seeded_fixture_data, sku="EVIDENCE-BLOCKED")

    body = (
        await client.get(
            f"/api/v1/payment-requests/{request_id}/evidence",
            headers=_headers(seeded_fixture_data),
        )
    ).json()

    assert body["decision"]["decision"] == "DENY"
    assert "MERCHANT_NOT_ALLOWED" in body["decision"]["reasons"]
    assert body["policy"] is not None
    assert body["derived"]["amount_minor"] == 10_000
    # Denied means no authority was issued and no provider order exists.
    assert body["authority"] is None
    assert body["provider_order"] is None
    assert body["provider_events"] == []


async def test_receipt_reports_no_provider_outcome_before_one_exists(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    request_id = await _create_request(client, seeded_fixture_data)

    body = (
        await client.get(
            f"/api/v1/payment-requests/{request_id}/evidence",
            headers=_headers(seeded_fixture_data),
        )
    ).json()

    assert body["provider_order"] is None
    assert body["provider_events"] == []
    assert body["authority"] is None


async def test_another_tenant_cannot_read_the_receipt(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    request_id = await _create_request(client, seeded_fixture_data)

    response = await client.get(
        f"/api/v1/payment-requests/{request_id}/evidence",
        headers={"X-Tenant-Id": str(seeded_fixture_data.tenant_b.id)},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "PAYMENT_REQUEST_NOT_FOUND"


async def test_a_cross_tenant_read_is_indistinguishable_from_an_unknown_id(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    """The response must not confirm that an identifier exists under another tenant."""

    request_id = await _create_request(client, seeded_fixture_data)
    headers = {"X-Tenant-Id": str(seeded_fixture_data.tenant_b.id)}

    real = await client.get(f"/api/v1/payment-requests/{request_id}/evidence", headers=headers)
    unknown = await client.get(f"/api/v1/payment-requests/{uuid4()}/evidence", headers=headers)

    assert real.status_code == unknown.status_code
    assert real.json() == unknown.json()


async def test_receipt_carries_the_audit_trail_for_its_decision(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    request_id = await _create_request(client, seeded_fixture_data)

    body = (
        await client.get(
            f"/api/v1/payment-requests/{request_id}/evidence",
            headers=_headers(seeded_fixture_data),
        )
    ).json()

    correlation = body["decision"]["correlation_id"]
    assert body["audit_trail"], "decision correlation produced no audit entries"
    # The decision's own entries are present. Later lifecycle steps run under their own
    # correlations and are gathered by identifier, so the trail is not limited to this one.
    assert any(entry["correlation_id"] == correlation for entry in body["audit_trail"])
    # Kinds and correlation are exposed; raw payloads are deliberately not.
    assert all(
        set(entry) == {"event_kind", "correlation_id", "created_at"}
        for entry in body["audit_trail"]
    )


async def test_an_unknown_request_is_not_found(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    response = await client.get(
        f"/api/v1/payment-requests/{uuid4()}/evidence", headers=_headers(seeded_fixture_data)
    )

    assert response.status_code == 404


async def test_a_legacy_request_without_a_catalog_snapshot_still_produces_a_receipt(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Fixture requests predate the catalog path; the receipt must not assume a snapshot."""

    request = await async_session.scalar(
        select(PaymentRequest).where(PaymentRequest.id == seeded_fixture_data.payment_request.id)
    )
    assert request is not None and request.catalog_sku is None

    body = (
        await client.get(
            f"/api/v1/payment-requests/{request.id}/evidence",
            headers=_headers(seeded_fixture_data),
        )
    ).json()

    assert body["proposed"]["sku"] is None
    assert body["derived"]["amount_minor"] == request.amount_minor


async def test_the_receipt_renders_the_three_stages_separately(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    """Keeping the stages apart is what makes the authority boundary legible."""

    request_id = await _create_request(client, seeded_fixture_data)

    response = await client.get(
        f"/api/v1/payment-requests/{request_id}/receipt", headers=_headers(seeded_fixture_data)
    )
    body = response.text

    assert response.status_code == 200
    assert "Chosen by the buying agent" in body
    assert "Determined by TrustGate" in body
    assert "What Razorpay actually did" in body
    # The agent's choices and the server's derivation both appear, in that order.
    assert body.index("Chosen by the buying agent") < body.index("Determined by TrustGate")


async def test_the_receipt_and_the_json_cannot_disagree(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    """Both renderings come from one assembly, so the facts must match."""

    request_id = await _create_request(client, seeded_fixture_data)
    headers = _headers(seeded_fixture_data)

    data = (
        await client.get(f"/api/v1/payment-requests/{request_id}/evidence", headers=headers)
    ).json()
    receipt = (
        await client.get(f"/api/v1/payment-requests/{request_id}/receipt", headers=headers)
    ).text

    assert data["proposed"]["sku"] in receipt
    assert data["decision"]["decision"] in receipt
    assert data["derived"]["merchant_display_name"] in receipt
    assert data["derived"]["order_ref"] in receipt
    # 39900 minor units must appear as currency, not as a raw integer.
    assert "399.00" in receipt


async def test_a_denied_request_receipt_says_nothing_reached_the_provider(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    async_session.add(
        CatalogItem(
            id=uuid4(),
            tenant_id=seeded_fixture_data.tenant_a.id,
            merchant_id=seeded_fixture_data.tenant_a_blocked_merchant.id,
            sku="RECEIPT-BLOCKED",
            name="Blocked Merchant Item",
            description_untrusted="Synthetic item bound to a merchant outside the active policy.",
            price_minor=10_000,
            currency="INR",
            max_quantity=1,
            active=True,
        )
    )
    await async_session.flush()
    request_id = await _create_request(client, seeded_fixture_data, sku="RECEIPT-BLOCKED")

    body = (
        await client.get(
            f"/api/v1/payment-requests/{request_id}/receipt",
            headers=_headers(seeded_fixture_data),
        )
    ).text

    assert "DENY" in body
    assert "MERCHANT_NOT_ALLOWED" in body
    assert "Nothing reached Razorpay" in body


async def test_the_receipt_does_not_overclaim_what_it_is(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    """Language discipline: tamper-evident, never non-repudiable."""

    request_id = await _create_request(client, seeded_fixture_data)

    body = (
        await client.get(
            f"/api/v1/payment-requests/{request_id}/receipt",
            headers=_headers(seeded_fixture_data),
        )
    ).text

    assert "Tamper-evident" in body
    assert "not a signed or legally non-repudiable record" in body


async def test_another_tenant_cannot_read_the_receipt_either(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    request_id = await _create_request(client, seeded_fixture_data)

    response = await client.get(
        f"/api/v1/payment-requests/{request_id}/receipt",
        headers={"X-Tenant-Id": str(seeded_fixture_data.tenant_b.id)},
    )

    assert response.status_code == 404


async def test_the_receipt_does_not_double_escape_its_own_markup(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    """Escaping a heading that already held an entity once produced a visible `&amp;middot;`."""

    request_id = await _create_request(client, seeded_fixture_data)

    body = (
        await client.get(
            f"/api/v1/payment-requests/{request_id}/receipt",
            headers=_headers(seeded_fixture_data),
        )
    ).text

    assert "&amp;middot;" not in body
    assert "&amp;mdash;" not in body
    assert "&amp;#8377;" not in body


async def test_the_trail_follows_the_purchase_past_its_authorization(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Later lifecycle steps run under their own correlation and must still appear.

    Gathering by the decision's correlation alone produced a receipt that stopped at the
    authorization and omitted the authority, provider, and webhook history it exists to evidence.
    """

    request_id = await _create_request(client, seeded_fixture_data)
    authority = await client.post(
        f"/api/v1/checkout-authorities/{request_id}", headers=_headers(seeded_fixture_data)
    )
    assert authority.status_code == 200, authority.text

    body = (
        await client.get(
            f"/api/v1/payment-requests/{request_id}/evidence",
            headers=_headers(seeded_fixture_data),
        )
    ).json()

    kinds = {entry["event_kind"] for entry in body["audit_trail"]}
    correlations = {entry["correlation_id"] for entry in body["audit_trail"]}
    assert "checkout_authority_issued" in kinds, f"trail stopped early: {sorted(kinds)}"
    assert len(correlations) > 1, "the trail covers only one correlation"


async def test_the_trail_does_not_pull_in_another_purchase(
    client: AsyncClient, seeded_fixture_data: FixtureData
) -> None:
    """Widening the gather must not make receipts bleed into each other."""

    first = await _create_request(client, seeded_fixture_data)
    second = await _create_request(client, seeded_fixture_data)
    headers = _headers(seeded_fixture_data)

    body = (await client.get(f"/api/v1/payment-requests/{first}/evidence", headers=headers)).json()

    assert second not in str(body["audit_trail"])
