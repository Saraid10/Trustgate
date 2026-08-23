# Threat Model

## Scope

This project is a local payments-adjacent safety testbed. It models payment requests, policies, approvals, provider events, audit trails, MCP tool exposure, and adversarial scenarios without touching real money, real credentials, or real PII.

## In Scope

- Amount, merchant, and currency tampering.
- Cross-tenant object access.
- Idempotency replay and payload collision.
- Approval replay, expiry, and policy-version mismatch.
- Forged, stale, duplicate, tampered, and out-of-order provider webhooks.
- MCP tool-surface abuse, including attempts to call absent dangerous tools.
- A bounded Branded Whisper-style reasoning-layer reproduction with honest metrics.

## Out Of Scope For MVP

- Real provider integrations.
- Production authentication.
- Real card, bank, wallet, UPI, or PII handling.
- General prompt-injection defense claims.
- Vault Whisper-style secret exfiltration testing, reserved for v2.

