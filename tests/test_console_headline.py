"""The verdict, said once and said large, in words a reviewer can act on.

The timeline is comparative and shallow by design - three attempts side by side, so the difference
between them is visible. What it was bad at is the question someone watching actually asks first:
where does the newest thing stand, and can money move. Answering that meant reading the top row of
a five-column table and knowing that `QUANTITY_EXCEEDS_LIMIT` is a refusal rather than a field name.

Two things are asserted here. That the banner says what the record says - it is built from the same
evidence the receipt renders, and from `entries[0]` of the list the table below it draws, so it
describes the top row by construction rather than by coincidence. And that reason codes arrive as
sentences, because a code nobody can read is a refusal nobody can check.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from html import escape
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fixtures import FixtureData
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from api.app import app
from api.dependencies import get_session
from api.reason_text import humanise
from delegation.chain import Bounds, grant_root, revoke
from models.domain import AuditEvent, Delegation, PaymentRequest

ACTOR = "headline-actor"
PRINCIPAL = "headline-finance-lead"
TOKEN = "headline-approver-token"  # noqa: S105 - synthetic


@pytest_asyncio.fixture
async def client(
    async_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("TRUSTGATE_API_ACTOR_ID", ACTOR)
    monkeypatch.setenv("DEMO_APPROVER_TOKEN", TOKEN)
    monkeypatch.setenv("DEMO_APPROVER_ID", "headline-human")
    monkeypatch.setenv("ENABLE_CONSOLE", "true")

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield async_session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


def _headers(data: FixtureData) -> dict[str, str]:
    return {"X-Tenant-Id": str(data.tenant_a.id)}


async def _newest_buy(
    client: AsyncClient,
    session: AsyncSession,
    data: FixtureData,
    *,
    sku: str = "CLOUD-STARTER",
) -> str:
    """Make one purchase and put it unambiguously at the top of the timeline.

    Postgres `now()` is transaction-scoped and this suite runs inside one transaction, so every row
    a test creates carries the *same* `created_at` as the seeded fixture request - and "newest"
    becomes whichever one the database happened to return first. That is a test artifact, not a
    product one: in life each request is its own transaction with its own reading of the clock.

    The banner is correct either way, because it is assembled from `entries[0]` of the list the
    table renders - so it always describes the row shown at the top, tie or no tie. What is not safe
    is a test quietly assuming its own purchase won that tie, which is what this replaces.
    """

    response = await client.post(
        "/api/v1/catalog-payment-requests",
        json={
            "sku": sku,
            "quantity": 1,
            "purpose": "Provision an isolated build environment.",
            "idempotency_key": str(uuid4()),
        },
        headers=_headers(data),
    )
    assert response.status_code == 201, response.text
    request_id = str(response.json()["payment_request_id"])
    await session.execute(
        update(PaymentRequest)
        .where(PaymentRequest.id == UUID(request_id))
        .values(created_at=datetime.now(UTC) + timedelta(minutes=5))
    )
    await session.flush()
    return request_id


async def _root(
    session: AsyncSession, data: FixtureData, *, budget: int = 200_000, cap: int = 100_000
) -> Delegation:
    return await grant_root(
        session,
        tenant_id=data.tenant_a.id,
        policy=data.tenant_a_policy,
        principal_actor_id=PRINCIPAL,
        delegate_actor_id=ACTOR,
        bounds=Bounds(
            budget_minor=budget,
            max_amount_minor=cap,
            allowed_skus=("CLOUD-STARTER",),
            purpose="headline",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        ),
    )


async def _console(client: AsyncClient, data: FixtureData) -> str:
    page = await client.get(f"/console/{data.tenant_a.id}")
    assert page.status_code == 200, page.text
    return page.text


# --- the three verdicts -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_authorized_purchase_that_cannot_pay_yet_says_both_things(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The whole design, in one panel: authorized, and not able to move money.

    These read as the same outcome unless the banner says them separately, and saying them
    separately is the point - "AUTHORIZED" on its own suggests the payment is about to happen.
    """

    await _newest_buy(client, async_session, seeded_fixture_data)

    page = await _console(client, seeded_fixture_data)

    assert "AUTHORIZED" in page
    assert "Order creation allowed: No" in page
    assert humanise("NO_CHECKOUT_AUTHORITY_ISSUED") in page


