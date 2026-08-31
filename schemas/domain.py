from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

CurrencyCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
NonNegativeAmount = Annotated[int, Field(ge=0)]


class DomainSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TenantSchema(DomainSchema):
    id: UUID
    name: str
    created_at: datetime


class MerchantSchema(DomainSchema):
    id: UUID
    tenant_id: UUID
    name: str
    is_active: bool


class CatalogItemSchema(DomainSchema):
    id: UUID
    tenant_id: UUID
    merchant_id: UUID
    sku: str
    name: str
    description_untrusted: str
    price_minor: NonNegativeAmount
    currency: CurrencyCode
    max_quantity: Annotated[int, Field(gt=0)]
    active: bool
    created_at: datetime
    updated_at: datetime


class SpendingPolicySchema(DomainSchema):
    id: UUID
    tenant_id: UUID
    version: Annotated[int, Field(gt=0)]
    max_amount_minor: NonNegativeAmount
    currency: CurrencyCode
    max_daily_spend_minor: NonNegativeAmount
    expiry: datetime
    approval_required_above_minor: NonNegativeAmount | None
    created_at: datetime


class PolicyMerchantSchema(DomainSchema):
    tenant_id: UUID
    policy_id: UUID
    merchant_id: UUID


class PaymentRequestSchema(DomainSchema):
    id: UUID
    tenant_id: UUID
    actor_id: str
    merchant_id: UUID
    catalog_item_id: UUID | None
    catalog_sku: str | None
    catalog_name: str | None
    merchant_display_name: str | None
    quantity: int | None
    purpose: str | None
    source: str
    request_revision: int
    amount_minor: NonNegativeAmount
    currency: CurrencyCode
    order_ref: str
    idempotency_key: str
    created_at: datetime


class ApprovalSchema(DomainSchema):
    id: UUID
    tenant_id: UUID
    payment_request_id: UUID
    policy_version: int
    granted_by: str
    expires_at: datetime
    consumed_at: datetime | None


class AuthorizationDecisionSchema(DomainSchema):
    id: UUID
    tenant_id: UUID
    payment_request_id: UUID
    decision: Literal["ALLOW", "DENY", "REQUIRE_APPROVAL"]
    reasons: list[str]
    policy_version: int
    correlation_id: UUID
    created_at: datetime


class PaymentSchema(DomainSchema):
    id: UUID
    tenant_id: UUID
    payment_request_id: UUID
    state: Literal[
        "CREATED",
        "APPROVAL_REQUIRED",
        "AUTHORIZED",
        "PROVIDER_PENDING",
        "CAPTURED",
        "DENIED",
        "EXPIRED",
        "FAILED",
        "REFUNDED",
        "PARTIALLY_REFUNDED",
        "CANCELLED",
    ]
    authorized_amount_minor: NonNegativeAmount | None
    captured_amount_minor: NonNegativeAmount
    refunded_amount_minor: NonNegativeAmount
    updated_at: datetime


class CheckoutAuthoritySchema(DomainSchema):
    id: UUID
    tenant_id: UUID
    payment_request_id: UUID
    payment_id: UUID
    approval_id: UUID | None
    policy_version: int
    snapshot_hash: str
    expires_at: datetime
    used_at: datetime | None
    created_at: datetime


class ProviderEventSchema(DomainSchema):
    id: UUID
    tenant_id: UUID
    provider_event_id: str
    event_type: Literal[
        "payment.authorized",
        "payment.captured",
        "payment.failed",
        "payment.refunded",
    ]
    payment_id: UUID
    raw_payload: bytes
    signature: str
    received_at: datetime
    processed_at: datetime | None


class AuditEventSchema(DomainSchema):
    id: UUID
    tenant_id: UUID
    correlation_id: UUID
    event_kind: str
    payload: dict[str, Any]
    created_at: datetime


class PaymentRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: Annotated[str, Field(min_length=1, max_length=255)]
    merchant_id: UUID
    amount_minor: NonNegativeAmount
    currency: CurrencyCode
    order_ref: Annotated[str, Field(min_length=1, max_length=255)]
    idempotency_key: Annotated[str, Field(min_length=1, max_length=255)]


class CatalogPaymentRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: Annotated[str, Field(min_length=1, max_length=64)]
    quantity: Annotated[int, Field(gt=0)]
    purpose: Annotated[str, Field(min_length=1, max_length=255)]
    idempotency_key: Annotated[str, Field(min_length=1, max_length=255)]


class PaymentRequestDecision(BaseModel):
    payment_request_id: UUID
    decision: Literal["ALLOW", "DENY", "REQUIRE_APPROVAL"]
    reasons: list[str]
    policy_version: int
    correlation_id: UUID
    next_state: str


class CatalogPaymentRequestDecision(PaymentRequestDecision):
    sku: str
    quantity: int
    purpose: str
    merchant_display_name: str
    amount_minor: NonNegativeAmount
    currency: CurrencyCode


