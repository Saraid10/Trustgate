"""Render an evidence record as a readable receipt.

This is a pure function of the assembled evidence. It queries nothing and decides nothing, so the
receipt and the JSON record cannot disagree about what happened.

The layout is the argument. Keeping what the agent proposed visually apart from what the server
derived is what makes the authority boundary legible: a reader can see that the price and merchant
were never the agent's to choose. Merging the two columns into one tidy summary would hide exactly
the property the project exists to demonstrate.
"""

from __future__ import annotations

import html

from schemas.domain import PaymentRequestEvidence

_DECISION_TONE = {"ALLOW": "ok", "REQUIRE_APPROVAL": "warn", "DENY": "bad"}


def _money(amount_minor: int, currency: str) -> str:
    symbol = "₹" if currency == "INR" else ""
    return f"{symbol}{amount_minor / 100:,.2f}"


def _text(value: object) -> str:
    if value is None:
        return "&mdash;"
    return html.escape(str(value))


def _rows(pairs: list[tuple[str, str]]) -> str:
    return "".join(
        f"<div class='row'><span class='k'>{html.escape(k)}</span><span class='v'>{v}</span></div>"
        for k, v in pairs
    )


def _stage(title: str, subtitle: str, body: str, *, tone: str = "") -> str:
    return (
        f"<section class='stage {tone}'><h2>{html.escape(title)}</h2>"
        f"<p class='sub'>{html.escape(subtitle)}</p>{body}</section>"
    )


