"""Serve the Razorpay Standard Checkout page for an order the server already created.

This page renders; it does not authorize. Loading it consumes no checkout authority and creates no
provider order, because both already happened through the authenticated order route. That
separation is deliberate: a page a browser can request must never be able to move money.

Everything the page carries is public by design. `razorpay_key_id` is the publishable key that
Razorpay Checkout requires in the browser; the key secret, the webhook secret, and every internal
identifier stay on the server. A test asserts the rendered page contains no secret.
"""

from __future__ import annotations

import html
import json
import os
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_session
from models.domain import CheckoutAuthority, PaymentRequest, RazorpayOrder

router = APIRouter(prefix="/api/v1/razorpay", tags=["razorpay test mode"])

_CHECKOUT_SCRIPT = "https://checkout.razorpay.com/v1/checkout.js"
_RAZORPAY_ORIGINS = "https://checkout.razorpay.com https://api.razorpay.com"


def _content_security_policy(nonce: str) -> str:
    """Defence in depth behind the escaping, not instead of it.

    Scripts run only with this request's nonce or from Razorpay's own origin, so text that
    somehow reached the document as markup still has no way to execute. Styles keep
    `unsafe-inline` because Razorpay Checkout injects its own; tightening that would break the
    payment flow without closing the hole this defends.
    """

    return (
        "default-src 'self'; "
        f"script-src 'nonce-{nonce}' {_RAZORPAY_ORIGINS}; "
        f"frame-src {_RAZORPAY_ORIGINS}; "
        f"connect-src 'self' {_RAZORPAY_ORIGINS}; "
        "img-src 'self' data: https:; "
        "style-src 'self' 'unsafe-inline'; "
        "base-uri 'none'; object-src 'none'; form-action 'none'; frame-ancestors 'none'"
    )


def _rupees(amount_minor: int) -> str:
    return f"{amount_minor / 100:,.2f}"


# Characters that can terminate or reopen a script element. `json.dumps` emits them verbatim,
# so catalog text carrying `</script>` would close the element and let whatever follows run.
# The line and paragraph separators are included because JavaScript treats them as line
# terminators inside string literals even though JSON does not.
_SCRIPT_ESCAPES = {
    ord("<"): "\\u003c",
    ord(">"): "\\u003e",
    ord("&"): "\\u0026",
    0x2028: "\\u2028",
    0x2029: "\\u2029",
}


# Obviously fake, and valid enough for the checkout form to accept. A repeated-digit number is
# rejected by the form's own validation, which is how the first choice here failed.
SYNTHETIC_CONTACT = "9876543210"
SYNTHETIC_EMAIL = "demo@example.invalid"


def _script_json(value: object) -> str:
    """Serialise for embedding inside a `<script>` block.

    Catalog text is the untrusted content this project exists to contain, which makes the
    browser the last place to relax about it. Escaping to unicode form leaves the value
    identical to JavaScript while making it unable to terminate the element.
    """

    return json.dumps(value).translate(_SCRIPT_ESCAPES)