class CheckoutAuthorityResponse(BaseModel):
    checkout_authority_id: UUID
    payment_request_id: UUID
    payment_id: UUID
    expires_at: datetime
    snapshot_hash: str


class RazorpayOrderResponse(BaseModel):
    checkout_authority_id: UUID
    razorpay_key_id: str
    razorpay_order_id: str
    amount_minor: NonNegativeAmount
    currency: CurrencyCode


class RazorpayCallback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    razorpay_payment_id: Annotated[str, Field(min_length=1, max_length=255)]
    razorpay_order_id: Annotated[str, Field(min_length=1, max_length=255)]
    razorpay_signature: Annotated[str, Field(min_length=1, max_length=512)]


class ApprovalGrantResponse(BaseModel):
    approval_id: UUID
    payment_request_id: UUID
    policy_version: int
    expires_at: datetime


class InternalPolicyCreate(BaseModel):
    tenant_id: UUID
    max_amount_minor: NonNegativeAmount
    currency: CurrencyCode
    max_daily_spend_minor: NonNegativeAmount
    expiry: datetime
    approval_required_above_minor: NonNegativeAmount | None = None
    allowed_merchant_ids: list[UUID] = Field(min_length=1)


class ProviderWebhookEvent(BaseModel):
    """Signed wire model; ``event_id`` is persisted as ``provider_event_id``."""

    event_id: UUID
    event_type: Literal[
        "payment.authorized", "payment.captured", "payment.failed", "payment.refunded"
    ]
    tenant_id: UUID
    payment_id: UUID
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("occurred_at must include a UTC offset")
        return value.astimezone(UTC)

    @property
    def provider_event_id(self) -> str:
        return str(self.event_id)


class ProviderEventSimulation(BaseModel):
    tenant_id: UUID
    payment_id: UUID
    event_id: UUID = Field(default_factory=uuid4)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_webhook_body(self, event_type: str) -> bytes:
        return json.dumps(
            {
                "event_id": str(self.event_id),
                "event_type": event_type,
                "tenant_id": str(self.tenant_id),
                "payment_id": str(self.payment_id),
                "occurred_at": self.occurred_at.isoformat(),
            },
            separators=(",", ":"),
        ).encode("utf-8")


class EvidenceProposal(BaseModel):
    """What the agent asked for. Every field here is agent-influenced."""

    sku: str | None
    quantity: int | None
    purpose: str | None
    actor_id: str
    source: str
    idempotency_key: str
    requested_at: datetime


class EvidenceDerivedFacts(BaseModel):
    """What the server determined. No field here can be set by the agent."""

    merchant_id: UUID
    merchant_display_name: str | None
    catalog_item_id: UUID | None
    catalog_name: str | None
    amount_minor: NonNegativeAmount
    currency: CurrencyCode
    order_ref: str
    request_revision: int


class EvidencePolicy(BaseModel):
    version: int
    currency: CurrencyCode
    max_amount_minor: NonNegativeAmount
    max_daily_spend_minor: NonNegativeAmount
    approval_required_above_minor: NonNegativeAmount | None
    expiry: datetime


class EvidenceDecision(BaseModel):
    decision: Literal["ALLOW", "DENY", "REQUIRE_APPROVAL"]
    reasons: list[str]
    policy_version: int
    correlation_id: UUID
    decided_at: datetime


class EvidenceApproval(BaseModel):
    approval_id: UUID
    granted_by: str
    policy_version: int
    expires_at: datetime
    consumed_at: datetime | None


class EvidenceAuthority(BaseModel):
    checkout_authority_id: UUID
    snapshot_hash: str
    policy_version: int
    approval_id: UUID | None
    expires_at: datetime
    used_at: datetime | None


class EvidencePayment(BaseModel):
    payment_id: UUID
    state: str
    authorized_amount_minor: NonNegativeAmount | None
    captured_amount_minor: NonNegativeAmount
    refunded_amount_minor: NonNegativeAmount
    updated_at: datetime


class EvidenceProviderOrder(BaseModel):
    razorpay_order_id: str | None
    provider_state: str
    amount_minor: NonNegativeAmount
    currency: CurrencyCode
    receipt: str
    created_at: datetime


class EvidenceProviderEvent(BaseModel):
    provider_event_id: str
    event_type: str
    received_at: datetime
    processed_at: datetime | None


class EvidenceAuditEntry(BaseModel):
    """Audit event kinds and correlation, without raw payloads.

    Payloads can carry internal detail; the kind and correlation identifier are what make the
    trail followable, so the receipt exposes those and leaves the payloads to the audit store.
    """

    event_kind: str
    correlation_id: UUID
    created_at: datetime


class EvidenceDelegationHop(BaseModel):
    """One link of the authority this purchase ran under, as it stands now.

    `remaining_minor` is a live number rather than a snapshot of the moment of purchase, like every
    other figure in this record. The receipt says what the database holds when it is read.
    """

    delegation_id: UUID
    depth: int
    delegator_actor_id: str
    delegate_actor_id: str
    budget_minor: NonNegativeAmount
    allocated_minor: NonNegativeAmount
    spent_minor: NonNegativeAmount
    remaining_minor: int
    max_amount_minor: NonNegativeAmount
    allowed_skus: list[str]
    purpose: str
    expires_at: datetime
    revoked_at: datetime | None


