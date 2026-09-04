# The pitch — what to actually say

`script.md` is the rehearsal walkthrough: every command, every recovery, what to check before
rolling. This is the other thing — the words. Read it out loud twice before you record and it will
stop sounding like reading.

**Talk to one engineer.** Not a panel, not a camera. Someone sitting next to you who knows payments
and has not seen this before. That is the register the whole thing is written in, and it is what
the submission asks for.

**Do not read this verbatim.** Get the shape and the three or four sentences that matter, then say
it in your own words. A slightly rough sentence you mean beats a smooth one you are reciting, and
the difference is audible.

## Length — read this before you record

The spoken text below is **838 words**. At a normal presenting pace that is about **5 minutes 35 of
speech, and 5:55 once the pauses are in**. That is the honest number; it was measured, not
estimated.

This demo runs eight commands. Its content is a six-minute demo, and pretending otherwise on camera
just means talking too fast through the delegation beat — which is the one nobody else has.

- **If five minutes is guidance** (most briefs are), record this as written and land at 5:55.
- **If five minutes is a hard cap**, make the three cuts marked `[CUT A]`–`[CUT C]` in the text
  before you record. They remove 85 words and bring it to **5:02 of speech, 5:22 with pauses** —
  and if you need the last twenty seconds, drop the three framing sentences listed under `CUT D`.
  All of them are listed together at the bottom.

Decide which of those it is *before* the first take. Do not try to fix length by speeding up.

---

## 0:00 – 0:50 · Start with the thing going wrong

**Run:** `python -m demo.unguarded`

> "I'll start with the problem, in my own code, because that's the only honest way to show it.
>
> An AI buying agent reads a product catalogue. A supplier hid an instruction in one of the
> descriptions, and the agent does what it says. That's not a model bug — following instructions in
> text is the job.
>
> Same model response, two payment tools. The first takes an amount and a merchant, written the
> obvious way. It just paid twenty thousand rupees to attacker-controlled-merchant. The catalogue
> price was six hundred. The second one is mine. Nothing happened."

**Pause. Let the two blocks sit on screen for two seconds.**

> "And nothing detected an attack. No filter, no classifier, no score. My tool has three fields —
> SKU, quantity, purpose. There's no amount field. The injected text had nowhere to go."

**Do not say "blocked" or "caught". Nothing was blocked.** The phrase is *nowhere to go*.

---

## 0:50 – 1:40 · A purchase that works, and the gap that is the product

**Run:** `python -m agent.demo "Buy Starter credits"` — then refresh the console.

> "A normal purchase. The agent proposed a SKU and a quantity; price, merchant and currency were all
> derived server-side from the catalogue. The agent never saw that number."

**Point at the panel.**

> "Here's the bit to notice. It says AUTHORIZED — and underneath, order creation allowed, no. Being
> allowed to buy isn't being able to pay. This is approved and it cannot move a rupee."

**Run:** `python -m agent.checkout --open` — pay, come back. Then `python -m agent.capture`.

> "The authority that closes that gap is single-use, fifteen minutes, and bound to a hash of this
> exact purchase.
>
> And only a signed server-to-server event moves money. `[CUT A →` The browser came back saying
> paid, and the server verified it and did nothing, because the browser is the buyer's machine.
> `← CUT A]` That path isn't mocked — a real Razorpay-delivered webhook is committed in this repo."

---

## 1:40 – 2:20 · The attack, refused

**Run:** `python -m agent.demo --adversarial "Buy cloud credits"` — refresh.

> "Same agent, same catalogue, same injected string as the first thing I showed you — both demos
> build from one module and a test asserts they match.
>
> The amount and the merchant were discarded; there's nowhere to put them. What survived is a
> quantity of fifty, which the agent *is* allowed to propose — and the server bounds it against the
> catalogue's own maximum of two."

**Point at the panel and read it.**

> "No payment request was created, so there's nothing to write a receipt about."

**Stop talking. Two full seconds. Let the red sit.**

---

## 2:20 – 3:20 · Delegation — the part nobody else did

**Run:** `python -m agent.delegate`

> "This is the piece I'm proudest of — it came from asking what happens when an agent delegates to
> another agent.
>
> A human gives an agent a budget. That agent gives part of it to another. Every hop narrows.
>
> Here's the problem. Every capability standard I read models attenuation as set intersection: a
> child is never wider than its parent. Correct for permissions. Wrong for money, because budgets
> add and sets don't.
>
> The parent holds two thousand and has already promised twelve hundred to one child. A second child
> asks for twelve hundred more. Per-edge narrowing allows it — but between them they'd hold more
> than the parent ever had."

**Point at the refusal.**

> "So budgets are partitioned, not compared. And the constraint refusing this sits on the parent's
> own database row, not in my code where another query could walk around it."

**Then the revocation.**

> "Then revocation. The human cancels the middle of the chain. The agent at the bottom is never
> touched and never told, and its next spend is refused — because authority is re-derived from the
> whole chain every time, including again at checkout. A signed token would have to be hunted down
> and recalled."

