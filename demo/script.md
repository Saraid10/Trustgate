# Demo script

Six minutes, five beats, one browser tab and one terminal. Everything is synthetic.

The order is deliberate: the problem is shown before the fix, because three clean refusals prove a
system works and are weak at showing why anyone needs it.

---

## Before you record

**Activate the virtual environment first, in every terminal you will use.** This is not optional
and it is the failure that looks worst on camera: bare `python` on this machine is the system
interpreter, which does not have the project's dependencies, so `python -m agent.stage` exits with
an import traceback before anything else has a chance to go wrong.

```powershell
.venv\Scripts\Activate.ps1
```

Your prompt should now start with `(.venv)`. Confirm the interpreter is the right one:

```bash
python -c "import sys; print(sys.executable)"
```

It must print a path inside `.venv`. If it prints `C:\Python314\python.exe`, the activation did
not take and every command below will fail.

Then run these once and leave them running.

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

**Check before rolling:** the console shows `0 attempts` and *no decision panel at all* — the
panel only appears once something has been attempted. If either is not true, stage again.

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

> The difference is not a filter that recognized an attack. One interface had a field for the
> money and the other did not.

---

## Beat 1 · A purchase that should go through (0:45 – 1:30)

```bash
python -m agent.demo "Buy Starter credits for the robotics club."
```

Switch to the console and refresh.

**Read the panel at the top first — it is the biggest thing on screen and a viewer is already
looking at it.** It says **AUTHORIZED**, and directly underneath, `Order creation allowed: No`.

**Say, roughly:**

> Those two lines together are the entire design. It is authorized, and it still cannot move money.
> Authorization and payment are separate steps: the agent obtained the right to buy this, and did
> not obtain the ability to pay.

Now drop to the row. `CLOUD-STARTER ×1` proposed by the agent; **ALLOW ₹399.00** derived by the
server; `Not sent to Razorpay yet`.

> The agent proposed a SKU, a quantity, and a purpose. That is the entire set of things it can
> propose. The price, the merchant, and the currency in the middle column were looked up
> server-side from the catalog.

Point at the counters — they read `1 attempt`, `0 refused`, `1 authorized, not yet
paid`, `0 reached the provider`.

> That third counter is the state this whole design exists to produce.

Now take it to the provider:

```bash
python -m agent.checkout --open
```

That issues a one-time checkout authority, creates a real Razorpay Test Mode order from the
snapshot it is bound to, and opens the payment page.

Pay with **Netbanking** - pick any bank, then click Success on the simulated page. Test Mode
accounts have international payments off by default, so the widely quoted `4111` test card is
refused as international. UPI `success@razorpay` also works.

> That authority is bound to a hash of this exact purchase, expires in fifteen minutes, and is
> consumed by the order that just used it. It cannot be spent twice.

Refresh the console. The panel now reads `Order creation allowed: Yes`, the row names a real
`order_...`, and the counter reads `1 reached the provider`.

> That line flipped because a checkout authority was issued. It is the only thing that changed.

The payment is still `AUTHORIZED`, not captured, and that is deliberate: the browser callback came
back verified and the server refuses to treat a callback as capture evidence. Only a signed
server-to-server event may move money, and Razorpay cannot reach a laptop. So deliver those events:

```bash
python -m agent.capture
```

**Say the provenance before you say the result — the command prints it, so read it:**

> These two events are signed here with this project's own webhook secret and posted locally.
> Razorpay did not send them. The order is real and provider-originated; the delivery is not.
> What this proves is the signature check over raw bytes, the event identity that keeps the
> authorization and the capture from deduplicating against each other, the amount cross-checked
> against the order the server derived, and the state machine carrying the payment through.

Refresh. The row reads `payment CAPTURED`, and the panel reads `Order creation allowed: No` —
*The money has already moved for this purchase*.

> And now it refuses again, for the opposite reason. It was refused before because no authority had
> been issued; it is refused now because the money has already moved.

Click **Receipt**. Let the three columns sit on screen for a beat.

> Proposed, derived, provider outcome. The evidence record is assembled from the rows themselves,
> so the readable receipt and the JSON record cannot disagree.

---

## Beat 2 · A purchase that needs a human (1:30 – 2:30)

```bash
python -m agent.demo "Buy Team credits for the robotics club."
```

Refresh the console. The panel reads **APPROVAL REQUIRED**, in words: *A human has to approve
this before it can go any further*. The new row is **amber**: `REQUIRE_APPROVAL ₹600.00`.

