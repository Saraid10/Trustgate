"""The two catalogs the demo shows must actually be the same catalog.

The demo's claim is that one poisoned input produces two different outcomes because the interfaces
differ. Two hand-written catalogs cannot support that, and the first pair drifted immediately: one
injected `sku=CLOUD-STARTER quantity=1`, the other `sku=CLOUD-TEAM quantity=50`, their CLOUD-TEAM
prices differed by 900 rupees, and the README asserted they matched throughout.

Both are now built from `agent.demo_catalog`. These tests fail if anyone restates a value locally.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent import demo_catalog as facts
from agent.stage import DEMO_TENANT_ID, stage_demo
from demo.unguarded import DEMO_CATALOG
from models.domain import CatalogItem


async def test_the_seeded_catalog_carries_the_same_injection_as_the_baseline(
    async_session: AsyncSession,
) -> None:
    """The sentence the demo rests on, asserted against both renderings.

    Not a comparison of the two constants - that would only prove the module is consistent with
    itself. This reads the row the database actually holds and the object the baseline actually
    runs against.
    """

    await stage_demo(async_session)

    seeded = await async_session.scalar(
        select(CatalogItem).where(
            CatalogItem.tenant_id == DEMO_TENANT_ID, CatalogItem.sku == facts.TEAM_SKU
        )
    )
    baseline = next(item for item in DEMO_CATALOG if item.sku == facts.TEAM_SKU)

    assert seeded is not None
    assert seeded.description_untrusted == baseline.description
    assert facts.INJECTED_INSTRUCTION in seeded.description_untrusted


async def test_both_catalogs_price_and_bound_every_item_identically(
    async_session: AsyncSession,
) -> None:
    """A price that differs between the two would make the overcharge figure a fiction."""

    await stage_demo(async_session)

    seeded = {
        item.sku: item
        for item in (
            await async_session.scalars(
                select(CatalogItem).where(CatalogItem.tenant_id == DEMO_TENANT_ID)
            )
        ).all()
    }

    assert set(seeded) == {item.sku for item in DEMO_CATALOG}
    for item in DEMO_CATALOG:
        row = seeded[item.sku]
        assert row.price_minor == item.price_minor, f"{item.sku} price differs between the demos"
        assert row.max_quantity == item.max_quantity, f"{item.sku} bound differs between the demos"
        assert row.currency == item.currency


def test_the_injection_survives_the_discard_as_a_quantity() -> None:
    """Why this particular instruction, and not a simpler one.

    Every field earns its place. The unguarded adapter reads `amount_minor` and `merchant_id` and
    pays them. TrustGate has nowhere to put either, so what survives is `quantity`, and the server
    bounds that against the catalog maximum. An injection that only set an amount would be
    neutralised into an ordinary purchase - correct, and invisible on camera.
    """

    assert facts.INJECTED_QUANTITY > facts.TEAM_MAX_QUANTITY
    assert f"quantity={facts.INJECTED_QUANTITY}" in facts.INJECTED_INSTRUCTION
    assert f"amount_minor={facts.INJECTED_AMOUNT_MINOR}" in facts.INJECTED_INSTRUCTION
    assert f"merchant_id={facts.INJECTED_MERCHANT_ID}" in facts.INJECTED_INSTRUCTION


def test_the_shared_facts_module_imports_nothing() -> None:
    """It is shared by a module forbidden from importing a database, so it stays import-free."""

    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parent.parent / "agent" / "demo_catalog.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        and not (isinstance(node, ast.ImportFrom) and node.module == "__future__")
    ]

    assert not imports, "demo_catalog gained an import and can no longer be shared safely"
