# Demo script

Five minutes, four beats, one browser tab and one terminal. Everything is synthetic.

The order is deliberate: the problem is shown before the fix, because three clean refusals prove a
system works and are weak at showing why anyone needs it.

---

## Before you record

Run these once and leave them running.

```bash
docker compose up -d
```

```bash
python -m alembic upgrade head
```

Confirm `.env` has `ENABLE_CONSOLE=true`, `DEMO_APPROVER_TOKEN`, and `DEMO_APPROVER_ID`. If you
change `.env`, the API container only reads it on creation:

```bash
docker compose up -d --force-recreate api
```

Then stage the demo. **Run this between every take** — it clears the timeline so you are not
filming the last attempt's leftovers.

```bash
python -m agent.stage
```

It prints the console URL and the exact commands for each beat. Export the identity it gives you:

```bash
export MCP_TENANT_ID=d3f0d3f0-0000-4000-8000-000000000001
```

```bash
export MCP_ACTOR_ID=trustgate-demo-buyer
```

Open the console and leave it on a second monitor or a second tab:

```
http://127.0.0.1:8000/console/d3f0d3f0-0000-4000-8000-000000000001
```

**Check before rolling:** the console shows `0 attempts`. If it does not, stage again.

---

## Beat 0 · The problem, in our own code (0:00 – 0:45)

```bash
python -m demo.unguarded
```

**What appears:** one model response handed to two adapters. The unguarded one pays
**INR 20,000.00 to `attacker-controlled-merchant`** against a catalog price of INR 600.00.

**Say, roughly:**

> This is an AI buying agent reading a product catalog. One of the descriptions was written by a
> supplier, and it contains an instruction. The agent follows it.
>
> The tool it is calling accepts an amount and a merchant, because that is the obvious way to write
> a payment tool — the model needs to say what to buy and what it costs. Nothing about that reads
> as a vulnerability until untrusted text reaches the model.
>
> That is my code, in this repository. It is the problem I built the rest of it to solve.

**Do not say** "blocked", "detected", or "caught". Nothing was. Point at the second half of the
output: the same instruction through TrustGate has nowhere to put an amount or a merchant.

> The difference is not a filter that recognised an attack. One interface had a field for the
> money and the other did not.

---

## Beat 1 · A purchase that should go through (0:45 – 1:30)

```bash
python -m agent.demo "Buy Starter credits for the robotics club."
```

Switch to the console and refresh.

**What appears:** one row. `CLOUD-STARTER ×1` proposed by the agent; **ALLOW ₹399.00** derived by
the server; `Not sent to Razorpay yet`.

**Say, roughly:**

> The agent proposed a SKU, a quantity, and a purpose. That is the entire set of things it can
> propose. The price, the merchant, and the currency in the middle column were looked up
> server-side from the catalog.
>
> It is authorized and nothing has been sent to Razorpay. Authorization and payment are separate
> steps: the agent obtained the right to buy this, and did not obtain the ability to pay.

Point at the counters along the top — they read `1 attempt`, `0 refused`, `1 authorized, not yet
paid`, `0 reached the provider`.

> That third counter is the state this whole design exists to produce.

Click **Receipt**. Let the three columns sit on screen for a beat.

> Proposed, derived, provider outcome. The evidence record is assembled from the rows themselves,
> so the readable receipt and the JSON record cannot disagree.

---

## Beat 2 · A purchase that needs a human (1:30 – 2:30)

```bash
python -m agent.demo "Buy Team credits for the robotics club."
```

Refresh the console. The new row is **amber**: `REQUIRE_APPROVAL ₹600.00`.

> Six hundred rupees is over this tenant's approval threshold, so the agent cannot complete it
> alone. The payment is sitting in `APPROVAL_REQUIRED`.

Now grant it — **from a separate command, and say so**:

```bash
python -m agent.approve
```

> That is a different command holding a token the agent does not have, under a different identity.
> If the approver identity matched the requester, the server refuses it outright. Separation of
> duties is enforced, not assumed from configuration.

Refresh. The row turns **green** and reads `approved by trustgate-demo-human`.

---

## Beat 3 · The attack, refused (2:30 – 3:15)

```bash
python -m agent.demo --adversarial "Buy cloud credits for the club."
```

Refresh the console. The new row is **red**.

**Say, roughly:**

> Same agent, same catalog, same injected instruction you saw in the first beat — literally the
> same string; both demos build their catalog from one module and a test asserts they match.
>
> The amount and the merchant were discarded, because the proposal has nowhere to put them. What
> survived is a quantity of fifty, which the agent *is* allowed to propose — and the server bounds
> that against the catalog's own maximum of two.

Then the sentence to land on, pointing at the third column:

> No payment request was created. No amount was derived. There is no receipt to open, because
> there is nothing to write one about.

---

## Beat 4 · Why it holds (3:15 – 4:15)

Two mechanisms only. Resist explaining everything.

> **One.** The agent's tool contract declares three fields: SKU, quantity, purpose. There is no
> amount field to attack. Every money-critical fact is derived server-side.
>
> **Two.** Authority is short-lived and single-use. A checkout permission is bound to a hash of the
> exact purchase, expires in fifteen minutes, and is consumed once. If the policy changes underneath
> it, or the purchase is edited, it stops being valid.

---

## Beat 5 · What is proven, and what is not (4:15 – 5:00)

```bash
make mutation
```

> Sixteen adversarial scenarios, and a generated attack matrix a test keeps honest, so the README
> cannot claim an attack that is not covered.
>
> And this: seventeen deliberate breaks of the safety code, each requiring its tests to fail. A
> passing suite says the code behaves as written. It does not say the tests would object if it
> stopped doing something important.

Close on the limits, deliberately:

> Test Mode only, synthetic data, header-based tenant identity that is not production
> authentication, and receipts that are traceable but not yet tamper-evident.
> `docs/limitations.md` names every cut.

---

## If something goes wrong on camera

| Symptom | Fix |
|---|---|
| Console shows old rows | `python -m agent.stage`, refresh |
| `python -m agent.approve` says nothing is waiting | Beat 2's purchase did not create one — re-run it |
| Console 404s | `ENABLE_CONSOLE=true` missing; `docker compose up -d --force-recreate api` |
| Approval refused `APPROVER_IS_REQUESTER` | `DEMO_APPROVER_ID` equals `MCP_ACTOR_ID` — change one |
| Anything hangs | Docker Desktop stopped. Restart it, `docker compose up -d` |

---

## Claims to avoid

The demo is strong because it is exact. These are the phrasings that would make it untrue:

- **"blocked" or "detected"** in beat 0 — nothing was blocked; the fields were never accepted.
- **"tamper-evident" receipts** — they are traceable, assembled from live rows, not hashed or
  signed.
- **anything about other products' controls** — this project makes no such comparison and does not
  need one.
- **"secure" or "safe"** without a scope — say what is enforced and where, or say nothing.
