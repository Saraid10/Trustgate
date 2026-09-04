# The five-minute pitch — what to actually say

`script.md` is the rehearsal walkthrough: every command, every recovery, what to check before
rolling. This is the other thing — the words. Read it out loud twice before you record and it will
stop sounding like reading.

**Talk to one engineer.** Not a panel, not a camera. Someone sitting next to you who knows payments
and has not seen this before. That is the register the whole thing is written in, and it is what
the submission asks for.

**Do not read this verbatim.** Get the shape and the three or four sentences that matter, then say
it in your own words. A slightly rough sentence you mean beats a smooth one you are reciting, and
the difference is audible.

**Pace.** Roughly 600 words of speech across five minutes. That is slow. It will feel too slow
while you are doing it and it will sound right on playback. Everyone speeds up on camera; budget
for it.

---

## 0:00 – 0:45 · Start with the thing going wrong

**Run:** `python -m demo.unguarded`

> "Before I show you what I built, I want to show you the problem — and I want to show it in my own
> code, because I think that's the only honest way to do it.
>
> This is an AI buying agent reading a product catalogue. One of those descriptions was written by
> a supplier, and there's an instruction hidden in it. The agent reads it, and does what it says.
> That's not a bug in the model. Following instructions in text is the whole job.
>
> Both halves of this run on the same model response. The first one is a payment tool written the
> obvious way — it takes an amount and a merchant, because the agent has to say what it's buying and
> what it costs. It just paid twenty thousand rupees to a merchant called
> attacker-controlled-merchant. The catalogue price was six hundred.
>
> The second one is mine. Same instruction, same agent. Nothing happened."

**Pause here. Let the two blocks sit on screen for two seconds.**

> "And I want to be precise about why, because it's the whole point. Nothing detected an attack.
> There's no filter, no classifier, no score. My tool has three fields — SKU, quantity, purpose.
> There is no amount field. The injected text had nowhere to go."

**Do not say "blocked" or "caught". Nothing was blocked.** The word you want is *nowhere to go*.

---

## 0:45 – 1:40 · A purchase that works, and the gap that is the product

**Run:** `python -m agent.demo "Buy Starter credits"` — then refresh the console.

> "So here's a normal purchase. The agent proposed a SKU and a quantity. Everything else — the
> price, the merchant, the currency — the server looked up from the catalogue. Three ninety-nine.
> The agent never saw that number and can't change it."

**Point at the panel.**

> "Now this is the bit I'd most want you to notice. It says AUTHORIZED. And directly underneath:
> order creation allowed, no.
>
> Those are two different things and most systems collapse them into one. Being allowed to buy
> something isn't the same as being able to pay for it. This purchase is approved and it cannot move
> a rupee, because no checkout authority has been issued yet."

**Run:** `python -m agent.checkout --open` — pay, come back.

> "That authority is single-use, expires in fifteen minutes, and is bound to a hash of this exact
> purchase. Change the amount after the fact and the hash stops matching."

**Run:** `python -m agent.capture`

> "And only a signed server-to-server event actually moves the money. When the browser came back
> and said the payment was done, the server verified that and deliberately did nothing with it —
> because the browser is the buyer's machine. In the audit trail you can see the webhook capturing
> at fifty-two twenty-five and the browser callback arriving ten seconds later, changing nothing."

---

## 1:40 – 2:15 · The human, and separation of duties

**Run:** `python -m agent.demo "Buy Team credits"`

> "Six hundred rupees is over this tenant's approval threshold, so the agent stops. It literally
> cannot complete this on its own."

**Run:** `python -m agent.approve`

> "That's a different command, holding a token the agent doesn't have, under a different identity.
> And if the approver identity matches the requester, the server refuses it. Separation of duties is
> enforced, not just configured."

---

## 2:15 – 2:55 · The attack, refused

**Run:** `python -m agent.demo --adversarial "Buy cloud credits"` — refresh.

> "Same agent, same catalogue, same injected instruction as the very first thing I showed you.
> Literally the same string — both demos build their catalogue from one module and there's a test
> that asserts they match.
>
> The amount and the merchant were discarded, because there's nowhere to put them. What survived is
> a quantity of fifty, which the agent *is* allowed to propose — and the server bounds that against
> the catalogue's own maximum of two."

**Point at the panel and read it.**

> "No payment request was created, so there is nothing to write a receipt about."

