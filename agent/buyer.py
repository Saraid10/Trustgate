"""A deliberately narrow agent that can propose, never authorize, a catalog purchase."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field


class CatalogItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str
    name: str
    merchant_display_name: str
    description: str
    price_minor: int
    currency: str
    max_quantity: int


class PurchaseProposal(BaseModel):
    """The complete set of purchase facts that an agent is permitted to propose."""

    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=1, max_length=64)
    quantity: int = Field(gt=0)
    purpose: str = Field(min_length=1, max_length=255)


class BuyerModel(Protocol):
    async def propose(self, goal: str, catalog: Sequence[CatalogItem]) -> Mapping[str, object]: ...


class BuyerTools(Protocol):
    async def list_catalog(self) -> list[dict[str, object]]: ...

    async def create_catalog_purchase(self, proposal: PurchaseProposal) -> dict[str, object]: ...


@dataclass(frozen=True)
class AgentTrace:
    event: str
    detail: dict[str, object]


@dataclass(frozen=True)
class BuyerRun:
    goal: str
    proposal: PurchaseProposal
    tool_result: dict[str, object]
    influenced_by_untrusted_content: bool
    uninfluenced_baseline: PurchaseProposal | None
    discarded_model_fields: tuple[str, ...]
    trace: tuple[AgentTrace, ...]


class BuyerAgent:
    """Coordinate one untrusted proposal through the fixed TrustGate MCP contract."""

    def __init__(self, *, model: BuyerModel, tools: BuyerTools) -> None:
        self._model = model
        self._tools = tools

    async def run(self, goal: str) -> BuyerRun:
        catalog = [CatalogItem.model_validate(item) for item in await self._tools.list_catalog()]
        raw_proposal = dict(await self._model.propose(goal, catalog))
        allowed_fields = set(PurchaseProposal.model_fields)
        # Underscore-prefixed keys are harness metadata, not fields the model tried to supply.
        # Excluding them keeps `discarded_model_fields` an honest record of contract violations.
        proposed_fields = {field for field in raw_proposal if not field.startswith("_")}
        discarded_fields = tuple(sorted(proposed_fields - allowed_fields))
        proposal = PurchaseProposal.model_validate(
            {field: raw_proposal[field] for field in allowed_fields if field in raw_proposal}
        )
        baseline_value = raw_proposal.get("_uninfluenced_baseline")
        baseline = _proposal_metadata(baseline_value, allowed_fields)
        result = await self._tools.create_catalog_purchase(proposal)
        influenced = (
            _is_influenced_by_baseline(
                proposal=proposal,
                baseline=baseline,
                baseline_value=baseline_value,
                discarded_fields=discarded_fields,
            )
            if baseline is not None
            else bool(raw_proposal.get("_influenced_by_untrusted_content", False))
        )
        return BuyerRun(
            goal=goal,
            proposal=proposal,
            tool_result=result,
            influenced_by_untrusted_content=influenced,
            uninfluenced_baseline=baseline,
            discarded_model_fields=discarded_fields,
            trace=(
                AgentTrace("catalog_listed", {"item_count": len(catalog)}),
                AgentTrace(
                    "purchase_proposed",
                    {
                        "sku": proposal.sku,
                        "quantity": proposal.quantity,
                        "purpose": proposal.purpose,
                        "discarded_model_fields": list(discarded_fields),
                    },
                ),
                AgentTrace("trustgate_decision_received", dict(result)),
            ),
        )


def _proposal_metadata(value: object, allowed_fields: set[str]) -> PurchaseProposal | None:
    """Parse optional model metadata only for display; it never affects the payment request."""

    if not isinstance(value, Mapping):
        return None
    try:
        return PurchaseProposal.model_validate(
            {field: value[field] for field in allowed_fields if field in value}
        )
    except ValueError:
        return None


def _is_influenced_by_baseline(
    *,
    proposal: PurchaseProposal,
    baseline: PurchaseProposal,
    baseline_value: object,
    discarded_fields: tuple[str, ...],
) -> bool:
    """Compare the discrete purchase choice and newly attempted authority fields."""

    if (baseline.sku, baseline.quantity) != (proposal.sku, proposal.quantity):
        return True
    if not isinstance(baseline_value, Mapping):
        return False
    baseline_fields = {field for field in baseline_value if not field.startswith("_")}
    return bool(set(discarded_fields) - baseline_fields)


class InProcessMcpTools:
    """Expose only agent-permitted operations from an in-process FastMCP server."""

    def __init__(self, server: FastMCP) -> None:
        self._server = server

    async def _call(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        result = await self._server.call_tool(name, arguments)
        if isinstance(result, tuple):
            _, result = result
        if not isinstance(result, dict):
            raise RuntimeError(f"MCP tool {name} returned an unexpected result.")
        return result

    async def list_catalog(self) -> list[dict[str, object]]:
        result = await self._call("list_catalog", {})
        items = result.get("items")
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise RuntimeError("MCP catalog response is invalid.")
        return items

    async def create_catalog_purchase(self, proposal: PurchaseProposal) -> dict[str, object]:
        return await self._call(
            "create_payment_request",
            {
                "sku": proposal.sku,
                "quantity": proposal.quantity,
                "purpose": proposal.purpose,
                "idempotency_key": str(uuid4()),
            },
        )
