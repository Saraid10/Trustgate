# The pitch — what to actually say

`script.md` is the rehearsal walkthrough: every command, every recovery, what to check before
rolling. This is the other thing — the words. Read it out loud twice before you record and it will
stop sounding like reading.

**Talk to one engineer.** Not a panel, not a camera. Someone sitting next to you who knows payments
and has not seen this before. That is the register the whole thing is written in.

**Do not read this verbatim.** Get the shape and the three or four sentences that matter, then say
it in your own words. A slightly rough sentence you mean beats a smooth one you are reciting, and
the difference is audible.

## What this video is for, and what it is not for

The submission form asks for **Project Objectives — what does it solve** and **Build Challenges &
Technical Obstacles** as written answers. Those are written answers. Narrating them costs a minute
of screen time to say something a judge can already read next to the video, and screen time is the
only scarce resource here.

`build-plan.md`'s own checklist asks the video for one thing: **"Architecture explanation rehearsed
out loud."** So this script spends its middle on architecture and its evidence, and leaves the
prose to the form. Draft answers for both form fields are in
[`demo/form-answers.md`](form-answers.md) — the war stories live there now, in more detail than you
could ever speak.

**What stays in the video is what only video can do:** money moving on screen when it shouldn't, a
panel that says AUTHORIZED and *cannot pay* in the same breath, a refusal, and the output of a run
that deleted fifty-three of your own safety guards and found every one of them guarded. None of
that reads as text.

## Length — read this before you record

The spoken text below is **953 words**, and 824 with every cut taken. Both numbers are measured,
and a test fails if they drift.

What that becomes on camera depends entirely on how fast you speak, so time a rehearsal take before
you decide anything. Add roughly 25 seconds to every figure for the pauses and the scrolling.

| Your pace | As written | With all four cuts |
|---|---|---|
| 150 wpm — deliberate | 6:21 | 5:30 |
| 190 wpm — brisk | 5:01 | 4:20 |
| **230 wpm — fast** | **4:09** | 3:35 |

If a rehearsal puts you near the bottom row, **record it as written and take no cuts at all** — the
architecture beat is what the checklist actually asks for, and it is the last thing to shorten. The
cuts exist for a slower reading, and are listed at the bottom.

Do not fix length by talking faster than you naturally do. It is audible, and this script is already
dense enough that a rushed delivery loses the delegation argument entirely.

---

## 0:00 – 0:45 · Start with the thing going wrong

**Run:** `python -m demo.unguarded`

> "An AI buying agent reads a product catalogue. A supplier hid an instruction in one of the
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

## 0:45 – 1:35 · A purchase that works, and the gap that is the product

**Run:** `python -m agent.demo "Buy Starter credits"` — then refresh the console.

> "A normal purchase. The agent proposed a SKU and a quantity; price, merchant and currency were all
> derived server-side from the catalogue."

**Point at the panel.**

> "Here's the bit to notice. It says AUTHORIZED — and underneath, order creation allowed, no. Being
> allowed to buy isn't being able to pay. This is approved and it cannot move a rupee."

**Run:** `python -m agent.checkout --open` — pay, come back. Then `python -m agent.capture`.

> "The authority that closes that gap is single-use, fifteen minutes, and bound to a hash of this
> exact purchase.
>
> And only a signed server-to-server event moves money. `[CUT A →` The signature is verified over
> the raw bytes before anything parses the body, and the provider's own event ID is stored, so a
> replay is refused by the database rather than by whichever handler happens to look. `← CUT A]`
> That path isn't mocked — a real Razorpay-delivered webhook is committed in this repo."

---

## 1:35 – 2:10 · The attack, refused

**Run:** `python -m agent.demo --adversarial "Buy cloud credits"` — refresh.

> "Same agent, same catalogue, same injected string as the first thing I showed you. `[CUT D →`
> Both demos build from one module and a test asserts they match. `← CUT D]`
>
> The amount and the merchant were discarded; there's nowhere to put them. What survived is a
> quantity of fifty, which the agent *is* allowed to propose — and the server bounds it against the
> catalogue's own maximum of two."

