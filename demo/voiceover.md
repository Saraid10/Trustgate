# Voiceover script — matched to `Demo Screen.mp4` (5:22)

Not the general pitch. This is keyed to **the take you actually recorded**, in the order you
actually did it, so you can read it straight through while the timeline plays in Clipchamp.

**About 900 words across 5:22 — an average of 168 words a minute.** Every block below sits between
145 and 185, which is a comfortable speaking pace with real pauses in it. You are not talking for
all 322 seconds and you should not try to.

**Before you record a word:** watch the video once, end to end, with this open beside you. Knowing
what is coming is the entire difference between reacting to the screen and reading at it.

The windows are deliberately wider than the scene changes. **Your voice does not have to land on
the cut** — it is better if it slightly overruns, because that is how people actually talk over
their own screen.

If you trim the video further, these timestamps shift. Re-check before you start.

---

## 0:00 – 1:00 · The unguarded run, then a normal purchase

*The two output blocks are already on screen. Around 0:32 you type the Starter purchase.*

> "An AI buying agent reads a product catalogue. A supplier hid an instruction inside one of the
> product descriptions, and the agent does what it says. That is not a model bug — following
> instructions in text is the job.
>
> Same model response, handed to two payment tools. The first takes an amount and a merchant,
> written the obvious way. It just charged twenty thousand rupees to a merchant called
> attacker-controlled-merchant. The catalogue price was six hundred."

**Pause. Two seconds.**

> "The second one is mine. Nothing happened — and nothing detected an attack. No filter, no
> classifier, no score. My tool has three fields: SKU, quantity, purpose. There is no amount field
> for that instruction to land in.
>
> So here is an ordinary purchase through the same agent. It proposes a SKU and a quantity. The
> price, the merchant and the currency are all derived server-side from the catalogue — the agent
> never sees that number."

## 1:00 – 1:35 · Checkout, then the gap

*Razorpay loads. Then the console: AUTHORIZED, order creation allowed: No.*

> "That takes me to a real Razorpay order — Test Mode, real API, real signatures, no real money. And
> the agent did not create it. A human did, from a separate command.
>
> Now this is the bit I would most want you to notice. It says AUTHORIZED. And directly underneath:
> order creation allowed, no. Being allowed to buy something is not the same as being able to pay
> for it, and most systems collapse those into one state. This purchase is approved and it cannot
> move a rupee."

## 1:35 – 2:05 · The attack, refused

*The banner flips to REFUSED. Two rows now.*

> "Same agent, same catalogue, same injected instruction as the very first thing I showed you.
>
> The amount and the merchant were thrown away again. What survived is a quantity of fifty — which
> the agent *is* allowed to propose — and the server bounds that against the catalogue's own maximum
> of two."

**Stop talking. Let the red sit for two full seconds.**

> "No payment request was created, so there is nothing to write a receipt about. And underneath it,
> the one that did go through: confirmed, payment captured — by a signed event from Razorpay, not by
> the browser claiming so."

## 2:05 – 2:55 · The trace, and the human

*The JSON trace, then the Team purchase, then the approval on the console.*

> "There is the trace. Amount and merchant, discarded at the boundary. Quantity exceeds limit, from
> the catalogue itself.
>
> Now something more expensive — six hundred rupees, over this tenant's approval threshold. The
> agent stops. It literally cannot complete this on its own.
>
> That approval came from a different command, under a different identity, holding a token the agent
> does not have. And if the approver had matched the requester, the server would have refused it.
> Separation of duties is enforced, not just configured."

## 2:55 – 3:40 · Delegation

*The chain prints in the terminal, then you switch to the diagram.*

> "This next part is the piece I am proudest of: what happens when an agent delegates to another
> agent.
>
> A human funds an agent with two thousand. That agent funds another with twelve hundred, and that
> one funds a third with six hundred. Every hop narrows.
>
> Here is the problem I hit. Every capability standard I read models attenuation as set
> intersection — a child is never wider than its parent. Correct for permissions. Wrong for money,
> because budgets add and sets do not.
>
> Look at the two boxes. Both children are no wider than their parent, so every per-edge check
> passes — and between them they would hold two thousand four hundred of a two thousand budget. So
> budgets are partitioned, not compared, and the constraint that refuses it sits on the parent's own
> database row."

## 3:40 – 4:10 · The layers

*Section 4, with the dotted line.*

> "Architecturally, that is the shape of the whole thing. The agent proposes three fields, and that
> is the only arrow it ever crosses. Five MCP tools, and not one of them can authorize, capture,
> refund, or reach a provider.
>
> And this is the claim I would stake the project on. Delete the agent entirely, and everything
> below that dotted line is unchanged and still correct. If the agent were the centre of this, the
> project would be arguing against itself."

## 4:10 – 4:45 · The mutation run

*`53 mutations applied to the safety core`, then the `[caught]` lines.*

> "And this is how I know any of it holds. Fifty-three deliberate breaks — it deletes each safety
> guard in the source, one at a time, and requires the test protecting it to fail. Every one is
> caught.
>
> That exists because of something that genuinely scared me. I deleted the check that stops an
> expired policy from authorising payments, just to see what would happen, and three hundred and two
> tests passed. Two clean code reviews had already been over that file. A passing suite tells you
> the code does what you wrote. It does not tell you your tests would notice if it stopped."

## 4:45 – 5:22 · The ladder, and the close

*The 2 / 4 / 6 pyramid, then back to the console.*

> "And the rules that matter are not in my code at all. Two live in application code — the weakest
> place. Four hold inside a transaction. Six are database constraints. No query gets around that
> bottom row, including mine.
>
> So every money action here is gated, bounded and explainable, with an audit trail behind it.
>
> What it does not do: Test Mode only, deliberately. Tenant identity is a header, not real
> authentication. And no agent identity yet — nothing proves the agent spending is the agent the
> budget was given to. Biggest gap, first thing I'd build next.
>
> Nearly six hundred tests, fifty-three deliberate breaks, and JUDGE dot M-D maps every claim to the
> command that proves it. Thanks for watching."

---

## Reading it

- **One continuous take.** Sentence-by-sentence dubbing is what makes a voiceover sound assembled.
  Let small stumbles stand — they read as human.
- **The two-second silence after the refusal is the most persuasive moment in the video.** Do not
  fill it.
- **Never say "blocked", "caught" or "detected"** about the attack. Nothing was. The phrase is
  *nowhere to go*, and a reviewer who knows payments hears the difference.
- If you fall behind the picture, **stop and let it catch up.** Silence over a moving screen reads
  as confidence; racing to catch up reads as panic.
- Say **"JUDGE dot M-D"**, not "judge markdown". It is the one place the script names a filename
  aloud.
