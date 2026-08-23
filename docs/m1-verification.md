# M1 Buyer Agent and Adversarial Harness Verification

Date: 2026-08-24

## Invariant

The buyer agent can propose only a catalog SKU, quantity, and purpose. TrustGate derives every
money-critical fact and retains authorization, approval, and provider authority.

## Implemented Surface

- `agent/BuyerAgent` accepts a natural-language goal, lists the configured tenant catalog, validates
  a narrow proposal, and creates a catalog-backed payment request through the existing MCP service.
- `agent/InProcessMcpTools` exposes the agent to only `list_catalog` and
  `create_payment_request`; it does not surface the MCP server's status or approval-request tools,
  and no provider operation is available.
- `agent/seed.py` creates an isolated synthetic tenant, policy, merchant, catalog, and policy
  binding for repeatable safe, approval-required, and hostile runs.
- `python -m agent.demo` runs the normal buyer; its `--adversarial` switch selects the explicit
  poisoned-catalog harness for the hostile flow.
- `python -m agent.demo --live` is an optional Anthropic-backed buyer path. It is not used in
  tests, and it produces an actual proposal plus an independently generated, description-free
  baseline for the demo record.
- The two command-line entry points select the compatible Windows event loop for Psycopg's async
  driver, while retaining the platform default elsewhere.
- The adversarial model is deterministic by design. It follows an explicit marker in untrusted
  catalog text, so the safety proof does not depend on a live model provider behaving a particular
  way.
- `--live` optionally sends the catalog to a real model twice at deterministic sampling settings:
  once without third-party descriptions and once with them. Its JSON output includes the validated
  description-free baseline beside the actual proposal, so influence is observable rather than
  self-reported. This metadata is display-only and never reaches the payment-request tool.

## Scenario Record

| Flow | Agent proposal | TrustGate outcome | Proven property |
|---|---|---|---|
| Safe purchase | `CLOUD-STARTER`, quantity 1 | `ALLOW`; INR 39,900 request | Catalog derives merchant, amount, and currency |
| High-value purchase | `CLOUD-TEAM`, quantity 1 | `REQUIRE_APPROVAL`; `APPROVAL_REQUIRED` | Policy retains approval authority |
| Poisoned catalog | Injected `CLOUD-TEAM`, quantity 50 plus forged amount and merchant | `QUANTITY_EXCEEDS_LIMIT`; no request, payment, or provider order | Injection can influence the proposal but cannot create a payment artifact |

## Verification Performed

- `tests/test_agent.py` and `tests/test_agent_seed.py`: four focused M1 tests passed.
- `tests/test_agent.py tests/test_agent_seed.py tests/test_agent_llm.py tests/test_mcp_interface.py`:
  eighteen focused agent/MCP tests passed, including six fake-client live-adapter tests. No test
  invokes a model provider.
- `ruff check .`: passed.
- `mypy --strict`: passed across 32 source files.
- Full regression suite: 101 passed.
- A real locally seeded command-line run verified all three scenario outcomes in the table above.

## Pending Live Evidence

The live-model command has not been run from this environment because no Anthropic API key was
provided. Before a recorded demo, run it only against the synthetic seeded catalog. Preserve the
JSON output whether the model is influenced or resists the hostile description; both are honest
results, while only the former demonstrates the full adversarial narrative.

## Deliberate Limitation

M1 does not claim that its deterministic harness is a production LLM. It verifies the more
important boundary: replacing the buyer with a model that follows hostile text cannot grant that
model authority over payment facts or provider execution.
