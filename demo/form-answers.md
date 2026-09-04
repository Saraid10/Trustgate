# Submission form — draft answers

Paste and edit. These are drafts in your voice, not final copy — read each one out loud once and
change anything you would not say.

The video does not repeat any of this. `pitch.md` spends its time on the architecture and on things
happening on screen; this file carries the prose, because prose is what the form asked for.

| Field | Answer |
|---|---|
| Project name | `TrustGate` |
| GitHub Repository URL | `https://github.com/Saraid10/Trustgate` |
| 5-min Pitch Video Link | *(yours, once uploaded — set it to unlisted, not private)* |

---

## Project Objectives — what does it solve?

> AI agents are being given the ability to spend money, and the obvious way to build that is a
> payment tool taking an amount and a merchant. That design is unsafe for a reason no model
> improvement fixes: agents read untrusted text — product descriptions, emails, web pages — and
> following instructions in text is what a language model does. If the tool has an amount field,
> injected text can fill it. `demo/unguarded.py` in this repo demonstrates exactly that against a
> bare provider adapter: one hidden instruction in a supplier's product description moves ₹20,000 to
> an attacker-named merchant when the catalogue price was ₹600.
>
> TrustGate is the authorization layer between an agent and a payment provider. The agent proposes
> only a catalogue SKU, a quantity, and a purpose. Price, merchant and currency are derived
> server-side; the agent never sees or supplies them. No tool on the surface can authorize, capture,
> refund, or call the provider — asserted by exercising every exposed tool, not by reading names.
>
> Concretely it closes four gaps:
>
> **1. Amount and merchant tampering.** Structurally impossible rather than validated, because the
> fields do not exist for injected text to land in. Nothing is detected, filtered, or scored.
>
> **2. "Allowed to buy" conflated with "able to pay."** These are separate states here. A purchase
> can sit `AUTHORIZED` indefinitely with order creation still refused. Payment needs a separate
> single-use checkout authority, bound to a hash of that exact purchase, expiring in fifteen
> minutes, and revoked if the policy it was checked under is superseded.
>
> **3. Delegated spending authority between agents.** A human funds an agent; that agent sub-funds
> another. Every hop narrows — and critically, an agent's children cannot *between them* promise
> more than it holds. Revoking one hop ends every branch below it without recalling anything,
> because authority is re-derived from the whole chain on every spend and again at checkout.
>
> **4. Provenance of the money movement.** Only a signed provider event captures. The browser's own
> success callback is verified and then deliberately ignored, because the browser is the buyer's
> machine. A genuine Razorpay-delivered `payment.captured` is preserved in the repo as evidence.
>
> Everything is tenant-scoped and audited. Every decision writes a reason code in plain words, and
> the evidence receipt shows three columns side by side: what the agent proposed, what the server
> derived, and what the provider actually did.

---

## Build Challenges & Technical Obstacles — what issues did you face, and how did you solve them?

> **My tests passed while the code was wrong, twice, and that changed how I built the rest of it.**
>
> Early on, request-scoped database sessions were discarding every write — and 146 tests passed,
> because each test asserted inside the same transaction it had written in. Later I deleted, on
> purpose, the check that stops an expired spending policy from authorising payments. 302 tests
> passed. Two clean code reviews had missed it.
>
> A green suite tells you the code does what you wrote. It does not tell you the tests would object
> if the code stopped doing something important — and only the second claim matters when the subject
> is money. So I built a mutation harness: it deletes each of 53 safety guards in the source, one at
> a time, runs the tests that are supposed to protect it, and fails if they still pass. All 53 are
> caught. One survivor turned out to be genuinely unreachable defensive code, so I deleted it rather
> than keep a line that reads like care and does nothing.
>
> **Attenuation as set intersection is wrong for money.** When I built agent-to-agent delegation, I
> followed every capability standard I could find: a child's authority is the intersection of what
> it asks for and what its parent holds, so a child is never wider than its parent. That is correct
> for permissions and quietly wrong for budgets, because quantities add and sets do not. A parent
> holding ₹2,000 that has already promised ₹1,200 to one child will happily grant ₹1,200 to a
> second — each edge narrows correctly, and between them the children hold more than the parent ever
> had. I fixed it by partitioning the budget rather than comparing it: a `CHECK` constraint on the
> parent's own row requires the sum of live child budgets to stay within it. I put it in the
> database specifically because I had already proved I could not trust my own module — I wrote three
> over-committed sibling budgets straight past my own validation code and every check in it passed.
>
> **A lock that serialised the wrong thing.** I had a `FOR UPDATE` row lock I believed was
> protecting payment state under concurrency. It queued callers correctly, and then handed the
> second one stale data out of SQLAlchemy's identity map — so it approved a payment that had just
> been approved. The lock was serialising *when* things ran, not *what they decided from*. Fixed
> with `populate_existing=True` on the locked read, and I added nine tests that drive genuinely
> concurrent sessions rather than simulating them.
>
> **A refusal that left money moved.** Wiring delegation into the payment path introduced a leak I
> did not see for a while: a request claims the actor's daily budget, then claims the delegation
> budget, and if the second one refuses, the first stays claimed — for a payment that never happens.
> The release path only fires on a transition out of a holding state, which a denied request never
> enters, and doing the claims in the other order just mirrors the bug. Both claims now happen
> inside one savepoint, so either both hold or neither does and the ordering stops mattering.
>
> **Infrastructure, at the least convenient time.** Razorpay could not reach my machine for webhook
> delivery: ngrok's free tier was unusable, and cloudflared's default QUIC transport dropped 31
> times in five minutes, which surfaced as Razorpay reporting "no such host" — an accurate error I
> initially assumed was a misconfiguration on my side. Forcing `--protocol http2` gave zero drops.
> Separately, Docker Desktop's WSL2 backend kept being OOM-killed on 8GB of RAM until I capped it
> with a `.wslconfig`. Neither is interesting engineering, but both cost real hours, and the fix for
> each is now written down in `docs/tunnel.md` so it is not rediscovered five minutes before a demo.
>
> The through-line: every one of these was a case where the code looked right and the tests agreed.
> That is why the invariants that matter now live in Postgres as composite foreign keys, partial
> unique indexes, triggers and check constraints, under one rule — enforce at the lowest layer that
> can hold it, because a rule in application code is a rule a different query walks around.

---

## Before you paste

- The form's fields are plain text. The `**bold**` and `` `code` `` marks above will show as
  literal characters — strip them, or keep them if the field renders Markdown. Check one first.
- Both answers are long on purpose; a form field is cheap and video seconds are not. If you want
  them shorter, cut the infrastructure paragraph from the second answer first — it is the least
  technical of the five.
- The GitHub URL must be a **public** repo. Verify it in a logged-out browser window before you
  submit, not after.
- Set the video to unlisted, not private, and open the link once in a private window to confirm a
  stranger can actually play it.
