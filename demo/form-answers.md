# Submission form — paste-ready answers

Three rules these follow, because a form is not a document:

- **No Markdown.** Google Forms renders none of it, so there is no bold and no backticks below.
- **No em dashes.** They are a tell. Commas, colons and full stops instead.
- **No hard line wrapping.** Each paragraph is one long line, so the textarea wraps it itself. Wrap
  it yourself and it pastes in ragged, with breaks in the middle of sentences.

| Field | Answer |
|---|---|
| Project name | TrustGate |
| GitHub Repository URL | https://github.com/Saraid10/Trustgate |
| 5-min Pitch Video Link | *(yours, once uploaded, unlisted rather than private)* |

The video does not repeat any of this. `pitch.md` spends its time on architecture and on things
happening on screen; these two fields carry the prose, because prose is what the form asked for.

---

## Project Objectives — what does it solve?

```
AI agents are starting to get the ability to spend money, and the obvious way to build that is a payment tool that takes an amount and a merchant. That is unsafe for a reason no better model fixes. Agents read untrusted text all day (product descriptions, emails, web pages) and following instructions in text is what a language model does. If your tool has an amount field, injected text can fill it.

I did not want to just claim that, so the repo demonstrates it. demo/unguarded.py takes one model response and hands it to two payment adapters. Against a plain provider adapter, a single hidden instruction in a supplier's product description moves ₹20,000 to an attacker-named merchant. The actual catalogue price was ₹600.

TrustGate sits between the agent and the payment provider. The agent can propose three things: a catalogue SKU, a quantity, and a purpose. Price, merchant and currency all get derived server side, so the agent never sees them and cannot supply them. There is also no tool anywhere on the surface that can authorize, capture, refund or call the provider, and I assert that by actually exercising every exposed tool rather than by reading tool names.

Four specific things it fixes:

1. Amount and merchant tampering. Structurally impossible instead of validated, because there is no field for the injected text to land in. Nothing gets detected, filtered or scored. The instruction just has nowhere to go.

2. "Allowed to buy" being treated as the same thing as "able to pay". Here they are separate states. A purchase can sit at AUTHORIZED forever with order creation still refused. To actually pay, you need a separate checkout authority that is single use, bound to a hash of that exact purchase, expires in fifteen minutes, and gets revoked if the policy it was checked against is superseded.

3. Delegated spending between agents. A human funds an agent, that agent funds another one, and every hop narrows. The important part is that an agent's children cannot, between them, promise more than it holds. Revoking one hop kills every branch below it without recalling anything, because authority gets re-derived from the whole chain on every spend and again at checkout.

4. Where the money movement actually comes from. Only a signed provider event captures. The browser's own success callback gets verified and then deliberately ignored, because the browser is the buyer's machine. There is a real Razorpay-delivered payment.captured event committed in the repo as proof.

All of it is tenant scoped and audited. Every decision writes a reason code in plain words, and the receipt shows three columns side by side: what the agent proposed, what the server derived, and what the provider actually did.

On scope I would rather be straight: Razorpay Test Mode only, tenant identity is a header and not real auth, and there is no per-agent identity yet. It is all written up in docs/limitations.md.
```

---

## Build Challenges & Technical Obstacles

```
The thing that shaped this project most: my tests passed while the code was wrong, twice.

First time, request-scoped database sessions were silently throwing away every write and 146 tests passed anyway, because each test was asserting inside the same transaction it had written in. Second time, I deleted the check that stops an expired spending policy from authorising payments, just to see what would happen. 302 tests passed. Two clean code reviews had already gone over that file.

A green suite tells you the code does what you wrote. It does not tell you your tests would complain if the code stopped doing something important, and when the subject is money only the second one matters. So I wrote a mutation harness. It deletes each of my 53 safety guards one at a time, runs the tests that are supposed to protect that guard, and fails if they still pass. All 53 get caught now. One of them survived at first and it turned out the line was genuinely unreachable, so I deleted it instead of keeping code that looks like care and does nothing.

The delegation problem took me the longest. When I built agent-to-agent delegation I followed every capability standard I could find, where a child's authority is the intersection of what it asks for and what the parent holds, so a child can never be wider than its parent. That is correct for permissions and it is wrong for money, because quantities add up and sets do not. A parent holding ₹2,000 that has already promised ₹1,200 to one child will happily give ₹1,200 to a second one. Each edge narrowed correctly. Between them the two children now hold more than the parent ever had. The fix was to partition the budget instead of comparing it, with a CHECK constraint on the parent's own row requiring the sum of live child budgets to stay inside it. I put it in the database specifically because I had already proved I could not trust my own module: I managed to write three over-committed sibling budgets straight past my own validation code and every check in it passed.

Then there was a lock that was serialising the wrong thing. I had a FOR UPDATE row lock that I was sure was protecting payment state under concurrency. It queued callers correctly and then handed the second one stale data out of SQLAlchemy's identity map, so it approved a payment that had just been approved. It was serialising when things ran, not what they decided from. Fixed with populate_existing=True on the locked read, and I added nine tests that drive actually concurrent sessions instead of pretending.

There was also a refusal that left money moved. Wiring delegation into the payment path introduced a leak I did not spot for a while. A request claims the actor's daily budget, then claims the delegation budget, and if that second claim refuses, the first one stays claimed for a payment that never happens. The release path only fires on a transition out of a holding state, which a denied request never enters, and swapping the order just moves the bug somewhere else. Both claims go inside one savepoint now, so either both hold or neither does, and the order stops mattering.

Infrastructure wasted more hours than any of the above. Razorpay could not reach my machine for webhook delivery. ngrok's free tier was unusable, and cloudflared's default QUIC transport dropped 31 times in five minutes, which showed up on Razorpay's side as "no such host". That error was completely accurate and I spent a while assuming it was my own misconfiguration. Forcing --protocol http2 gave me zero drops. Separately, Docker Desktop's WSL2 backend kept getting OOM killed on 8GB of RAM until I capped it with a .wslconfig. Not interesting engineering, but both cost me real time, so both fixes are written down now instead of being rediscovered five minutes before a demo.

Looking back, every one of these was the same shape: the code looked right and the tests agreed with it. That is why the invariants that actually matter now live in Postgres as composite foreign keys, partial unique indexes, triggers and check constraints. The rule I ended up with is to enforce at the lowest layer that can hold it, because a rule in application code is a rule a different query can walk around.
```

---

## Before you submit

- Open https://github.com/Saraid10/Trustgate in a logged-out or private browser window. If the repo
  is private, the URL field is worth nothing, and that failure is silent until after the deadline.
- Set the video to unlisted rather than private, and open its link in a private window too.
- If a field rejects the length, cut the infrastructure paragraph from the second answer first. It
  is the least technical of the five.
