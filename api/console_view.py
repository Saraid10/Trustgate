"""Render a tenant's purchase attempts as one reviewable timeline.

The receipt answers "what happened to this purchase". This answers the question a viewer actually
arrives with: "what has this agent been trying, and did the system hold". Those need different
shapes. A receipt is deep and singular; a timeline is shallow and comparative, and the comparison
is the point - a safe purchase, an approval-gated one, and a refused attack sitting in one column
so the difference between them is visible without reading three pages.

This deliberately does not re-render the three stages. `api.receipt.render_receipt` already lays
out proposed against derived against provider outcome, and a second renderer assembling the same
facts could disagree with the first. The timeline links to that receipt instead.

Like the receipt, this is a pure function of data it is handed. It queries nothing, so the console
cannot show a state the evidence record would contradict.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from api.reason_text import PROGRESSED_REASONS

# A decision's tone is its own, but an outcome can override it. A request that was allowed and then
# refused by the provider is not a green row, and colouring it by the decision alone would tell a
# viewer the opposite of what happened.
_DECISION_TONE = {"ALLOW": "ok", "REQUIRE_APPROVAL": "warn", "DENY": "bad"}

# States from which no provider order will ever follow. A payment here was refused or expired,
# so "nothing reached Razorpay" is the end of its story rather than a stage in it.
_SETTLED_WITHOUT_PROVIDER = frozenset({"DENIED", "EXPIRED", "FAILED", "CANCELLED"})


@dataclass(frozen=True)
class ConsoleHeadline:
    """The most recent attempt, said once and said large.

    The timeline answers "what has been tried". This answers "where does the newest thing stand",
    which is the question someone watching over a shoulder is actually asking, and which they were
    previously answering by reading the top row of a five-column table.

    Assembled from the same evidence record the receipt renders, not from a second set of queries.
    A banner that disagreed with the receipt it sits above would be worse than no banner.
    """

    verdict: str
    """AUTHORIZED, APPROVAL REQUIRED, or REFUSED. Not the raw decision: `REQUIRE_APPROVAL` that a
    human has since granted is `AUTHORIZED` here, because that is what is true now.

    REFUSED rather than BLOCKED, deliberately. Nothing here intercepts an attack: the amount and
    merchant fields do not exist to be filled, and what survives is declined by an ordinary bound.
    "Blocked" is firewall vocabulary and implies a detector this system does not have - and the
    timeline row under this banner has always said REFUSED, so the banner now agrees with it."""

    tone: str
    reasons: tuple[str, ...]
    provider_action_allowed: bool
    provider_action_blocked_reason: str | None
    delegation_root_actor_id: str | None
    delegation_remaining_minor: int | None
    currency: str
    has_payment_request: bool
    """False for an attack refused at the tool boundary. There is no request, no amount, and no
    receipt - which is the strongest thing this system does and the easiest thing to render as an
    empty panel if nobody says so on purpose."""

    provider_action_blocked_code: str | None = None
    """The raw reason code, kept beside the humanised sentence purely so the panel can tell a
    refusal from a completion. Matching on the English would break the first time it is reworded.

    Last and defaulted so every existing constructor keeps working; the panel falls back to the
    refusal rendering when it is absent, which is the safe direction to be wrong in.
    """


@dataclass(frozen=True)
class ConsoleEntry:
    """One purchase attempt, flattened to what a reviewer needs at a glance.

    A view model rather than a wire contract, so it lives here instead of in `schemas.domain`.
    Nothing serialises it; the only consumer is the renderer below.
    """

    payment_request_id: UUID | None
    requested_at: datetime
    actor_id: str
    source: str
    sku: str | None
    quantity: int | None
    purpose: str | None
    merchant_display_name: str | None
    amount_minor: int | None
    currency: str
    decision: str | None
    reasons: tuple[str, ...]
    approval_granted_by: str | None
    # The human whose authority the purchase ran under, or None because most purchases run under
    # nobody's in particular. On this screen it is the difference between "an agent bought
    # something" and "an agent spent a budget a named person handed it", which is the whole claim
    # delegation makes and the one a reviewer scanning rows would otherwise never see.
    delegation_root_actor_id: str | None
    payment_state: str | None
    provider_order_id: str | None
    provider_state: str | None

    @property
    def reached_provider(self) -> bool:
        """Whether anything about this attempt was ever sent to Razorpay.

        The most important fact on a refused row, and the one a viewer is least likely to take on
        trust. A rejection that still created a provider order would not be a rejection.
        """

        return self.provider_order_id is not None

    @property
    def refused_at_the_boundary(self) -> bool:
        """Whether the attempt was turned away before a payment request existed at all.

        The strongest outcome the system produces, and the easiest to lose: an attack refused at
        the MCP boundary leaves an audit event and no payment request, so a timeline built only
        from requests would show nothing where the most important row belongs.
        """

        return self.payment_request_id is None

    @property
    def awaiting_checkout(self) -> bool:
        """Authorized, with nothing sent to the provider - and still able to be.

        The state the whole design exists to produce: the agent obtained an authorization and did
        not obtain the ability to pay. It reads identically to a refusal unless it is named
        separately, which is why it is a property rather than an inline condition.
        """

        return (
            not self.refused_at_the_boundary
            and not self.reached_provider
            and self.payment_state is not None
            and self.payment_state not in _SETTLED_WITHOUT_PROVIDER
        )

    @property
    def tone(self) -> str:
        if self.decision is None:
            return ""
        if self.decision == "REQUIRE_APPROVAL":
            # Amber while a human has not acted, green once one has. Reading this from the granted
            # approval rather than from the payment state says what actually happened: an approval
            # is a human decision, and the payment moving is its consequence.
            return "ok" if self.approval_granted_by is not None else "warn"
        return _DECISION_TONE.get(self.decision, "")


def _money(amount_minor: int, currency: str) -> str:
    symbol = "₹" if currency == "INR" else ""
    return f"{symbol}{amount_minor / 100:,.2f}"


def _text(value: object) -> str:
    if value is None:
        return "&mdash;"
    return html.escape(str(value))


def _outcome_cell(entry: ConsoleEntry) -> str:
    """The third column, which has three answers and used to give two of them the same words.

    A refusal and an authorization that has not been taken to checkout are entirely different
    facts, and both rendered as "Nothing reached Razorpay". On screen that made a working purchase
    indistinguishable from a blocked one, and it contradicted the sentence the demo needs to say
    while pointing at it: that the agent obtained an authorization and not the ability to pay.
    """

    if entry.refused_at_the_boundary:
        return (
            "<span class='never'>Nothing reached Razorpay</span>"
            "<span class='muted'>no payment request was created</span>"
        )
    if entry.awaiting_checkout:
        return (
            "<span class='pending'>Not sent to Razorpay yet</span>"
            f"<span class='muted'>payment {_text(entry.payment_state)}"
            " &middot; a human completes checkout</span>"
        )
    if not entry.reached_provider:
        return (
            "<span class='never'>Nothing reached Razorpay</span>"
            f"<span class='muted'>payment {_text(entry.payment_state)}</span>"
        )
    return (
        f"<code>{_text(entry.provider_order_id)}</code>"
        f"<span class='muted'>{_text(entry.provider_state)}"
        f" &middot; payment {_text(entry.payment_state)}</span>"
    )


def _verdict_cell(entry: ConsoleEntry) -> str:
    if entry.decision is None:
        return "<span class='muted'>No decision recorded</span>"
    reasons = ", ".join(entry.reasons) if entry.reasons else ""
    parts = [f"<strong>{_text(entry.decision)}</strong>"]
    parts.append(
        f"<span class='amount'>{_money(entry.amount_minor, entry.currency)}</span>"
        if entry.amount_minor is not None
        # Refused before the server derived one. Saying so is stronger than showing a blank: the
        # attack did not get as far as having a price.
        else "<span class='muted'>no amount was derived</span>"
    )
    if reasons:
        parts.append(f"<span class='reasons'>{_text(reasons)}</span>")
    if entry.approval_granted_by is not None:
        parts.append(f"<span class='muted'>approved by {_text(entry.approval_granted_by)}</span>")
    if entry.delegation_root_actor_id is not None:
        parts.append(
            f"<span class='muted'>under authority from "
            f"{_text(entry.delegation_root_actor_id)}</span>"
        )
    return "".join(parts)


def _proposed_cell(entry: ConsoleEntry) -> str:
    sku = f"<code>{_text(entry.sku)}</code>" if entry.sku else "<span class='muted'>no SKU</span>"
    quantity = f" &times;{_text(entry.quantity)}" if entry.quantity is not None else ""
    return (
        f"{sku}{quantity}"
        f"<span class='purpose'>{_text(entry.purpose)}</span>"
        f"<span class='muted'>{_text(entry.actor_id)} &middot; {_text(entry.source)}</span>"
    )


def _row(entry: ConsoleEntry, *, receipt_href: str | None) -> str:
    link = (
        f"<a href='{html.escape(receipt_href, quote=True)}'>Receipt</a>"
        if receipt_href is not None
        else "<span class='muted'>no receipt</span>"
    )
    return (
        f"<tr class='{entry.tone}'>"
        f"<td class='when'>{_text(entry.requested_at.strftime('%H:%M:%S'))}</td>"
        f"<td class='proposed'>{_proposed_cell(entry)}</td>"
        f"<td class='verdict'>{_verdict_cell(entry)}</td>"
        f"<td class='outcome'>{_outcome_cell(entry)}</td>"
        f"<td class='link'>{link}</td>"
        "</tr>"
    )


def _headline_panel(headline: ConsoleHeadline | None) -> str:
    """The verdict, why, and whether money may move - in that order, because that is the order
    someone reads them in and the order they matter in."""

    if headline is None:
        return ""
    reasons = (
        "".join(f"<li>{_text(reason)}</li>" for reason in headline.reasons)
        or "<li class='muted'>No reason was recorded.</li>"
    )
    if headline.provider_action_allowed:
        provider = "<span class='yes'>Order creation allowed: Yes</span>"
    elif headline.provider_action_blocked_code in PROGRESSED_REASONS:
        provider = (
            "<span class='done'>Order creation allowed: No longer needed</span>"
            f"<span class='muted'>{_text(headline.provider_action_blocked_reason)}</span>"
        )
    else:
        provider = (
            "<span class='no'>Order creation allowed: No</span>"
            f"<span class='muted'>{_text(headline.provider_action_blocked_reason)}</span>"
        )
    authority = ""
    if headline.delegation_root_actor_id is not None:
        remaining = (
            _money(headline.delegation_remaining_minor, headline.currency)
            if headline.delegation_remaining_minor is not None
            else "&mdash;"
        )
        authority = (
            f"<div class='authority'><span class='muted'>Delegated by</span> "
            f"<strong>{_text(headline.delegation_root_actor_id)}</strong>"
            f"<span class='muted'>{remaining} left on the chain</span></div>"
        )
    # Said explicitly rather than shown as an empty panel. An attack turned away before a payment
    # request exists produces no amount and no receipt, and that absence is the safety property -
    # a blank space says "we have not loaded it yet".
    nothing_written = (
        "<div class='authority'><span class='muted'>No payment request was created, so there is "
        "nothing to write a receipt about.</span></div>"
        if not headline.has_payment_request
        else ""
    )
    return (
        f"<section class='headline {headline.tone}'>"
        f"<p class='l'>Current decision</p>"
        f"<p class='verdict'>{_text(headline.verdict)}</p>"
        f"<ul class='why'>{reasons}</ul>"
        f"<div class='provider'>{provider}</div>"
        f"{authority}{nothing_written}"
        "</section>"
    )


def render_console(
    *,
    tenant_id: UUID,
    tenant_name: str,
    entries: list[ConsoleEntry],
    receipt_href: str,
    generated_at: datetime,
    headline: ConsoleHeadline | None = None,
) -> str:
    """Build the timeline page for one tenant.

    `receipt_href` is a format string taking `payment_request_id`, supplied by the route so this
    module never has to know how the application is mounted.
    """

    if entries:
        rows = "".join(
            _row(
                entry,
                receipt_href=(
                    receipt_href.format(payment_request_id=entry.payment_request_id)
                    if entry.payment_request_id is not None
                    else None
                ),
            )
            for entry in entries
        )
        body = f"""<table>
