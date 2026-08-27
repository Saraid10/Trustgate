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
        id="A3",
        title="Currency substitution",
        invariant=(
            "Currency is derived from the catalog item, and the one route that accepts a currency "
            "is disabled by default and denies a mismatch against the active policy when enabled."
        ),
        test_names=(
            "test_a3_the_agent_surface_derives_currency_and_cannot_be_told_one",
            "test_a3_the_only_currency_accepting_route_is_disabled_by_default",
            "test_a3_an_enabled_legacy_route_still_denies_a_currency_outside_the_policy",
        ),
    ),
    Scenario(
        id="A4",
        title="Expired or reused approval",
        invariant=(
            "An approval is a permission with a lifetime and a single use. Neither an expired one "
            "nor an already consumed one can authorize, and a refused approval is not burned."
        ),
        test_names=(
            "test_a4_an_expired_approval_cannot_authorize",
            "test_a4_an_already_consumed_approval_cannot_authorize_again",
        ),
    ),
    Scenario(
        id="A5",
        title="Self-approval",
        invariant=(
            "An approval cannot be granted by the identity that requested the purchase. "
            "Separation of duties is enforced, not merely expected from configuration."
        ),
        test_names=(
            "test_a5_an_approval_cannot_be_granted_by_the_requesting_actor",
            "test_a5_a_separate_approver_can_still_grant",
        ),
    ),
    Scenario(
        id="A6",
        title="Forged webhook signature",
        invariant=(
            "Provider events are authenticated by raw-byte HMAC before the body is parsed. A "
            "forged or absent signature changes nothing, however well-formed the event is."
        ),
        test_names=(
            "test_a6_a_forged_signature_is_refused",
            "test_a6_an_unsigned_event_is_refused",
        ),
    ),
    Scenario(
        id="A7",
        title="Tampered webhook body",
        invariant=(
            "The signature covers the exact bytes received, so a genuinely signed event edited in "
            "flight no longer verifies and never reaches a payment."
        ),
        test_names=("test_a7_a_body_altered_after_signing_no_longer_verifies",),
    ),
    Scenario(
        id="A8",
        title="Duplicate webhook delivery",
        invariant=(
            "Provider event identity is stored, so a replay of an authentic, in-window event is "
            "refused by the database rather than by whichever handler happens to look."
        ),
        test_names=("test_a8_a_replayed_event_does_not_transition_the_payment_twice",),
    ),
    Scenario(
        id="A9",
        title="Out-of-order provider events",
        invariant=(
            "Arrival order is the provider's and legality is ours. A capture cannot precede its "
            "authorization, and a terminal payment accepts no further outcome."
        ),
        test_names=(
            "test_a9_a_capture_cannot_precede_an_authorization",
            "test_a9_a_terminal_payment_accepts_no_further_provider_outcome",
        ),
    ),
    Scenario(
        id="A10",
        title="Double refund",
        invariant=(
            "No surface can initiate a refund at all, asserted against the live route table and "
            "tool list, and the ledger invariant refuses a refund total exceeding the capture."
        ),
        test_names=(
            "test_a10_no_surface_anywhere_can_initiate_a_refund",
            "test_a10_a_refund_total_cannot_exceed_what_was_captured",
        ),
    ),
    Scenario(
        id="A11a",
        title="Unknown tenant header",
        invariant=(
            "A tenant that does not resolve is refused before any route body runs, and the refusal "
            "discloses nothing that would let a caller enumerate which tenants exist."
        ),
        test_names=(
            "test_a11a_an_unknown_tenant_header_is_refused",
            "test_a11a_an_unknown_tenant_is_indistinguishable_from_a_forbidden_one",
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
        id="A12",
        title="Idempotency key collision",
        invariant=(
            "A key reused with a different purchase returns the original decision and a 409. The "
            "second purchase is never created and cannot be mistaken for one that was accepted."
        ),
        test_names=("test_a12_a_reused_key_with_a_different_purchase_returns_the_first_decision",),
    ),
    Scenario(
        id="A13",
        title="Policy drift between authorization and use",
        invariant=(
            "An authority does not outlive the policy it was checked against, nor the purchase it "
            "was issued for. A superseding policy, an expired one, or an edited amount revokes "
            "it without burning it, and an undrifted authority still works."
        ),
        test_names=(
            "test_a13_an_authority_is_valid_until_the_policy_under_it_moves",
            "test_a13_a_policy_published_after_authorization_revokes_the_authority",
            "test_a13_an_amount_edited_after_authorization_breaks_the_snapshot_hash",
            "test_a13_an_expired_policy_cannot_spend_an_authority",
        ),
    ),
    Scenario(
        id="A14",
        title="Stale or post-dated webhook",
        invariant=(
            "A signature proves origin, not recency. An event outside the freshness window, dated "
            "into the future, or carrying no timestamp at all is refused before any lookup."
        ),
        test_names=(
            "test_a14_a_stale_signed_event_is_refused",
            "test_a14_a_post_dated_event_cannot_extend_its_own_validity",
            "test_a14_an_event_with_no_timestamp_is_refused_rather_than_exempted",
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
