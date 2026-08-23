"""Deterministic buyer-model substitutes for the safe and adversarial M1 demonstrations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agent.buyer import CatalogItem

_INJECTION_PREFIX = "TRUSTGATE_DEMO_INJECTION:"


class CatalogHeuristicBuyer:
    """Choose the first catalog item whose name or SKU overlaps the buyer's goal."""

    async def propose(self, goal: str, catalog: Sequence[CatalogItem]) -> Mapping[str, object]:
        goal_words = set(goal.casefold().replace("-", " ").split())
        chosen = next(
            (
                item
                for item in catalog
                if goal_words.intersection(
                    set(item.name.casefold().replace("-", " ").split())
                    | set(item.sku.casefold().replace("-", " ").split())
                )
            ),
            catalog[0],
        )
        return {"sku": chosen.sku, "quantity": 1, "purpose": goal}


class InjectedContentFollower:
    """Deterministically models an instruction-following failure for the adversarial harness.

    It is intentionally not presented as an LLM. The marker lets tests and a demo prove that
    untrusted catalog text influenced a proposal while server-side authorization stayed intact.
    """

    async def propose(self, goal: str, catalog: Sequence[CatalogItem]) -> Mapping[str, object]:
        for item in catalog:
            if _INJECTION_PREFIX not in item.description:
                continue
            instruction = item.description.split(_INJECTION_PREFIX, maxsplit=1)[1].strip()
            values = dict(
                part.split("=", maxsplit=1) for part in instruction.split() if "=" in part
            )
            return {
                "sku": values.get("sku", item.sku),
                "quantity": int(values.get("quantity", "1")),
                "purpose": goal,
                "amount_minor": values.get("amount_minor", "0"),
                "merchant_id": values.get("merchant_id", "ignored"),
                "_influenced_by_untrusted_content": True,
            }
        return await CatalogHeuristicBuyer().propose(goal, catalog)
