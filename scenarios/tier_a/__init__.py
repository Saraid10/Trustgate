"""The Tier A adversarial registry.

Each entry names the test that proves it. `scenarios.report` renders the published attack matrix
from this registry, and a test asserts every named test exists and that the README matches. The
matrix therefore cannot claim an attack that is not actually covered by a passing test.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    invariant: str
    test_names: tuple[str, ...]


REGISTRY: tuple[Scenario, ...] = (
    Scenario(
        id="A1",
        title="Amount tampering",
        invariant=(
            "The amount is derived from the catalog item's price and a server-bounded quantity. "
            "No agent-supplied value can change it."
        ),
        test_names=(
            "test_a1_supplied_amount_field_is_refused_at_the_boundary",
            "test_a1_mcp_surface_has_no_amount_parameter",
            "test_a1_quantity_cannot_be_used_to_escalate_the_amount",
        ),
    ),
    Scenario(
        id="A2",
        title="Merchant substitution",
        invariant=(
            "The merchant is derived from the tenant-scoped catalog item. A merchant outside the "
            "tenant is unreachable, and one outside the active policy cannot be paid."
        ),
        test_names=(
            "test_a2_another_tenants_sku_is_not_reachable",
            "test_a2_policy_disallowed_merchant_cannot_be_paid",
        ),
    ),
    Scenario(
        id="A11b",
        title="Cross-tenant object access",
        invariant=(
            "Every tenant-scoped lookup filters by the trusted tenant. A known tenant cannot read "
            "or act on another tenant's request, payment, or authority on any surface."
        ),
        test_names=(
            "test_a11b_checkout_authority_route_refuses_another_tenants_request",
            "test_a11b_razorpay_route_refuses_another_tenants_authority",
            "test_a11b_mcp_refuses_another_tenants_payment",
        ),
    ),
    Scenario(
        id="A15",
        title="Unauthorized capture via MCP",
        invariant=(
            "No tool reachable by the agent can authorize, capture, refund, or call a provider. "
            "Proven by exercising every exposed tool, not by inspecting tool names."
        ),
        test_names=(
            "test_a15_every_exposed_mcp_tool_grants_no_payment_authority",
            "test_a15_mcp_exposes_no_provider_or_authorization_tool",
        ),
    ),
)


def scenario_ids() -> tuple[str, ...]:
    return tuple(scenario.id for scenario in REGISTRY)