**Point at the panel and read it.**

> "No payment request was created, so there's nothing to write a receipt about."

**Stop talking. Two full seconds. Let the red sit.**

---

## 2:10 – 3:05 · Delegation — the part nobody else did

**Run:** `python -m agent.delegate`

> "`[CUT D →` Now the part I'd most want to talk about: `← CUT D]` what happens when an agent
> delegates to another agent.
>
> A human gives an agent a budget. That agent gives part of it to another. Every hop narrows.
>
> Every capability standard I read models attenuation as set intersection — a child is never wider
> than its parent. Correct for permissions. Wrong for money, because budgets add and sets don't.
>
> The parent holds two thousand and has already promised twelve hundred to one child. A second child
> asks for twelve hundred more. Per-edge narrowing allows it — but between them they'd hold more
> than the parent ever had."

**Point at the refusal.**

> "So budgets are partitioned, not compared, and the constraint refusing this sits on the parent's
> own database row."

**Then the revocation.**

> "Then revocation. The human cancels the middle of the chain. The agent at the bottom is never
> touched and never told, and its next spend is refused — because authority is re-derived from the
> whole chain every time, including again at checkout. A signed token would have to be hunted down
> and recalled."

---

## 3:05 – 4:20 · The architecture — this is the centre of the video

**Open this in a browser tab before you start recording:**

```
demo/architecture.html
```

A stat strip and three diagrams, built for this beat. **Scroll from one to the next as you talk** —
never narrate a screen that is not moving. Have it already open on a second tab; do not open it on
camera.

Land on the strip for two seconds before you say anything. It reads
**3 fields · 5 tools · 0 can move money · 53 guards · 16 attacks**, and it does a lot of the
arguing for you.

### 3:05 · Diagram 1 — the trust boundary

> "So — the shape of it, because this is the actual contribution.
>
> The trust boundary. The agent proposes three fields: SKU, quantity, purpose. That is the only
> arrow it ever crosses. Merchant, amount and currency are looked up server-side from the
> catalogue — there is no amount field for injected text to fill.
>
> And on the right, only a signed provider event moves money."

**Scroll to diagram 2.**

### 3:30 · Diagram 2 — the layers

> "The layers. Agent on top, untrusted and replaceable. Then five MCP tools, and not one of them can
> authorize, capture, refund, or reach a provider. Below that, the authorization core.
>
> And this is the claim I'd stake the project on — that dotted line. Delete the agent entirely, and
> everything below it is unchanged and still correct. If the agent were the centre of this, the
> project would be arguing against itself."

**Scroll to the ladder.**

### 3:55 · Diagram 3 — where each rule is enforced

**Point at the widest bar as you say the number.**

> "And this is where the rules actually live. One principle: enforce at the lowest layer that can
> hold it.
>
> Two are in application code — that's the weakest place, because a different code path can miss
> it. Four hold inside a transaction. And six are database constraints: composite foreign keys,
> a partial unique index on live approvals, a check that siblings cannot outspend their parent.
> `[CUT D →` An immutability trigger on published policies. `← CUT D]`
>
> That bottom row is the point. No query can get around those. Including mine —
> `[CUT B →` and that's not hypothetical: I once wrote three over-committed sibling budgets straight
> past my own validation module, and every check in it passed. `← CUT B]`"

---

## 4:05 – 4:50 · What the tests are worth, and the limits

⚠️ **Do not start this on camera.** `python -m scenarios.mutation` takes **5 minutes 45 seconds**
and prints *nothing at all* until it finishes — it would be a blank terminal for the whole beat.

**Instead:** run it in a second terminal **before you hit record** (the runbook's Part G tells you
when). By this point it has finished. Switch to that window and scroll slowly up through the
`[caught  ]` lines to the last line, `Every mutation was caught.`

