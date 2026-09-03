# For a reader with four minutes

Every claim this project makes, and the command that proves it. Nothing here asks you to take a
number on trust — each row regenerates.

If you run one thing, run this:

```bash
make triage
```

The first two steps need **no database, no Docker, and no credentials**. The third needs Postgres
and says so rather than failing.

---

## The one-sentence claim

> An AI agent can ask to buy something. It can never decide what that costs, who gets paid, or
> whether the money moves.

---

## Start here: the problem, in this repository's own code

```bash
python -m demo.unguarded
```

One model response, handed to two payment adapters. A supplier wrote an instruction into a catalog
description and the agent obeyed it.

| Adapter | Outcome |
|---|---|
| Written the obvious way, with `amount` and `merchant` parameters | **INR 20,000 to `attacker-controlled-merchant`** |
| TrustGate | **The proposal has no amount or merchant field to fill** |

**Nothing detected an attack.** No filter, no classifier, no score. One interface had a field for
the money and the other did not.

That file is in this repository on purpose. Most submissions show you the fix; this one shows you
its own code failing first, because a refusal is only interesting once you have seen what it
prevents.

---

## Claims, and what proves each

| Claim | Command | What you should see |
|---|---|---|
| The agent can influence only SKU and quantity | `pytest tests/test_scenarios_tier_a.py -q` | 16 adversarial scenarios pass |
| The agent is offered no tool that can pay | `pytest tests/test_delegation_api.py -q` | `test_the_agent_is_still_offered_no_way_to_grant_anything` |
| Authorized is not the same as able to pay | `pytest tests/test_authorization_envelope.py -q` | `..._with_no_authority_yet_is_not_allowed_to_pay` |
| A checkout authority is single-use and bound to one purchase | `pytest tests/test_checkout_authorities.py -q` | snapshot-hash and reuse refusals |
| Only a signed provider event moves money | `pytest tests/test_razorpay_webhook.py -q` | 19 tests over raw-byte verification |
| Delegated budgets partition rather than compare | `pytest tests/test_delegation.py -q` | `test_two_children_cannot_together_outspend_their_parent` |
| Revoking one hop ends the branch below it | `pytest tests/test_delegation_through_checkout.py -q` | revocation refused at issue and at consume |
| Money invariants hold under real concurrency | `pytest tests/test_concurrency.py -q` | 9 multi-session races |
| Authority relationships are database facts | `pytest tests/test_authority_relationships.py -q` | writes refused by constraints, not by code |
| **The tests would notice if a guard disappeared** | `python -m scenarios.mutation` | **53 guards deleted, every one caught** |

Everything at once — lint, types, migration parity, the full suite, and the mutation registry:

```bash
docker compose up -d      # the gate talks to Postgres
make verify
```

---

## The claim worth your scepticism, and how it is answered

A passing test suite says *the code behaves as written*. It does **not** say *the tests would
object if it stopped*. Those are different claims and only the second is worth anything when the
subject is money.

`scenarios/mutation.py` holds **53 deliberate breaks**. Each deletes one safety guard, runs the
tests meant to protect it, and **requires them to fail**. A mutation that survives is a guard that
exists in the source and not in the verification.

**It found a real one.** The check that stops an expired spending policy authorizing payments was
deleted and **302 tests still passed** — after two clean human reviews.

Guards that live in the database are proven differently, by tests that violate them directly, since
a mutation runner edits source files and a trigger already applied to a schema would not notice.

---

## What broke while building this, and what it cost

The submission asks what broke. These were found by running the system, not by reading it.

| Defect | How it was found | Why it mattered |
|---|---|---|
| Policy-expiry check unguarded | Mutation suite | 302 tests passed without it |
| Row lock returned stale data | First concurrency race | The lock serialised *when* callers ran, not *what they decided from* — two callers could authorize the same payment |
| Budget reserved and never returned | Wiring delegation into payments | A refusal after one of two budget claims left the first moved, for a payment that never happened |
| Attenuation enforced only on insert | Probing the trigger directly | An existing hop could be widened by an update |
| Sibling budgets written around the module | Direct database insert | Three children of 1,000 under a parent holding 1,000, every per-edge check passing |
| Console said "never authorized" over a captured payment | Looking at the rendered page | The panel contradicted the row beneath it |
| Demo script's first command | Running the pre-flight | Bare `python` was the system interpreter; the script would have failed on camera |

The last three are in this table deliberately. Two were found by *looking at the screen* rather than
at an assertion, and one by running documentation instead of reading it.

---

## What this does not do

Stated here rather than left to be discovered. `docs/limitations.md` has the complete list.

- **Test Mode only** — by choice. A project about bounded spending authority has no business
  holding live keys, and the code refuses an `rzp_live_` key rather than the documentation asking.
- **Tenant identity is a header**, not production authentication.
- **No agent identity.** `actor_id` is a string the caller supplies. Nothing proves the actor
  spending is the actor a delegation was granted to. **This is the largest gap** and it is named in
  the demo as well as here.
- **Receipts are traceable, not tamper-evident.** Nothing is hashed or signed; a receipt reflects
  the database as it stands rather than proving what it held earlier.
- **No rate limiting.**

---

## Evidence preserved on disk

| File | What it establishes |
|---|---|
| `docs/evidence/m3-provider-delivered-webhook.json` | A Test Mode payment carried to `CAPTURED` by events **Razorpay itself delivered** through a public tunnel |
| `docs/evidence/m3-webhook-lifecycle.json` | The same lifecycle with locally signed payloads, from before a tunnel existed |
| `docs/evidence/m1-live-*.json` | A real language model proposing against a poisoned catalog, measured by proposing twice |

`docs/evidence/README.md` records the provenance of each, including how to tell a
provider-delivered event from a locally signed one **from the stored data** rather than from the
note claiming it.

---

## Where to read further

| Document | What it holds |
|---|---|
| [`README.md`](README.md) | What it is, and why the shape is what it is |
| [`docs/architecture.md`](docs/architecture.md) | How the pieces fit |
| [`docs/decision-log.md`](docs/decision-log.md) | Every real choice, with the alternatives rejected and why |
| [`docs/limitations.md`](docs/limitations.md) | Every deliberate cut, including the unflattering ones |
| [`docs/threat-model.md`](docs/threat-model.md) | What is defended, and against whom |
| [`demo/script.md`](demo/script.md) | The rehearsed walkthrough, with the claims it refuses to make |
