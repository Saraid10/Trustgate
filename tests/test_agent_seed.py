from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.seed import seed_demo
from models.domain import CatalogItem, Merchant, PolicyMerchant, SpendingPolicy, Tenant


@pytest.mark.asyncio
async def test_m1_seed_creates_an_isolated_authorized_catalog(async_session: AsyncSession) -> None:
    identity = await seed_demo(async_session)
    tenant = await async_session.scalar(select(Tenant).where(Tenant.id == identity.tenant_id))
    policy = await async_session.scalar(
        select(SpendingPolicy).where(SpendingPolicy.tenant_id == identity.tenant_id)
    )
    merchant = await async_session.scalar(
        select(Merchant).where(Merchant.tenant_id == identity.tenant_id)
    )
    catalog = list(
        await async_session.scalars(
            select(CatalogItem)
            .where(CatalogItem.tenant_id == identity.tenant_id)
            .order_by(CatalogItem.sku)
        )
    )
    policy_merchant = await async_session.scalar(
        select(PolicyMerchant).where(PolicyMerchant.tenant_id == identity.tenant_id)
    )

    assert identity.actor_id == "trustgate-demo-buyer"
    assert tenant is not None
    assert policy is not None
    assert merchant is not None
    assert [item.sku for item in catalog] == ["CLOUD-STARTER", "CLOUD-TEAM"]
    assert catalog[0].price_minor == 39_900
    assert catalog[1].price_minor == 60_000
    assert "TRUSTGATE_DEMO_INJECTION:" in catalog[1].description_untrusted
    assert policy_merchant is not None
    assert policy_merchant.policy_id == policy.id
    assert policy_merchant.merchant_id == merchant.id