> Six hundred rupees is over this tenant's approval threshold, so the agent cannot complete it
> alone. The payment is sitting in `APPROVAL_REQUIRED`.

Now grant it — **from a separate command, and say so**:

```bash
python -m agent.approve
```

> That is a different command holding a token the agent does not have, under a different identity.
> If the approver identity matched the requester, the server refuses it outright. Separation of
> duties is enforced, not assumed from configuration.

Refresh. The panel turns **AUTHORIZED** and the row turns **green**, reading
`approved by trustgate-demo-human`.

---

## Beat 3 · The attack, refused (2:30 – 3:15)

```bash
python -m agent.demo --adversarial "Buy cloud credits for the club."
```

Refresh the console. The panel reads **BLOCKED**, and under it, in plain words: *The agent asked
for more than the catalog allows*. The new row is **red**.

**Say, roughly:**

> Same agent, same catalog, same injected instruction you saw in the first beat — literally the
> same string; both demos build their catalog from one module and a test asserts they match.
>
> The amount and the merchant were discarded, because the proposal has nowhere to put them. What
> survived is a quantity of fifty, which the agent *is* allowed to propose — and the server bounds
> that against the catalog's own maximum of two.

Then the sentence to land on. **Do not assert it — point at it.** The panel has already printed
`No payment request was created, so there is nothing to write a receipt about.`, and the third
column says `no payment request was created` and `no amount was derived`.

> No payment request was created. No amount was derived. There is no receipt to open, because
> there is nothing to write one about.

Reading back a line the system printed is a different kind of claim from making one. Let the panel
say it, then say it yourself, then stop talking and let the red sit.

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

## Beat 5 · Delegation narrows, and revocation cascades (4:15 – 5:15)

```bash
python -m agent.delegate
```

Roughly 60 seconds. Say, while the tree prints:

> A human gives one agent a budget. That agent gives part of it to another, which gives part of that
> to a third. Every hop is narrower than the one above it.

When the sibling is refused:

> This is the part the standards do not cover yet. Attenuation is defined over capabilities as sets,
> and a child no wider than its parent can never widen the chain. But money is not a set. Two
> children each granted exactly the parent's budget pass every per-edge check and hold twice what
> the parent had between them. So the budget is partitioned, not compared - and the check constraint
> that refuses it is on the parent's row, not in the code.

When the revoked hop kills the leaf:

> Nothing was sent to that agent. It was never written to. Its authority is re-derived from its
> whole chain every time it spends, so cutting a link above it is already the end of the branch.
> A signed token would have had to be hunted down and recalled - which is why revocation is on the
> open-problems list for that whole family of designs.

Then say the part that is easy to leave out, because it is the strongest thing here:

> This is not a separate demo. That chain is checked inside the same authorization path that
> refused the attack a minute ago. A purchase by an agent holding a delegation is measured against
> every hop above it before the request is even recorded, the budget is taken in the same
> transaction as the daily limit, and it comes back if the payment never happens.

If asked how you know the two budgets cannot disagree:

> Because a refusal on either one gives back both. That was the bug: reserving the daily budget and
> then refusing on the delegation leaves the reservation moved for a payment that never happened,
> and the release path only fires on a state transition a refused request never reaches. Both
> claims are in one savepoint now. Three tests exist for that single property.

Do not claim this solves multi-agent delegation. It does not: there is no agent identity here, so
nothing proves the agent spending is the agent the chain was granted to, and there are no
cross-tenant chains. `docs/limitations.md` says both and so should you.

---

## Beat 6 · What is proven, and what is not (5:15 – 6:00)

```bash
make mutation
```

> Sixteen adversarial scenarios, and a generated attack matrix a test keeps honest, so the README
> cannot claim an attack that is not covered.
>
> And this: fifty-three deliberate breaks of the safety code, each requiring its tests to fail. A
> passing suite says the code behaves as written. It does not say the tests would object if it
> stopped doing something important.

Say the number the terminal is printing, not one you remember. It has been sixteen, eighteen,
twenty-six, thirty-three, thirty-seven, forty-one, forty-four, forty-seven, fifty and fifty-two on the way here, and being contradicted by your own screen is the one
mistake in this beat a viewer will actually notice.

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
| `ModuleNotFoundError` on any `python -m ...` | The venv is not active in that terminal. `.venv\Scripts\Activate.ps1`, then re-run. |
| Anything hangs, or Docker errors about the pipe | Docker Desktop died — it does that on a machine this size. Relaunch Docker Desktop, wait for the whale, `docker compose up -d`. |
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