def render_receipt(evidence: PaymentRequestEvidence) -> str:
    """Build the receipt for one purchase attempt."""

    proposed = evidence.proposed
    derived = evidence.derived
    decision = evidence.decision
    tone = _DECISION_TONE.get(decision.decision, "") if decision else ""

    proposed_body = _rows(
        [
            ("SKU", _text(proposed.sku)),
            ("Quantity", _text(proposed.quantity)),
            ("Purpose", _text(proposed.purpose)),
            ("Actor", _text(proposed.actor_id)),
            ("Source", _text(proposed.source)),
            ("Requested", _text(proposed.requested_at)),
        ]
    )
    derived_body = _rows(
        [
            ("Merchant", _text(derived.merchant_display_name)),
            ("Catalog item", _text(derived.catalog_name)),
            ("Amount", f"<strong>{_money(derived.amount_minor, derived.currency)}</strong>"),
            ("Currency", _text(derived.currency)),
            ("Order reference", f"<code>{_text(derived.order_ref)}</code>"),
        ]
    )

    if decision is None:
        decision_body = "<p class='empty'>No authorization decision was recorded.</p>"
    else:
        reasons = ", ".join(decision.reasons) if decision.reasons else "—"
        pairs = [
            ("Decision", f"<strong class='verdict'>{_text(decision.decision)}</strong>"),
            ("Reasons", _text(reasons)),
            ("Policy version", _text(decision.policy_version)),
            ("Decided", _text(decision.decided_at)),
        ]
        if evidence.policy is not None:
            policy = evidence.policy
            pairs += [
                (
                    "Per-payment limit",
                    _money(policy.max_amount_minor, policy.currency),
                ),
                (
                    "Daily limit",
                    _money(policy.max_daily_spend_minor, policy.currency),
                ),
                (
                    "Approval required above",
                    _money(policy.approval_required_above_minor, policy.currency)
                    if policy.approval_required_above_minor is not None
                    else "&mdash;",
                ),
            ]
        if evidence.approval is not None:
            approval = evidence.approval
            pairs += [
                ("Approved by", _text(approval.granted_by)),
                ("Approval consumed", _text(approval.consumed_at)),
            ]
        if evidence.authority is not None:
            authority = evidence.authority
            pairs += [
                ("Authority snapshot", f"<code>{_text(authority.snapshot_hash[:16])}…</code>"),
                ("Authority used", _text(authority.used_at)),
            ]
        decision_body = _rows(pairs)

    # Rendered inside stage two rather than as a fourth stage: a delegation is not a thing that
    # happened after authorization, it is part of what authorized. Absent entirely when the request
    # spent no delegation, so an empty block never suggests that authority was checked and found
    # wanting when in fact none was involved.
    delegation_body = ""
    if evidence.delegation is not None:
        held = evidence.delegation
        currency = derived.currency
        delegation_pairs = [
            ("Granted by", f"<strong>{_text(held.root_actor_id)}</strong>"),
            ("Chain", _text(f"{len(held.chain)} hop{'' if len(held.chain) == 1 else 's'}")),
            ("Debited", _money(held.spent_minor, currency)),
            ("Scope spent", f"<code>{_text(held.spent_sku)}</code>"),
        ]
        if held.released_at is not None:
            delegation_pairs.append(("Returned", _text(held.released_at)))
        if held.refusal_reason is not None:
            delegation_pairs.append(
                (
                    "Refused at checkout",
                    f"<strong class='verdict'>{_text(held.refusal_reason)}</strong>",
                )
            )
        hops = "".join(
            f"<li><span class='muted'>depth {_text(hop.depth)}</span> "
            f"<code>{_text(hop.delegator_actor_id)}</code> &rarr; "
            f"<code>{_text(hop.delegate_actor_id)}</code>"
            f"<br><span class='muted'>{_money(hop.remaining_minor, currency)} left of "
            f"{_money(hop.budget_minor, currency)}"
            f"{' &middot; revoked' if hop.revoked_at is not None else ''}</span></li>"
            for hop in held.chain
        )
        delegation_body = (
            "<h3 class='block'>Delegated authority</h3>"
            + _rows(delegation_pairs)
            + f"<ul class='events'>{hops}</ul>"
        )

    if evidence.provider_order is None:
        provider_body = (
            "<p class='empty'>No provider order exists for this request. "
            "Nothing reached Razorpay.</p>"
        )
    else:
        order = evidence.provider_order
        provider_pairs = [
            ("Provider order", f"<code>{_text(order.razorpay_order_id)}</code>"),
            ("Provider state", _text(order.provider_state)),
            ("Amount sent", _money(order.amount_minor, order.currency)),
            ("Receipt", f"<code>{_text(order.receipt)}</code>"),
        ]
        if evidence.payment is not None:
            provider_pairs.insert(0, ("Payment state", _text(evidence.payment.state)))
        provider_body = _rows(provider_pairs)
        if evidence.provider_events:
            events = "".join(
                f"<li><code>{_text(event.event_type)}</code> "
                f"<span class='muted'>{_text(event.received_at)}</span></li>"
                for event in evidence.provider_events
            )
            provider_body += f"<ul class='events'>{events}</ul>"
        else:
            provider_body += (
                "<p class='empty'>No verified provider events yet, so nothing is captured.</p>"
            )

    audit = (
        "".join(
            f"<li><code>{_text(entry.event_kind)}</code> "
            f"<span class='muted'>{_text(entry.created_at)}</span></li>"
            for entry in evidence.audit_trail
        )
        or "<li class='empty'>No audit entries for this decision.</li>"
    )

    stage_one = _stage("1 · Proposed", "Chosen by the buying agent", proposed_body)
    stage_two = _stage(
        "2 · Derived and authorized",
        "Determined by TrustGate",
        derived_body + decision_body + delegation_body,
        tone=tone,
    )
    stage_three = _stage("3 · Provider outcome", "What Razorpay actually did", provider_body)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TrustGate receipt</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 0;
          background: #f4f7f7; color: #0d1719; line-height: 1.55; }}
  .wrap {{ max-width: 60rem; margin: 0 auto; padding: 2.5rem 1.25rem 4rem; }}
  header {{ margin-bottom: 1.75rem; }}
  h1 {{ font-size: 1.5rem; margin: 0 0 .3rem; }}
  .meta {{ color: #5e7477; font-size: .85rem; font-family: ui-monospace, monospace; }}
  .stages {{ display: grid; gap: 1rem; }}
  @media (min-width: 60rem) {{ .stages {{ grid-template-columns: 1fr 1fr 1fr; }} }}
  .stage {{ background: #fff; border: 1px solid #d2dfdf; border-radius: 12px; padding: 1.25rem; }}
  .stage h2 {{ font-size: .95rem; margin: 0 0 .2rem; }}
  .stage .sub {{ margin: 0 0 1rem; font-size: .78rem; color: #5e7477; }}
  .stage.ok {{ border-left: 4px solid #2c6349; }}
  .stage.warn {{ border-left: 4px solid #8c5a0c; }}
  .stage.bad {{ border-left: 4px solid #973029; }}
  .row {{ display: flex; justify-content: space-between; gap: 1rem; padding: .32rem 0;
          border-bottom: 1px solid #eef3f3; font-size: .87rem; }}
  .row:last-child {{ border-bottom: 0; }}
  .k {{ color: #5e7477; }}
  .v {{ text-align: right; word-break: break-word; }}
  .verdict {{ letter-spacing: .04em; }}
  .block {{ font-size: .82rem; margin: 1.1rem 0 .3rem; text-transform: uppercase;
            letter-spacing: .06em; color: #5e7477; }}
  code {{ font-family: ui-monospace, monospace; font-size: .8rem; }}
  .empty {{ color: #5e7477; font-size: .83rem; margin: .6rem 0 0; }}
  .muted {{ color: #5e7477; }}
  ul.events, ul.audit {{ list-style: none; padding: 0; margin: .75rem 0 0;
                          font-size: .82rem; display: grid; gap: .3rem; }}
  .trail {{ margin-top: 1rem; background: #fff; border: 1px solid #d2dfdf;
            border-radius: 12px; padding: 1.25rem; }}
  .trail h2 {{ font-size: .95rem; margin: 0 0 .75rem; }}
  .note {{ margin-top: 1.5rem; font-size: .8rem; color: #5e7477; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #080f10; color: #e6efef; }}
    .stage, .trail {{ background: #101a1c; border-color: #223436; }}
    .row {{ border-bottom-color: #18262a; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Purchase evidence</h1>
    <div class="meta">request {_text(evidence.payment_request_id)}<br>
      generated {_text(evidence.generated_at)}</div>
  </header>
  <div class="stages">
    {stage_one}
    {stage_two}
    {stage_three}
  </div>
  <div class="trail">
    <h2>Audit trail</h2>
    <ul class="audit">{audit}</ul>
  </div>
  <p class="note">
    A traceable, tenant-scoped evidence receipt, assembled from live records at read time. It is
    not tamper-evident: nothing here is hashed or signed, so it reflects the database as it stands
    rather than proving what it held earlier. A signed snapshot would change that.
  </p>
</div>
</body>
</html>
"""