@pytest.mark.asyncio
async def test_issuing_the_authority_flips_the_provider_line(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    request_id = await _newest_buy(client, async_session, seeded_fixture_data)
    issued = await client.post(
        f"/api/v1/checkout-authorities/{request_id}", headers=_headers(seeded_fixture_data)
    )
    assert issued.status_code == 200, issued.text

    page = await _console(client, seeded_fixture_data)

    assert "Order creation allowed: Yes" in page


@pytest.mark.asyncio
async def test_a_purchase_waiting_on_a_human_says_approval_required(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    await _newest_buy(client, async_session, seeded_fixture_data, sku="CLOUD-TEAM")

    page = await _console(client, seeded_fixture_data)

    assert "APPROVAL REQUIRED" in page
    assert humanise("APPROVAL_REQUIRED") in page


@pytest.mark.asyncio
async def test_a_refused_purchase_says_blocked_in_words(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """`DELEGATION_BUDGET_EXHAUSTED` is exact and unreadable. The banner owes a reader the words."""

    # A budget too small for the purchase, but a per-payment cap large enough to clear -
    # so the refusal is the exhausted budget rather than the hop limit, which is the one
    # a viewer needs to be able to read.
    await _root(async_session, seeded_fixture_data, budget=1_000, cap=100_000)

    response = await client.post(
        "/api/v1/catalog-payment-requests",
        json={
            "sku": "CLOUD-STARTER",
            "quantity": 1,
            "purpose": "Provision an isolated build environment.",
            "idempotency_key": str(uuid4()),
        },
        headers=_headers(seeded_fixture_data),
    )
    await async_session.execute(
        update(PaymentRequest)
        .where(PaymentRequest.id == UUID(str(response.json()["payment_request_id"])))
        .values(created_at=datetime.now(UTC) + timedelta(minutes=5))
    )
    await async_session.flush()

    page = await _console(client, seeded_fixture_data)

    assert "REFUSED" in page
    assert "Order creation allowed: No" in page
    # Escaped, because the page is HTML and the sentence has an apostrophe in it. Asserting
    # the raw string would pass only for reasons that happen to contain no punctuation.
    assert escape(humanise("DELEGATION_BUDGET_EXHAUSTED")) in page


# --- the attack, which is the one with no record to read ----------------------------------------


@pytest.mark.asyncio
async def test_an_attack_refused_at_the_boundary_says_there_is_nothing_to_write_about(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The strongest thing the system does, and the easiest to render as an empty panel.

    A refusal that happened before a payment request existed has no evidence record to read. The
    banner has to say that absence out loud - blanks read like a page that has not finished
    loading, which is the opposite of the claim.
    """

    async_session.add(
        AuditEvent(
            tenant_id=seeded_fixture_data.tenant_a.id,
            correlation_id=uuid4(),
            event_kind="catalog_purchase_rejected",
            created_at=datetime.now(UTC) + timedelta(minutes=5),
            payload={
                "reason": "QUANTITY_EXCEEDS_LIMIT",
                "sku": "CLOUD-STARTER",
                "requested_quantity": 50,
                "actor_id": ACTOR,
            },
        )
    )
    await async_session.flush()

    page = await _console(client, seeded_fixture_data)

    assert "REFUSED" in page
    assert humanise("QUANTITY_EXCEEDS_LIMIT") in page
    assert "nothing to write a receipt about" in page


# --- delegated authority, on the banner ----------------------------------------------------------


@pytest.mark.asyncio
async def test_the_banner_names_the_human_and_what_is_left_on_the_chain(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    await _root(async_session, seeded_fixture_data)
    await _newest_buy(client, async_session, seeded_fixture_data)

    page = await _console(client, seeded_fixture_data)

    assert f"<strong>{PRINCIPAL}</strong>" in page
    assert "left on the chain" in page


@pytest.mark.asyncio
async def test_revoking_the_chain_shows_up_on_the_banner_as_a_refused_provider_action(
    client: AsyncClient, async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """The consume-time window, on screen, in words."""

    held = await _root(async_session, seeded_fixture_data)
    request_id = await _newest_buy(client, async_session, seeded_fixture_data)
    issued = await client.post(
        f"/api/v1/checkout-authorities/{request_id}", headers=_headers(seeded_fixture_data)
    )
    assert issued.status_code == 200, issued.text
    await revoke(async_session, tenant_id=seeded_fixture_data.tenant_a.id, delegation_id=held.id)

    page = await _console(client, seeded_fixture_data)

    assert "Order creation allowed: No" in page
    assert humanise("DELEGATION_REVOKED") in page


@pytest.mark.asyncio
async def test_a_console_with_no_attempts_renders_no_banner(
    async_session: AsyncSession, seeded_fixture_data: FixtureData
) -> None:
    """Nothing has happened yet is not a verdict, and inventing one would be a lie.

    Asked of the assembler directly, because the seeded fixture guarantees at least one attempt
    exists - an actually-empty tenant is not a state the HTTP path can be put into here.
    """

    from api.routes.console import _headline

    assert await _headline(async_session, seeded_fixture_data.tenant_a, []) is None


# --- the translation itself ----------------------------------------------------------------------


def test_every_reason_reads_as_a_sentence_even_when_nobody_wrote_one() -> None:
    """The fallback matters more than the table, because the table goes stale and it does not."""

    assert humanise("SOME_REFUSAL_NOBODY_TRANSLATED") == "Some refusal nobody translated"
    assert humanise("") == "Refused for a reason that was not recorded"
    assert humanise("DELEGATION_REVOKED") == "The authority behind this payment was withdrawn"


def test_no_translation_is_blank_or_still_a_code() -> None:
    """A table entry that shipped empty would render as a refusal with no reason at all."""

    from api.reason_text import _PLAIN

    for code, text in _PLAIN.items():
        assert text.strip(), f"{code} translates to nothing"
        assert text != code, f"{code} translates to itself"
        assert "_" not in text, f"{code} still reads like a code: {text!r}"


def test_a_completed_purchase_is_not_dressed_as_a_refusal() -> None:
    """`Order creation allowed: No` means two opposite things, and the panel showed only one.

    Every other blocked reason withholds authority: none was issued, it expired, it was used, the
    chain was revoked. Those are refusals, and refusal red is right for them.

    A captured payment reaches the same line for the opposite reason - it already paid. Rendering
    that in red beside a row reading CAPTURED told a viewer something had gone wrong on the one
    beat where everything went right. Caught on camera during a rehearsal, not by a test.
    """

    from api.console_view import ConsoleHeadline, _headline_panel

    def panel(code: str) -> str:
        return _headline_panel(
            ConsoleHeadline(
                verdict="AUTHORIZED",
                tone="ok",
                reasons=("Within every limit in the current policy",),
                provider_action_allowed=False,
                provider_action_blocked_reason=humanise(code),
                delegation_root_actor_id=None,
                delegation_remaining_minor=None,
                currency="INR",
                has_payment_request=True,
                provider_action_blocked_code=code,
            )
        )

    settled = panel("PAYMENT_ALREADY_SETTLED")
    assert "class='done'" in settled, "a captured payment still renders as a refusal"
    assert "class='no'" not in settled
    assert "No longer needed" in settled

    # The beat the whole demo turns on. This one must keep saying No, in red.
    withheld = panel("NO_CHECKOUT_AUTHORITY_ISSUED")
    assert "class='no'" in withheld, "authorized-but-cannot-pay stopped reading as a refusal"
    assert "Order creation allowed: No<" in withheld

    # An unknown or absent code falls back to the refusal rendering, which is the safe direction.
    assert "class='no'" in panel("SOMETHING_NOBODY_HAS_WRITTEN_YET")
