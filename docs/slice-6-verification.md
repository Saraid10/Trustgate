# Slice 6 Verification

## Scope

Implemented the local FastMCP stdio interface in `mcp_server/server.py`.

## Surface Cross-Check

- Exactly four tools are registered: `create_payment_request`, `evaluate_payment_policy`,
  `request_user_approval`, and `get_payment_status`.
- `authorize_payment`, `capture_payment`, and `call_provider` are absent.
- `MCP_TENANT_ID` is resolved from process configuration and checked against the tenant table;
  no tool schema contains tenant identity, provider secret, signature, or signing-key fields.
- The payment-status lookup includes both `payment_id` and configured `tenant_id` in its query.
  A cross-tenant lookup returns a non-disclosing denial and writes one tenant-scoped audit event.
- Request creation returns the existing decision data plus its tenant-scoped `payment_id`, allowing
  the approved status tool to be used without adding a broader lookup capability.

## Host Verification

```text
ruff check .                                                        PASS
mypy api models schemas state_machine policy_engine mock_provider mcp_server PASS
pytest tests/test_mcp_interface.py -v                              PASS (7 tests)
pytest -q                                                          PASS (65 project tests)
```

The FastMCP package emits an upstream `IncompleteFieldDefinitionWarning` during import. It does
not affect registration or invocation; all tool discovery and calls passed through FastMCP's
own `list_tools()` and `call_tool()` interfaces.

## Docker Verification

```text
docker compose exec -T api python -m ruff check .                         PASS
docker compose exec -T api python -m mypy api models schemas state_machine policy_engine mock_provider mcp_server PASS
docker compose exec -T api python -m pytest tests/test_mcp_interface.py -q PASS (7 tests)
GET http://localhost:8000/health                                         200 {"status":"ok"}
```

Docker emits the same upstream FastMCP warning plus a non-functional pytest-cache permission
warning; neither affects tool registration or test behavior.