class EvidenceDelegation(BaseModel):
    """The chain a purchase spent, root first, and the human at the top of it.

    Present only when the request actually debited a delegation. An actor holding none takes the
    path it always took, and this section is absent rather than empty - the difference between "no
    delegation was involved" and "a delegation was involved and did nothing" is worth keeping.

    `root_actor_id` is the accountability claim and the weakest link in it: it is the identity the
    approver token was configured with, so it names a role rather than proving a person. That is
    stated in `docs/limitations.md` and is why this says who the chain *records*, not who acted.
    """

    root_actor_id: str
    chain: list[EvidenceDelegationHop]
    spent_minor: NonNegativeAmount
    spent_sku: str | None
    released_at: datetime | None
    refusal_reason: str | None


class AuthorizationEnvelope(BaseModel):
    """One answer to "may money move here, and on whose authority", in a fixed shape.

    Every field is already somewhere else in this record. The envelope adds no fact; it collects
    the ones a reader needs to decide whether to be worried, so that answering "what was decided
    and can the provider be called" does not mean reading five sections and knowing which of them
    outranks the others.

    `provider_action_allowed` is the field to be careful about, so it is defined narrowly: it says
    whether the *record currently shows* a live, unused checkout authority over an authorized
    payment whose chain, if it has one, is still live. It is a description of stored rows, not a
    prediction and not a gate. The gate is `consume_checkout_authority`, which re-checks all of it
    inside a transaction that holds the row locks - and which, unlike this, is the thing standing
    between an agent and Razorpay. An envelope that said "allowed" would still be refused there if
    the world moved in between; that is the correct order of authority between the two.
    """

    payment_request_id: UUID
    decision: Literal["ALLOW", "DENY", "REQUIRE_APPROVAL"] | None
    reason_codes: list[str]
    merchant_id: UUID
    merchant_display_name: str | None
    amount_minor: NonNegativeAmount
    currency: str
    policy_version: int | None
    approval_state: Literal["UNKNOWN", "NOT_REQUIRED", "REQUIRED", "GRANTED", "EXPIRED", "CONSUMED"]
    delegation_id: UUID | None
    delegation_root_actor_id: str | None
    authority_expires_at: datetime | None
    provider_action_allowed: bool
    provider_action_blocked_reason: str | None


class PaymentRequestEvidence(BaseModel):
    """A traceable record of one purchase attempt.

    Assembled from tenant-scoped rows at read time and neither hashed nor signed, so it reflects
    the current database rather than proving what it held earlier. Calling it tamper-evident would
    claim a property it does not have; a signed snapshot or hash chain would provide one.
    """

    payment_request_id: UUID
    tenant_id: UUID
    generated_at: datetime
    envelope: AuthorizationEnvelope
    proposed: EvidenceProposal
    derived: EvidenceDerivedFacts
    policy: EvidencePolicy | None
    decision: EvidenceDecision | None
    approval: EvidenceApproval | None
    authority: EvidenceAuthority | None
    delegation: EvidenceDelegation | None
    payment: EvidencePayment | None
    provider_order: EvidenceProviderOrder | None
    provider_events: list[EvidenceProviderEvent]
    audit_trail: list[EvidenceAuditEntry]


class RazorpayWebhookPaymentEntity(BaseModel):
    """The payment entity Razorpay nests inside a webhook payload."""

    model_config = ConfigDict(extra="ignore")

    id: Annotated[str, Field(min_length=1, max_length=255)]
    order_id: Annotated[str, Field(min_length=1, max_length=255)]
    amount: NonNegativeAmount
    currency: CurrencyCode
    status: Annotated[str, Field(min_length=1, max_length=64)]


class RazorpayWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    payment: dict[str, RazorpayWebhookPaymentEntity] | None = None


class RazorpayWebhookEvent(BaseModel):
    """A Razorpay webhook envelope.

    Unknown fields are ignored rather than rejected: Razorpay adds fields over time, and refusing
    an otherwise valid signed event because of an unrecognised key would drop real payment
    outcomes. The fields this project acts on are pinned.
    """

    model_config = ConfigDict(extra="ignore")

    event: Annotated[str, Field(min_length=1, max_length=64)]
    payload: RazorpayWebhookPayload
    # Optional in the schema and required by the route, which is deliberate. A signed event with no
    # timestamp cannot be bounded in time, so it is refused - but it is refused with its own reason
    # code rather than as a malformed body, so an operator can tell "the provider changed shape"
    # apart from "someone posted garbage".
    created_at: int | None = None

    @property
    def payment_entity(self) -> RazorpayWebhookPaymentEntity | None:
        if self.payload.payment is None:
            return None
        return self.payload.payment.get("entity")
