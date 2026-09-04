"""Say a reason code in words a person who has not read this codebase can act on.

Reason codes are the right thing to store and the wrong thing to show. `DELEGATION_BUDGET_EXHAUSTED`
is exact, stable, and safe to assert on in a test; it is also unreadable at a glance by the person
who most needs to understand it, which on this project is anyone deciding whether to trust the
refusal they are looking at.

So this translates, and it translates for display only. Nothing branches on these strings, no API
returns them, and every stored code stays exactly as it was - a refusal that reads differently in
two places is worse than one that reads badly in both.

Unknown codes are made readable rather than hidden. A code with no entry here is a code someone
added without passing this file, and rendering it as "Delegation budget exhausted" is a small
degradation; rendering it as blank, or crashing the page a reviewer is reading, is not.
"""

from __future__ import annotations

_PLAIN: dict[str, str] = {
    # --- refused before a payment request existed -------------------------------------------
    "QUANTITY_EXCEEDS_LIMIT": "The agent asked for more than the catalog allows",
    "CATALOG_ITEM_NOT_AVAILABLE": "No such item is on sale to this tenant",
    "CROSS_TENANT_ACCESS_DENIED": "That item belongs to a different tenant",
    # --- the policy --------------------------------------------------------------------------
    "POLICY_NOT_FOUND": "This tenant has no spending policy",
    "POLICY_EXPIRED": "The spending policy has run out and must be reissued",
    "CURRENCY_NOT_ALLOWED": "The policy does not permit this currency",
    "MERCHANT_NOT_ALLOWED": "The policy does not permit this merchant",
    "AMOUNT_EXCEEDS_LIMIT": "More than the policy allows for a single payment",
    "DAILY_LIMIT_EXCEEDED": "More than this actor is allowed to spend today",
    "APPROVAL_REQUIRED": "A human has to approve this before it can go any further",
    "PENDING_HUMAN_APPROVAL": "Waiting for a human to approve it",
    "APPROVAL_NOT_FOUND": "No approval has been granted for this purchase",
    "APPROVAL_NOT_REQUIRED": "The policy does not require an approval here",
    "IDEMPOTENCY_KEY_REPLAYED": "This exact request was already made once",
    "REQUEST_BODY_TOO_LARGE": "The request was larger than this API accepts",
    # --- delegated authority ------------------------------------------------------------------
    "DELEGATION_REVOKED": "The authority behind this payment was withdrawn",
    "DELEGATION_EXPIRED": "The authority behind this payment has run out",
    "DELEGATION_BUDGET_EXHAUSTED": "This agent's delegated budget is used up",
    "DELEGATION_SKU_OUT_OF_SCOPE": "This agent was not delegated authority to buy this item",
    "DELEGATION_AMOUNT_EXCEEDS_HOP_LIMIT": "More than one payment of this delegation may cover",
    "DELEGATION_REQUIRES_A_CATALOG_SKU": (
        "A delegation is scoped to named items, and this purchase names none"
    ),
    "DELEGATION_ACTOR_ALREADY_HOLDS_ONE": "This agent already holds a delegation that still works",
    "DELEGATION_BUDGET_EXCEEDS_PARENT": "A delegation cannot hand on more than it holds",
    "DELEGATION_CAP_EXCEEDS_PARENT": "A delegation cannot raise the per-payment limit it was given",
    "DELEGATION_SCOPE_WIDENS_PARENT": "A delegation cannot add items it was not given",
    "DELEGATION_OUTLIVES_PARENT": "A delegation cannot outlast the one it came from",
    "DELEGATION_OUTLIVES_POLICY": "A delegation cannot outlast the policy it was cut from",
    "DELEGATION_EXCEEDS_POLICY_DAILY_LIMIT": "More than the policy's daily limit",
    "DELEGATION_EXCEEDS_POLICY_PAYMENT_LIMIT": "More than the policy's per-payment limit",
    "DELEGATION_NOT_FOUND": "No such delegation for this tenant",
    "DELEGATION_PARENT_REVOKED": "The delegation it was granted from has been withdrawn",
    "DELEGATION_POLICY_DRIFT": "The policy changed after this authority was granted",
    "DELEGATION_BOUNDS_ARE_FIXED": "A delegation's limits cannot be changed after it is granted",
    "DELEGATION_DEPTH_EXCEEDED": "The chain of delegation is too long to audit",
    # --- permission to call the provider ------------------------------------------------------
    "NO_CHECKOUT_AUTHORITY_ISSUED": "No checkout permission has been issued for this purchase",
    "PAYMENT_NOT_AUTHORIZED": "This payment was never authorized, so there is nothing to pay",
    # A settled payment is the opposite of an unauthorized one, and the panel sits directly above a
    # row reading CAPTURED - so getting this wrong makes the console contradict itself on screen.
    "PAYMENT_ALREADY_SETTLED": "The money has already moved for this purchase",
    "PAYMENT_ALREADY_WITH_THE_PROVIDER": "This payment is already with the provider",
    # Distinct from the above on purpose. "This payment was never authorized" implies a
    # payment exists, and for an attack refused at the tool boundary none was ever created -
    # which is the stronger claim and the one the panel is there to make.
    "NO_PAYMENT_REQUEST_CREATED": "Nothing was created that could be paid",
    "CHECKOUT_AUTHORITY_ALREADY_USED": "That checkout permission has already been spent",
    "CHECKOUT_AUTHORITY_EXPIRED": "The checkout permission ran out before it was used",
    "CHECKOUT_AUTHORITY_UNAVAILABLE": "There is no usable checkout permission for this purchase",
    "CHECKOUT_AUTHORITY_NOT_FOUND": "No checkout permission exists for this purchase",
    "CHECKOUT_AUTHORITY_POLICY_DRIFT": "The policy changed after this purchase was authorized",
    "CHECKOUT_AUTHORITY_APPROVAL_REQUIRED": "A human must approve before checkout can be permitted",
    "CHECKOUT_AUTHORITY_PAYMENT_NOT_AUTHORIZED": "The payment is not in a state that could be paid",
    "CHECKOUT_AUTHORITY_CATALOG_SNAPSHOT_REQUIRED": (
        "Checkout needs the full catalog snapshot this purchase does not carry"
    ),
    # --- the provider --------------------------------------------------------------------------
    "RAZORPAY_NOT_TEST_MODE": "Refused: the configured key is not a Test Mode key",
    "RAZORPAY_SIGNATURE_INVALID": "The provider's signature did not verify",
    "RAZORPAY_WEBHOOK_SIGNATURE_INVALID": "The webhook signature did not verify",
    "RAZORPAY_WEBHOOK_STALE": "The webhook was correctly signed but too old to act on",
    "RAZORPAY_WEBHOOK_TIMESTAMP_MISSING": "The webhook carried no timestamp, so it cannot be dated",
    "RAZORPAY_WEBHOOK_DUPLICATE_EVENT": "This provider event was already processed",
    "RAZORPAY_WEBHOOK_AMOUNT_MISMATCH": (
        "The provider reported an amount this system never approved"
    ),
}


def humanise(code: str) -> str:
    """One reason code, in a sentence. Never empty, never raises, never the bare code.

    The fallback matters more than the table. A code added next month renders as readable words
    without anyone remembering this file existed, which is the difference between a slightly plain
    line on the page and a reviewer staring at `SOME_NEW_REFUSAL` wondering if it is a bug.
    """

    known = _PLAIN.get(code)
    if known is not None:
        return known
    words = code.replace("_", " ").strip().casefold()
    if not words:
        return "Refused for a reason that was not recorded"
    return words[0].upper() + words[1:]


def humanise_all(codes: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Every reason, in order, with duplicates kept - a repeat is a fact about the record."""

    return tuple(humanise(code) for code in codes)
