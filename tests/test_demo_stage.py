"""Staging has to be repeatable, or the demo is different every take."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.stage import (
    APPROVAL_THRESHOLD_MINOR,
    DEMO_TENANT_ID,
    MAX_PAYMENT_MINOR,
    STARTER_PRICE_MINOR,
    TEAM_PRICE_MINOR,
    reset_demo_tenant,
    stage_demo,
)
from models.domain import (
    AuditEvent,
    CatalogItem,
    PaymentRequest,
    SpendingPolicy,
    Tenant,
)


async def test_staging_twice_leaves_one_tenant_not_two(async_session: AsyncSession) -> None:
    """The reason this exists: a second take must not film the first one's leftovers."""

    await stage_demo(async_session)
    await stage_demo(async_session)

    tenants = await async_session.scalar(
        select(func.count()).select_from(Tenant).where(Tenant.id == DEMO_TENANT_ID)
    )
    catalog = await async_session.scalar(
        select(func.count()).select_from(CatalogItem).where(CatalogItem.tenant_id == DEMO_TENANT_ID)
    )

    assert tenants == 1
    assert catalog == 2


async def test_restaging_clears_what_a_previous_run_recorded(async_session: AsyncSession) -> None:
    """A timeline carried over from the last take is the failure this prevents."""

    stage = await stage_demo(async_session)
    leftover = PaymentRequest(
        id=uuid4(),
        tenant_id=stage.tenant_id,
        actor_id=stage.actor_id,
        merchant_id=(
            await async_session.scalar(
                select(CatalogItem.merchant_id).where(CatalogItem.tenant_id == stage.tenant_id)
            )
        ),
        amount_minor=39_900,
        currency="INR",
        order_ref=f"order-{uuid4()}",
        idempotency_key=str(uuid4()),
    )
    async_session.add(leftover)
    await async_session.flush()
    async_session.add(
        AuditEvent(
            tenant_id=stage.tenant_id,
            payment_request_id=leftover.id,
            correlation_id=uuid4(),
            event_kind="catalog_purchase_rejected",
            payload={"reason": "LEFTOVER_FROM_LAST_TAKE"},
        )
    )
    await async_session.flush()

    await stage_demo(async_session)

    requests = await async_session.scalar(
        select(func.count())
        .select_from(PaymentRequest)
        .where(PaymentRequest.tenant_id == DEMO_TENANT_ID)
    )
    events = await async_session.scalar(
        select(func.count()).select_from(AuditEvent).where(AuditEvent.tenant_id == DEMO_TENANT_ID)
    )

    assert requests == 0
    assert events == 0


async def test_the_reset_touches_only_the_demo_tenant(async_session: AsyncSession) -> None:
    """Scoped deletes, so staging a demo on a shared database destroys nothing else.

    A truncate would have been simpler and would have taken every other tenant with it, including
    whatever the person running it was in the middle of.
    """

    other = Tenant(id=uuid4(), name=f"bystander-{uuid4()}")
    async_session.add(other)
    await async_session.flush()

    await stage_demo(async_session)
    await reset_demo_tenant(async_session)

    survivor = await async_session.scalar(select(Tenant).where(Tenant.id == other.id))

    assert survivor is not None


async def test_the_policy_separates_the_three_flows(async_session: AsyncSession) -> None:
    """The demo's whole shape depends on these three numbers standing in this order.

    Below the threshold allows, above it requires approval, and past the per-payment limit denies.
    If the catalog prices ever drift across the thresholds the flows silently collapse into two,
    which would be discovered on camera rather than here.
    """

    await stage_demo(async_session)

    policy = await async_session.scalar(
        select(SpendingPolicy).where(SpendingPolicy.tenant_id == DEMO_TENANT_ID)
    )

    assert policy is not None
    assert STARTER_PRICE_MINOR < APPROVAL_THRESHOLD_MINOR, "the safe flow would need approval"
    assert APPROVAL_THRESHOLD_MINOR < TEAM_PRICE_MINOR, "the approval flow would not need approval"
    assert TEAM_PRICE_MINOR <= MAX_PAYMENT_MINOR, "the approval flow would be denied outright"
    assert policy.approval_required_above_minor == APPROVAL_THRESHOLD_MINOR
    assert policy.max_amount_minor == MAX_PAYMENT_MINOR


async def test_the_injected_amount_is_refused_by_the_per_payment_limit(
    async_session: AsyncSession,
) -> None:
    """The attack has to exceed a limit, or the third flow proves nothing."""

    await stage_demo(async_session)

    poisoned = await async_session.scalar(
        select(CatalogItem).where(
            CatalogItem.tenant_id == DEMO_TENANT_ID, CatalogItem.sku == "CLOUD-TEAM"
        )
    )

    assert poisoned is not None
    assert "TRUSTGATE_DEMO_INJECTION:" in poisoned.description_untrusted
    assert "amount_minor=2000000" in poisoned.description_untrusted
    assert 2_000_000 > MAX_PAYMENT_MINOR


async def test_the_stage_reports_a_console_url_for_the_tenant_it_created(
    async_session: AsyncSession,
) -> None:
    stage = await stage_demo(async_session, base_url="http://127.0.0.1:8000/")

    assert stage.console_url == f"http://127.0.0.1:8000/console/{DEMO_TENANT_ID}"
    assert stage.tenant_id == DEMO_TENANT_ID


def test_the_printed_exports_match_the_shell_that_will_run_them() -> None:
    """Printing `export` on Windows is an instruction that silently does nothing.

    The failure does not appear at the prompt. It appears later, as an agent acting for the wrong
    tenant, on camera, with no obvious cause.
    """

    import os
    from unittest.mock import patch

    from agent.stage import DemoStage, _export

    stage = DemoStage(
        tenant_id=DEMO_TENANT_ID,
        actor_id="a-buyer",
        approver_id="a-human",
        console_url="http://127.0.0.1:8000/console/x",
    )

    with patch.object(os, "name", "nt"):
        windows = _export(stage)
    with patch.object(os, "name", "posix"):
        posix = _export(stage)

    assert "$env:MCP_TENANT_ID=" in windows
    assert "export " not in windows
    assert "export MCP_TENANT_ID=" in posix
    assert "$env:" not in posix
    for shell in (windows, posix):
        assert str(DEMO_TENANT_ID) in shell
        assert "a-buyer" in shell
