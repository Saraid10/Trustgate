# Positioning

Why a project like this is worth building now, and what it does *not* claim.

Everything below about Indian payments infrastructure is **context, not a compliance claim**.
TrustGate is a synthetic-data testbed running in Razorpay Test Mode. It is not certified,
audited, or aligned with any standard, and nothing here should be read as saying otherwise.
`docs/limitations.md` states that plainly.

Each item is labelled with what kind of evidence stands behind it, because those differ, and
treating a press report as though it were a circular is exactly the mistake this page is written
to avoid.

---

## The problem this project exists for

An AI agent that can buy things needs the ability to spend money. The obvious way to build that is
a tool taking an amount and a merchant, because the model has to say what to buy and what it costs.
Nothing about that reads as a vulnerability until untrusted text reaches the model — a product
description, a search result, an email — and the model follows an instruction inside it.

`python -m demo.unguarded` shows that happening in this repository's own code: the same catalog,
the same model response, an adapter with no policy layer, and a payment of INR 20,000 to a merchant
named by the catalog text against a catalog price of INR 600.

TrustGate's answer is not detection. It is that the agent's tool contract has no field an amount
or a merchant could be written into, so those facts are derived server-side and the instruction has
nothing to act on.

---

## Indian payments context

### UPI Circle — delegated payments within set limits

**Evidence: primary.** NPCI circular `UPI-OC-No-201-FY-24-25`, "Introduction of UPI
Circle – Delegated Payments for secondary users", published on npci.org.in, with a later addendum
covering IoT devices and software (`UPI_OC_No_201_B_FY_2025_26`).

UPI Circle lets a primary user delegate payment authority to a secondary user, with or without a
spending limit. That is a shipped, documented Indian payments primitive whose shape is the same one
this project implements: **a principal grants bounded spending authority to a delegate, and the
bound is enforced by the system rather than by the delegate's good behavior.**

TrustGate's delegate happens to be software rather than a family member, and its policy carries a
per-payment cap, a daily cap, and an approval threshold instead of a monthly limit. The structural
claim is the same: authority is delegated, bounded, and enforced centrally.

This is the strongest item on this page, because it is the only one that already exists.

### NPCI's Unified Agent Protocol — reported, not published

**Evidence: press reporting only.** Multiple outlets reported in July 2026 that NPCI is developing
a Unified Agent Protocol (UAP) to register, verify, and authorize AI agents transacting over UPI.
Reporting is consistent in describing it as *under development in consultation with industry* — the
language used is "reportedly developing" and "may allow".

**No NPCI circular, specification, or press release for the UAP was found while writing this page.**
Treat every detail as unconfirmed. If this project is discussed with anyone who works in Indian
payments, they will know more than this page does, and the correct posture is to say so.

What is worth noting regardless of the UAP's final shape: the problem it is reported to address —
*establishing not only who the user is, but whether their agent is legitimate and authorized to
act* — is the problem TrustGate scopes down to a single tenant and solves structurally.

### Human-in-the-loop above a financial threshold — a recommendation

**Evidence: a recommendation in a government report, via press.** CERT-In's Digital Threat Report
2025-26 is reported to recommend: *"Mandate human-in-the-loop controls for agentic AI actions above
defined financial thresholds, with full audit trails."*

It is a recommendation, not a rule. The threshold is not specified in the coverage found. CERT-In
sits under MeitY, and reporting attributes the proposal to both, which is consistent but worth
knowing if the distinction comes up.

The parallel to this project is direct and was not designed for it. TrustGate's policy carries
`approval_required_above_minor`; a purchase above that threshold cannot be completed by the agent
and requires a separate human identity holding a separate token, with the server refusing an
approval whose approver matches the requester. Every step of that is written to a tenant-scoped
audit trail.

That is "human-in-the-loop above a financial threshold, with full audit trails" — arrived at
because it is the right shape for delegated spending authority, not because a report asked for it.

---

## What this project is claiming

- The money-critical facts of a purchase can be made structurally unreachable by an agent, rather
  than defended by filters that recognize attacks.
- That property can be **verified** rather than asserted: 16 adversarial scenarios with a generated
  attack matrix, and 18 deliberate breaks of the safety code that each require their guarding tests
  to fail.
- Authority can be short-lived, single-use, and bound to the exact purchase it was issued for, so
  that a change to the policy or the purchase invalidates it.

## What it is not claiming

- Not compliance with any standard, framework, or proposed protocol.
- Not an implementation of UPI Circle, the UAP, or any CERT-In recommendation. The resemblances
  above are structural, not conformance.
- Not production-ready. `docs/limitations.md` names every cut, starting with the fact that tenant
  identity is a header rather than authentication.

---

## Sources

- [NPCI circular UPI-OC-No-201-FY-24-25 — Introduction of UPI Circle, Delegated Payments for
  secondary users](https://www.npci.org.in/PDF/npci/upi/circular/2024/UPI-OC-No-201-FY-24-25-Introduction-of-UPI%20Circle%E2%80%93Delegated-Payments-for-secondary-users.pdf)
- [NPCI addendum — IoT devices and software on UPI
  Circle](https://www.npci.org.in/uploads/UPI_OC_No_201_B_FY_2025_26_Addendum_to_NPCI_UPI_2024_25_OC_201_Introduction_of_Io_T_devices_software_on_UPI_Circle_09ec83c893.pdf)
- [Business Standard — India may allow agentic AI-led UPI transactions under new NPCI
  protocol](https://www.business-standard.com/finance/news/india-may-allow-agentic-ai-led-upi-transactions-under-new-npci-protocol-126070801343_1.html)
- [MediaNama — How NPCI should approach agentic
  payments](https://www.medianama.com/2026/07/223-npci-agentic-payments-upi/)
- [MediaNama — MeitY proposes mandatory human-in-the-loop interventions in agentic AI
  payments](https://www.medianama.com/2026/07/223-meity-proposes-mandatory-human-interventions-agentic-ai-payments/)

Press items are cited as press. Verify anything on this page against a primary source before
repeating it, and re-check the UAP's status before submission — it was under development when this
was written and may have moved.