def _render(
    *, key_id: str, order: RazorpayOrder, request: PaymentRequest | None, nonce: str
) -> str:
    """Build the checkout page.

    Server-derived values are still escaped for HTML and encoded through `json.dumps` for the
    script block. Catalog text sits beside a column this project treats as untrusted, and the
    project's whole position is that content of that provenance is data rather than markup.
    """

    sku = html.escape(request.catalog_sku or "-") if request else "-"
    name = html.escape(request.catalog_name or "Purchase") if request else "Purchase"
    merchant = html.escape(request.merchant_display_name or "-") if request else "-"
    quantity = request.quantity if request and request.quantity is not None else 1
    purpose = html.escape(request.purpose or "-") if request else "-"
    amount = _rupees(order.amount_minor)
    options = _script_json(
        {
            "key": key_id,
            "order_id": order.razorpay_order_id,
            "amount": order.amount_minor,
            "currency": order.currency,
            "name": "TrustGate",
            "description": request.catalog_name if request else "Purchase",
            # Synthetic, and prefilled deliberately. Test Mode sends nothing to either of these,
            # and leaving the fields blank asks whoever is demonstrating to type a real phone
            # number into a page that is being recorded. The project's rule against putting real
            # customer data through this demonstration applies to the person running it too.
            "prefill": {"contact": SYNTHETIC_CONTACT, "email": SYNTHETIC_EMAIL},
            "theme": {"color": "#0d6b67"},
        }
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TrustGate checkout</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 0;
          background: #f4f7f7; color: #0d1719; }}
  .wrap {{ max-width: 34rem; margin: 0 auto; padding: 2.5rem 1.25rem; }}
  .card {{ background: #fff; border: 1px solid #d2dfdf; border-radius: 12px; padding: 1.5rem; }}
  h1 {{ font-size: 1.3rem; margin: 0 0 .35rem; }}
  .sub {{ color: #5e7477; font-size: .9rem; margin: 0 0 1.25rem; }}
  dl {{ display: grid; grid-template-columns: auto 1fr; gap: .5rem 1rem; margin: 0 0 1.25rem; }}
  dt {{ color: #5e7477; font-size: .85rem; }}
  dd {{ margin: 0; font-size: .92rem; text-align: right; }}
  .total {{ font-size: 1.4rem; font-weight: 600; }}
  button {{ width: 100%; padding: .85rem; font-size: 1rem; font-weight: 600; cursor: pointer;
            background: #0d6b67; color: #fff; border: 0; border-radius: 8px; }}
  button:disabled {{ opacity: .55; cursor: not-allowed; }}
  #status {{ margin-top: 1rem; font-size: .9rem; min-height: 1.4rem; }}
  .ok {{ color: #2c6349; }}
  .bad {{ color: #973029; }}
  .note {{ margin-top: 1.5rem; font-size: .8rem; color: #5e7477; line-height: 1.5; }}
  code {{ font-family: ui-monospace, monospace; font-size: .82rem; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #080f10; color: #e6efef; }}
    .card {{ background: #101a1c; border-color: #223436; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <h1>{name}</h1>
    <p class="sub">Sold by {merchant}</p>
    <dl>
      <dt>SKU</dt><dd><code>{sku}</code></dd>
      <dt>Quantity</dt><dd>{quantity}</dd>
      <dt>Purpose</dt><dd>{purpose}</dd>
      <dt>Amount</dt><dd class="total">&#8377;{amount}</dd>
    </dl>
    <button id="pay">Pay &#8377;{amount}</button>
    <div id="status" role="status" aria-live="polite"></div>
    <p class="note">
      The amount above was derived by TrustGate from the catalog, not supplied by the buying agent.
      Completing this page is not proof of payment: the browser result is verified server-side, and
      only a signed provider event moves the payment to captured.
    </p>
  </div>
</div>
<script nonce="{nonce}" src="{_CHECKOUT_SCRIPT}"></script>
<script nonce="{nonce}">
(function () {{
  var statusEl = document.getElementById("status");
  var payButton = document.getElementById("pay");
  var options = {options};

  function show(message, cls) {{
    statusEl.textContent = message;
    statusEl.className = cls || "";
  }}

  options.handler = function (response) {{
    // The browser returning is not the verdict. Ask the server whether the signature holds.
    show("Verifying with the server...");
    payButton.disabled = true;
    fetch("/api/v1/razorpay/callback", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{
        razorpay_payment_id: response.razorpay_payment_id,
        razorpay_order_id: response.razorpay_order_id,
        razorpay_signature: response.razorpay_signature
      }})
    }})
      .then(function (r) {{
        return r.json().then(function (b) {{ return {{ status: r.status, body: b }}; }});
      }})
      .then(function (result) {{
        if (result.status === 202) {{
          show("Signature verified. Awaiting the signed provider event before capture.", "ok");
        }} else {{
          show("Server rejected the callback: " + (result.body.detail || result.status), "bad");
        }}
      }})
      .catch(function () {{ show("Could not reach the server to verify.", "bad"); }});
  }};
  options.modal = {{
    ondismiss: function () {{ show("Checkout closed. Nothing was charged."); }}
  }};

  var rzp = new Razorpay(options);
  rzp.on("payment.failed", function (response) {{
    show(
      "Payment attempt failed. You may retry: " +
      (response.error && response.error.description),
      "bad"
    );
  }});
  payButton.onclick = function (event) {{
    event.preventDefault();
    show("");
    rzp.open();
  }};
}})();
</script>
</body>
</html>
"""


@router.get("/checkout/{razorpay_order_id}", response_class=HTMLResponse)
async def render_checkout_page(
    razorpay_order_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HTMLResponse:
    """Render Standard Checkout for an order that already exists.

    Only a confirmed order renders. A pending intent has no provider order to pay against, and
    serving a page for one would invite a payment attempt for something that may not exist.
    """

    order = await session.scalar(
        select(RazorpayOrder).where(
            RazorpayOrder.razorpay_order_id == razorpay_order_id,
            RazorpayOrder.provider_state == "CONFIRMED",
        )
    )
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="RAZORPAY_ORDER_NOT_FOUND"
        )
    key_id = os.getenv("RAZORPAY_KEY_ID")
    if not key_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RAZORPAY_CREDENTIALS_UNAVAILABLE",
        )
    purchase = await session.scalar(
        select(PaymentRequest)
        .join(
            CheckoutAuthority,
            (CheckoutAuthority.payment_request_id == PaymentRequest.id)
            & (CheckoutAuthority.tenant_id == PaymentRequest.tenant_id),
        )
        .where(
            CheckoutAuthority.id == order.checkout_authority_id,
            CheckoutAuthority.tenant_id == order.tenant_id,
        )
    )
    nonce = secrets.token_urlsafe(16)
    return HTMLResponse(
        content=_render(key_id=key_id, order=order, request=purchase, nonce=nonce),
        headers={"Content-Security-Policy": _content_security_policy(nonce)},
    )