---

## 3:20 – 4:20 · What broke, and how I know any of this works

**Run:** `make mutation`

> "Now — what broke, because plenty did.
>
> The one that scared me: a check stopping an expired spending policy from authorising payments. I
> deleted it on purpose to see what would happen. Three hundred and two tests passed. Two clean
> reviews had missed it.
>
> That's what's running now. It deletes each of my fifty-three safety guards, one at a time, and
> requires the test protecting it to fail. A passing suite tells you the code does what you wrote.
> It doesn't tell you the tests would notice if it stopped."

**While it runs, keep going.**

> "`[CUT B →` Two more. A lock I thought was protecting payment state queued callers correctly and
> then handed the second one stale data — serialising *when* things ran, not *what they decided
> from*. Nine tests drive real concurrent sessions at that now. `← CUT B]` And a refusal after the
> first of two budget claims left that budget moved, for a payment that never happened."

---

## 4:20 – 5:20 · Architecture, limits, close

> "Architecturally: five MCP tools, none of them can pay, and every money-critical fact is derived
> server-side. Delete the agent entirely and the authorization core is unchanged — if the agent were
> the centre of this, the project would be arguing against itself.
>
> `[CUT C →` The rule throughout: enforce at the lowest layer that can hold it. Database constraint
> over transaction, transaction over application code, application code over convention. `← CUT C]`
> I learned that the hard way — I wrote three sibling budgets straight past my own module and every
> check in it passed.
>
> So every money action is gated, bounded and explainable, with an audit trail behind it.
>
> Limits: Test Mode only, and that's deliberate.
> Tenant identity is a header, not real authentication.
> And there's no agent identity — nothing proves the agent spending is the agent the budget was
> given to. That's the biggest gap and the first thing I'd build next."

**Last line. Slow down.**

> "Nearly six hundred tests, fifty-three deliberate breaks, and `JUDGE.md` maps every claim to the
> command that regenerates it. Thanks for watching."

---

## The cuts, in order

A, B and C are marked inline so you cannot lose your place mid-take. Together they take 841 words
down to 756 — **5:02 of speech, 5:22 with pauses**. D takes it to about 4:58.

| | What goes | Cost | Why it is the cheapest thing to lose |
|---|---|---|---|
| **A** | The browser-callback sentence (0:50) | 21 w | The next sentence — *a real Razorpay-delivered webhook is committed in this repo* — carries the point on its own, and harder. |
| **B** | The stale-lock story (3:20) | 40 w | You keep the savepoint story, which is shorter and lands harder. One war story plus the mutation suite already answers the brief. |
| **C** | The enforcement-ladder sentence (4:20) | 24 w | The delegation beat already said *on the parent's own database row, not in my code*. The punchline right after it survives without the taxonomy. |
| **D** | Three framing sentences, if you still need 20 seconds | ~60 w | *"I'll start with the problem, in my own code…"* — just start. *"This is the piece I'm proudest of — it came from asking…"* — open on **A human gives an agent a budget**. And *"I learned that the hard way…"* in the close. All three are throat-clearing; none is evidence. |

**Already cut from this script:** the approval beat, which ran `agent.demo "Buy Team credits"` and
`agent.approve`. If you record the long version and find you have room, it is the first thing to put
back — the line for it is under *If you have room* below.

**Never cut the opening or the attack.** Those two are the pitch.

## If you have room

Only after a rehearsal take has come in under time. Each is one sentence, placed where it costs
least.

- **Back into 0:50:** *"Publish a new policy and the authority is revoked — it doesn't outlive the
  rules it was checked under."*
- **Back into 1:40, on separation of duties:** *"Anything over the tenant's approval threshold stops
  and waits for a human under a different identity — and if the approver matches the requester, the
  server refuses."*
- **After the mutation line (3:20):** *"And the attack matrix in the README is generated from the
  scenario registry, with a test asserting they match — so the documentation can't claim an attack
  that isn't covered by a passing test."*
- **In the close (4:20):** *"The regression suite never calls a model — deterministic stand-ins, so
  safety verification doesn't depend on how a model behaves on a given day. But `--live` runs a real
  one against the same hostile catalogue, and measures influence by sending that catalogue twice,
  once with the descriptions stripped."*
- **After the webhook line (0:50):** *"The signature is verified over the raw bytes before anything
  parses the body, and the provider's own event ID is stored, so a replay is refused by the database
  rather than by whichever handler happens to look."*

## Things that will make it sound written

- Reading a sentence you'd never say out loud. If it feels stiff, say it wrong instead.
- Filling every silence. The two seconds after the refusal is the most persuasive moment you have.
- Any adjective doing work a number could do. "Very thorough" is worse than "302 tests passed".
- Apologising for the limits. State them at the same pace as everything else and move on.

## Numbers to have right

593 tests · 53 mutations · 16 adversarial scenarios · 19 migrations · 5 MCP tools, none of them pay.

Say the number the terminal is printing, not one you remember.
