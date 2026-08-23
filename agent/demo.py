"""Run the constrained local buyer agent against the configured TrustGate MCP server."""

from __future__ import annotations

import argparse
import json

from agent.buyer import BuyerAgent, BuyerRun, InProcessMcpTools
from agent.models import CatalogHeuristicBuyer, InjectedContentFollower
from agent.runtime import run_async
from mcp_server.server import create_mcp_server


def _as_json(run: BuyerRun) -> dict[str, object]:
    return {
        "goal": run.goal,
        "proposal": run.proposal.model_dump(),
        "tool_result": run.tool_result,
        "influenced_by_untrusted_content": run.influenced_by_untrusted_content,
        "uninfluenced_baseline": (
            run.uninfluenced_baseline.model_dump()
            if run.uninfluenced_baseline is not None
            else None
        ),
        "discarded_model_fields": list(run.discarded_model_fields),
        "trace": [{"event": event.event, "detail": event.detail} for event in run.trace],
    }


def _select_model(*, adversarial: bool, live: bool) -> object:
    """Choose the buyer model.

    The deterministic substitutes keep the demo runnable with no API key and no network. The live
    model is what actually demonstrates that untrusted catalog text can sway a real reasoning
    system, which is the premise the authorization layer exists to contain.
    """

    if live:
        from agent.llm import ClaudeBuyer, InfluenceMeasuringBuyer

        return InfluenceMeasuringBuyer(ClaudeBuyer())
    return InjectedContentFollower() if adversarial else CatalogHeuristicBuyer()


async def _run(goal: str, *, adversarial: bool, live: bool) -> BuyerRun:
    server = create_mcp_server()
    model = _select_model(adversarial=adversarial, live=live)
    agent = BuyerAgent(model=model, tools=InProcessMcpTools(server))  # type: ignore[arg-type]
    return await agent.run(goal)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Propose one catalog purchase through TrustGate MCP."
    )
    parser.add_argument("goal", help="The buyer's natural-language purchase goal.")
    parser.add_argument(
        "--adversarial",
        action="store_true",
        help="Run the deterministic poisoned-catalog harness instead of the normal buyer.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Use a real language model as the buyer. Requires the optional 'agent' extra and "
            "ANTHROPIC_API_KEY. The catalog is sent twice, with and without third-party "
            "descriptions, so untrusted influence is measured rather than self-reported."
        ),
    )
    args = parser.parse_args()
    print(
        json.dumps(
            _as_json(run_async(_run(args.goal, adversarial=args.adversarial, live=args.live))),
            default=str,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