> "Which is how I know any of it holds. Fifty-three deliberate breaks — it deletes each safety
> guard, one at a time, and requires the test protecting it to fail. This takes about six minutes,
> so I started it before we began; that's the finished run. A passing suite tells you the code does
> what you wrote. It doesn't tell you the tests would notice if it stopped. I found that out when I
> deleted an expired-policy check and three hundred and two tests passed. That's what broke.
> `[CUT D →` It is the single reason the rest of this exists. `← CUT D]`
>
> `[CUT C →` The attack matrix in the README is generated from the scenario registry, with a test
> asserting they match, so the documentation can't claim an attack that isn't covered by a passing
> test. `← CUT C]`
>
> So every money action is gated, bounded and explainable, with an audit trail behind it.
>
> Limits: Test Mode only, and that's deliberate.
> Tenant identity is a header, not real authentication.
> And there's no agent identity — nothing proves the agent spending is the agent the budget was
> given to. That's the biggest gap and the first thing I'd build next."

---

## 4:50 – 5:00 · Close

**Slow down.**

> "Nearly six hundred tests, fifty-three deliberate breaks, and `JUDGE.md` maps every claim to the
> command that regenerates it. Thanks for watching."

---

## The cuts, in order

Marked inline so you cannot lose your place mid-take. A through C take 891 words to 798 — **5:39 on
camera**. Adding D gives 758 — **5:23**.

| | What goes | Cost | Why it is the cheapest thing to lose |
|---|---|---|---|
| **A** | Raw-byte verification and replay (0:45) | 33 w | The sentence after it — *a real Razorpay-delivered webhook is committed in this repo* — is the one a judge remembers, and A6 to A8 in the attack matrix cover this in writing. |
| **B** | The three-sibling-budgets aside (3:05) | 23 w | A war story, and the form field is where war stories now live. The rule it illustrates survives without it. |
| **C** | The generated attack matrix (4:05) | 30 w | The README shows this, and a judge who opens the repo meets it immediately. |
| **D** | Four short asides, marked separately | 40 w | Pure throat-clearing: the demos-share-a-module aside, *"the part I'd most want to talk about"*, the third constraint example, and the last clause of the expired-policy story. None is evidence. |

**Never cut the opening, the attack, or the architecture beat.** Those three are the pitch.

**Already cut from this script:** the approval beat, which ran `agent.demo "Buy Team credits"` and
`agent.approve`; and the two remaining war stories, which now live in `form-answers.md`. If you have
room, the approval line is under *If you have room* below.

## If you have room

Only after a rehearsal take has come in under time.

- **Back into 0:45:** *"Publish a new policy and the authority is revoked — it doesn't outlive the
  rules it was checked under."*
- **Back into 1:35, on separation of duties:** *"Anything over the tenant's approval threshold stops
  and waits for a human under a different identity — and if the approver matches the requester, the
  server refuses."*
- **In the architecture beat (3:05):** *"The regression suite never calls a model — deterministic
  stand-ins, so safety verification doesn't depend on how a model behaves on a given day. But
  `--live` runs a real one against the same hostile catalogue, and measures influence by sending
  that catalogue twice, once with the descriptions stripped."*
- **In the architecture beat (3:05):** *"Nothing anywhere in this system can start a refund — that's
  asserted against the live route table, not by reading tool names."*
- **After the mutation line (4:05):** *"And a lock I thought was protecting payment state turned out
  to be serialising when things ran, not what they decided from. Nine tests drive real concurrent
  sessions at that now."*

## Things that will make it sound written

- Reading a sentence you'd never say out loud. If it feels stiff, say it wrong instead.
- Filling every silence. The two seconds after the refusal is the most persuasive moment you have.
- Any adjective doing work a number could do. "Very thorough" is worse than "302 tests passed".
- Apologising for the limits. State them at the same pace as everything else and move on.

## Numbers to have right

593 tests · 53 mutations · 16 adversarial scenarios · 19 migrations · 5 MCP tools, none of them pay.

Say the number the terminal is printing, not one you remember.