**Stop talking. Two full seconds. Let the red sit.**

---

## 2:55 – 3:40 · Delegation — the part nobody else did

**Run:** `python -m agent.delegate`

> "This next part is the piece I'm proudest of, and it came from asking what happens when an agent
> delegates to another agent — which is where all of this is obviously going.
>
> A human gives an agent a budget. That agent gives part of it to another. Every hop narrows.
>
> Here's the problem I hit. Every capability standard I read models attenuation as set
> intersection — a child can never be wider than its parent. That's correct for permissions. It is
> wrong for money, because budgets add and sets don't.
>
> Watch. The parent holds two thousand. It's already promised twelve hundred to one child. A second
> child asks for another twelve hundred — and per-edge narrowing *allows* that, because the child
> isn't wider than the parent. But between them they'd hold more than the parent ever had."

**Point at the refusal.**

> "So the budget is partitioned, not compared. And the constraint that refuses it is on the parent's
> own database row — not in my code, where a different query could walk around it."

**Then the revocation.**

> "And here's revocation. The human cancels the middle of the chain. The agent at the bottom was
> never touched and never told — its own row still says it's live — and its next spend is refused,
> because authority is re-derived from the whole chain every single time. A signed token would have
> to be hunted down and recalled. That's an open problem for that entire family of designs."

---

## 3:40 – 4:30 · What broke, and how I know any of this works

**Run:** `make mutation`

> "Now — what broke. Because plenty did, and honestly the failures taught me more than the features.
>
> The one that actually scared me: I had a check that stops an expired spending policy from
> authorising payments. I deleted it on purpose to see what would happen. Three hundred and two
> tests passed. Two clean reviews had missed it. My tests weren't testing the thing I thought they
> were testing.
>
> That's what this is running now. It deletes each of my fifty-three safety guards, one at a time,
> and requires the tests protecting it to fail. A passing test suite tells you the code does what
> you wrote. It doesn't tell you the tests would notice if it stopped."

**While it runs, keep going.**

> "Two more. I had a database lock that I thought was protecting payment state. It was queuing
> callers correctly — and then handing the second one stale data, so it approved a payment that had
> just been approved. The lock was serialising *when* things ran, not *what they decided from*.
>
> And when I wired delegation into payments, a refusal after the first of two budget claims left
> that first budget moved — for a payment that never happened. Both claims are in one savepoint now.
> Three tests exist for that single property."

---

## 4:30 – 5:00 · Architecture, limits, close

> "Architecturally: the agent gets five MCP tools and none of them can pay. Every money-critical
> fact is derived server-side. The rules that matter live in Postgres — triggers, check constraints,
> partial unique indexes — because a rule in Python is a rule a different query walks around. I
> learned that one the hard way: I wrote three sibling budgets straight past my own module and every
> check passed.
>
> So every money action here is gated — it needs an authority that was issued for that exact
> purchase. It's bounded — by policy, by delegation, and by the catalogue itself. And it's
> explainable: every refusal has a reason code, in plain words on the console and in the receipt,
> and the whole thing lands in an audit trail you just watched me read."
>
> What it doesn't do — Test Mode only, and that's deliberate; a project about bounded spending
> authority has no business holding live keys. Tenant identity is a header, not real authentication.
> And there's no agent identity — nothing proves the agent spending is the agent the budget was
> given to. That's the biggest gap and it's the first thing I'd build next.
>
> All of that is in limitations dot md. I'd rather you read it there than find it yourself."

**Last line. Slow down.**

> "The whole thing is nearly six hundred tests and fifty-three deliberate breaks, and
> `JUDGE.md` maps every claim to the command that regenerates it. Thanks for watching."

---

## If you have to cut

Cut **2:15's approval beat** first — Beat 3's refusal is stronger and the panel already shows
APPROVAL REQUIRED elsewhere. Never cut the opening or the attack. Those two are the pitch.

## Things that will make it sound written

- Reading a sentence you'd never say out loud. If it feels stiff, say it wrong instead.
- Filling every silence. The two seconds after the refusal is the most persuasive moment you have.
- Any adjective doing work a number could do. "Very thorough" is worse than "302 tests passed".
- Apologising for the limits. State them at the same pace as everything else and move on.

## Numbers to have right

593 tests · 53 mutations · 16 adversarial scenarios · 19 migrations · 5 MCP tools, none of them pay.

Say the number the terminal is printing, not one you remember.