<thead><tr>
  <th>Time</th>
  <th>Agent proposed<span>every field here is agent-influenced</span></th>
  <th>TrustGate derived and decided<span>no field here is the agent's to set</span></th>
  <th>Provider outcome<span>what Razorpay actually did</span></th>
  <th></th>
</tr></thead>
<tbody>{rows}</tbody>
</table>"""
    else:
        body = (
            "<p class='empty'>No purchase attempts recorded for this tenant yet. "
            "Run the demo flows and reload.</p>"
        )

    blocked = sum(
        1 for entry in entries if entry.decision == "DENY" or entry.refused_at_the_boundary
    )
    awaiting = sum(1 for entry in entries if entry.awaiting_checkout)
    reached = sum(1 for entry in entries if entry.reached_provider)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TrustGate console</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 0;
          background: #f4f7f7; color: #0d1719; line-height: 1.5; }}
  .wrap {{ max-width: 78rem; margin: 0 auto; padding: 2.5rem 1.25rem 4rem; }}
  header {{ margin-bottom: 1.5rem; }}
  h1 {{ font-size: 1.5rem; margin: 0 0 .3rem; }}
  .meta {{ color: #5e7477; font-size: .85rem; font-family: ui-monospace, monospace; }}
  .headline {{ background: #fff; border: 1px solid #d2dfdf; border-radius: 12px;
               padding: 1.5rem 1.5rem 1.25rem; margin: 1.25rem 0; }}
  .headline.ok {{ border-left: 6px solid #2c6349; }}
  .headline.warn {{ border-left: 6px solid #8c5a0c; }}
  .headline.bad {{ border-left: 6px solid #973029; }}
  .headline .l {{ margin: 0; font-size: .75rem; letter-spacing: .1em; text-transform: uppercase;
                  color: #5e7477; }}
  .headline .verdict {{ margin: .1rem 0 .6rem; font-size: 2.4rem; font-weight: 700;
                        letter-spacing: -.01em; line-height: 1.1; }}
  .headline .why {{ list-style: none; padding: 0; margin: 0 0 .9rem; font-size: .95rem;
                    display: grid; gap: .2rem; color: inherit; }}
  .headline .provider {{ font-size: 1rem; font-weight: 600; display: flex; gap: .6rem;
                         align-items: baseline; flex-wrap: wrap; }}
  .headline .provider .yes {{ color: #2c6349; }}
  .headline .provider .no {{ color: #973029; }}
  .headline .provider .done {{ color: #2c6349; }}
  .headline .authority {{ margin-top: .55rem; font-size: .9rem; display: flex; gap: .5rem;
                          align-items: baseline; flex-wrap: wrap; }}
  .headline .muted {{ font-weight: 400; }}
  .tally {{ display: flex; gap: 1.5rem; margin: 1.25rem 0 1.75rem; flex-wrap: wrap; }}
  .tally div {{ background: #fff; border: 1px solid #d2dfdf; border-radius: 12px;
                padding: .75rem 1.1rem; min-width: 8rem; }}
  .tally .n {{ font-size: 1.5rem; font-weight: 600; display: block; }}
  .tally .l {{ font-size: .78rem; color: #5e7477; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff;
           border: 1px solid #d2dfdf; border-radius: 12px; overflow: hidden; }}
  th {{ text-align: left; font-size: .78rem; padding: .8rem .9rem; vertical-align: top;
        border-bottom: 1px solid #d2dfdf; color: #0d1719; }}
  th span {{ display: block; font-weight: 400; color: #5e7477; font-size: .72rem;
             margin-top: .15rem; }}
  td {{ padding: .8rem .9rem; border-bottom: 1px solid #eef3f3; font-size: .85rem;
        vertical-align: top; }}
  tr:last-child td {{ border-bottom: 0; }}
  td.proposed code, td.outcome code {{ font-family: ui-monospace, monospace; font-size: .8rem; }}
  td span, td strong {{ display: block; }}
  .purpose {{ color: #0d1719; margin-top: .15rem; }}
  .muted {{ color: #5e7477; font-size: .78rem; margin-top: .15rem; }}
  .amount {{ font-weight: 600; margin-top: .1rem; }}
  .reasons {{ color: #973029; font-size: .78rem; margin-top: .15rem; }}
  .never {{ color: #2c6349; font-weight: 600; }}
  .pending {{ color: #8c5a0c; font-weight: 600; }}
  tr.ok td:first-child {{ box-shadow: inset 3px 0 0 #2c6349; }}
  tr.warn td:first-child {{ box-shadow: inset 3px 0 0 #8c5a0c; }}
  tr.bad td:first-child {{ box-shadow: inset 3px 0 0 #973029; }}
  td.when {{ font-family: ui-monospace, monospace; color: #5e7477; white-space: nowrap; }}
  td.link a {{ color: #2c6349; }}
  .empty {{ color: #5e7477; background: #fff; border: 1px solid #d2dfdf;
            border-radius: 12px; padding: 1.5rem; }}
  .note {{ margin-top: 1.5rem; font-size: .8rem; color: #5e7477; max-width: 46rem; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #080f10; color: #e6efef; }}
    table, .tally div, .empty, .headline {{ background: #101a1c; border-color: #223436; }}
    .headline .provider .yes {{ color: #6fbf95; }}
    .headline .provider .no {{ color: #e0847c; }}
    .headline .provider .done {{ color: #6fbf95; }}
    th {{ border-color: #223436; color: #e6efef; }}
    td {{ border-color: #162426; }}
    .purpose {{ color: #e6efef; }}
    .never {{ color: #6fd0a1; }}
    .pending {{ color: #e0b168; }}
    .reasons {{ color: #ef9a92; }}
    td.link a {{ color: #6fd0a1; }}
  }}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>{_text(tenant_name)}</h1>
  <p class="meta">tenant {_text(tenant_id)} &middot; generated {_text(generated_at)}</p>
</header>
{_headline_panel(headline)}
<div class="tally">
  <div><span class="n">{len(entries)}</span><span class="l">attempts</span></div>
  <div><span class="n">{blocked}</span><span class="l">refused</span></div>
  <div><span class="n">{awaiting}</span><span class="l">authorized, not yet paid</span></div>
  <div><span class="n">{reached}</span><span class="l">reached the provider</span></div>
</div>
{body}
<p class="note">Read-only. This view cannot authorize, approve, or create anything &mdash; it
renders rows that already exist. The amount, merchant, and currency in the middle column were
derived by the server and were never the agent's to choose.</p>
</div>
</body>
</html>"""
