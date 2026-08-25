from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all persistent domain records."""


class Tenant(Base):
    __tablename__ = "tenant"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Merchant(Base):
    __tablename__ = "merchant"
    __table_args__ = (UniqueConstraint("tenant_id", "id", name="uq_merchant_tenant"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class CatalogItem(Base):
    __tablename__ = "catalog_item"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_catalog_item_tenant"),
        UniqueConstraint("tenant_id", "sku", name="uq_catalog_item_tenant_sku"),
        ForeignKeyConstraint(
            ["tenant_id", "merchant_id"],
            ["merchant.tenant_id", "merchant.id"],
            name="fk_catalog_item_merchant_tenant",
            ondelete="RESTRICT",
        ),
        Index("ix_catalog_item_tenant_active", "tenant_id", "active"),
        CheckConstraint("price_minor > 0", name="ck_catalog_item_price_positive"),
        CheckConstraint("max_quantity > 0", name="ck_catalog_item_max_quantity_positive"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False
    )
    merchant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description_untrusted: Mapped[str] = mapped_column(Text, nullable=False)
    price_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    max_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SpendingPolicy(Base):
    __tablename__ = "spending_policy"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_policy_tenant"),
        UniqueConstraint("tenant_id", "version", name="uq_policy_tenant_version"),
        CheckConstraint("version > 0", name="ck_spending_policy_version_positive"),
        CheckConstraint("max_amount_minor >= 0", name="ck_policy_max_amount_nonnegative"),
        CheckConstraint("max_daily_spend_minor >= 0", name="ck_policy_daily_spend_nonnegative"),
        CheckConstraint(
            "approval_required_above_minor IS NULL OR approval_required_above_minor >= 0",
            name="ck_policy_approval_threshold_nonnegative",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    max_amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    max_daily_spend_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    expiry: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approval_required_above_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PolicyMerchant(Base):
    __tablename__ = "policy_merchant"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            ["spending_policy.tenant_id", "spending_policy.id"],
            name="fk_policy_merchant_policy_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "merchant_id"],
            ["merchant.tenant_id", "merchant.id"],
            name="fk_policy_merchant_merchant_tenant",
            ondelete="RESTRICT",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    policy_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    merchant_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)


class PaymentRequest(Base):
    __tablename__ = "payment_request"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_payment_request_tenant"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_payment_request_idempotency"),
        ForeignKeyConstraint(
            ["tenant_id", "merchant_id"],
            ["merchant.tenant_id", "merchant.id"],
            name="fk_payment_request_merchant_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "catalog_item_id"],
            ["catalog_item.tenant_id", "catalog_item.id"],
            name="fk_payment_request_catalog_item_tenant",
            ondelete="RESTRICT",
        ),
        Index("ix_payment_request_tenant_merchant", "tenant_id", "merchant_id"),
        Index("ix_payment_request_tenant_catalog_item", "tenant_id", "catalog_item_id"),
        CheckConstraint("amount_minor >= 0", name="ck_payment_request_amount_nonnegative"),
        CheckConstraint(
            "(catalog_item_id IS NULL AND quantity IS NULL AND purpose IS NULL "
            "AND catalog_sku IS NULL AND catalog_name IS NULL "
            "AND merchant_display_name IS NULL) OR "
            "(catalog_item_id IS NOT NULL AND quantity > 0 AND purpose IS NOT NULL "
            "AND catalog_sku IS NOT NULL AND catalog_name IS NOT NULL "
            "AND merchant_display_name IS NOT NULL)",
            name="ck_payment_request_catalog_snapshot_complete",
        ),
        CheckConstraint(
            "source IN ('API', 'MCP_AGENT', 'ATTACK_HARNESS')",
            name="ck_payment_request_source",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False
    )
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    merchant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    catalog_item_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    catalog_sku: Mapped[str | None] = mapped_column(String(64), nullable=True)
    catalog_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    merchant_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    purpose: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="API")
    request_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    order_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DailySpendReservation(Base):
    """One atomic per-actor budget reservation for a UTC calendar day."""

    __tablename__ = "daily_spend_reservation"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "actor_id", "spend_date", name="uq_daily_spend_reservation_actor_day"
        ),
        CheckConstraint(
            "reserved_amount_minor >= 0", name="ck_daily_spend_reservation_amount_nonnegative"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False
    )
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    spend_date: Mapped[date] = mapped_column(nullable=False)
    reserved_amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Approval(Base):
    __tablename__ = "approval"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_approval_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "payment_request_id"],
            ["payment_request.tenant_id", "payment_request.id"],
            name="fk_approval_payment_request_tenant",
            ondelete="RESTRICT",
        ),
        Index("ix_approval_tenant_payment_request", "tenant_id", "payment_request_id"),
        Index(
            "uq_approval_active_per_payment",
            "tenant_id",
            "payment_request_id",
            unique=True,
            postgresql_where=text("consumed_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False
    )
    payment_request_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    granted_by: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthorizationDecision(Base):
    __tablename__ = "authorization_decision"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "payment_request_id"],
            ["payment_request.tenant_id", "payment_request.id"],
            name="fk_authorization_decision_payment_request_tenant",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_authorization_decision_tenant_payment_request",
            "tenant_id",
            "payment_request_id",
        ),
        CheckConstraint(
            "decision IN ('ALLOW', 'DENY', 'REQUIRE_APPROVAL')",
            name="ck_authorization_decision_value",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False
    )
    payment_request_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    correlation_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Payment(Base):
    __tablename__ = "payment"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_payment_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "payment_request_id"],
            ["payment_request.tenant_id", "payment_request.id"],
            name="fk_payment_payment_request_tenant",
            ondelete="RESTRICT",
        ),
        Index("ix_payment_tenant_payment_request", "tenant_id", "payment_request_id"),
        CheckConstraint(
            "state IN ('CREATED', 'APPROVAL_REQUIRED', 'AUTHORIZED', "
            "'PROVIDER_PENDING', 'CAPTURED', 'DENIED', 'EXPIRED', 'FAILED', "
            "'REFUNDED', 'PARTIALLY_REFUNDED', 'CANCELLED')",
            name="ck_payment_state",
        ),
        CheckConstraint(
            "authorized_amount_minor IS NULL OR authorized_amount_minor >= 0",
            name="ck_payment_authorized_amount_nonnegative",
        ),
        CheckConstraint(
            "captured_amount_minor >= 0", name="ck_payment_captured_amount_nonnegative"
        ),
        CheckConstraint(
            "refunded_amount_minor >= 0", name="ck_payment_refunded_amount_nonnegative"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False
    )
    payment_request_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    authorized_amount_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    captured_amount_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    refunded_amount_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CheckoutAuthority(Base):
    """A short-lived, single-use permission to create one provider order."""

    __tablename__ = "checkout_authority"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_checkout_authority_tenant"),
        UniqueConstraint(
            "tenant_id", "payment_request_id", name="uq_checkout_authority_payment_request"
        ),
        UniqueConstraint("tenant_id", "payment_id", name="uq_checkout_authority_payment"),
        ForeignKeyConstraint(
            ["tenant_id", "payment_request_id"],
            ["payment_request.tenant_id", "payment_request.id"],
            name="fk_checkout_authority_payment_request_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "payment_id"],
            ["payment.tenant_id", "payment.id"],
            name="fk_checkout_authority_payment_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "approval_id"],
            ["approval.tenant_id", "approval.id"],
            name="fk_checkout_authority_approval_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "policy_version"],
            ["spending_policy.tenant_id", "spending_policy.version"],
            name="fk_checkout_authority_policy_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "snapshot_hash ~ '^[0-9a-f]{64}$'", name="ck_checkout_authority_snapshot_hash"
        ),
        CheckConstraint(
            "expires_at > created_at", name="ck_checkout_authority_expiry_after_creation"
        ),
        CheckConstraint(
            "used_at IS NULL OR used_at >= created_at",
            name="ck_checkout_authority_use_after_creation",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False
    )
    payment_request_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    payment_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    approval_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RazorpayOrder(Base):
    """Server-side binding between one checkout authority and one Razorpay order."""

    __tablename__ = "razorpay_order"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_razorpay_order_tenant"),
        UniqueConstraint("tenant_id", "checkout_authority_id", name="uq_razorpay_order_authority"),
        UniqueConstraint("razorpay_order_id", name="uq_razorpay_order_provider_id"),
        ForeignKeyConstraint(
            ["tenant_id", "checkout_authority_id"],
            ["checkout_authority.tenant_id", "checkout_authority.id"],
            name="fk_razorpay_order_authority_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "payment_id"],
            ["payment.tenant_id", "payment.id"],
            name="fk_razorpay_order_payment_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint("amount_minor >= 0", name="ck_razorpay_order_amount_nonnegative"),
        CheckConstraint(
            "provider_state IN ('PENDING', 'CONFIRMED', 'NEEDS_REVIEW')",
            name="ck_razorpay_order_provider_state",
        ),
        CheckConstraint(
            "(provider_state = 'PENDING' AND razorpay_order_id IS NULL) "
            "OR (provider_state = 'CONFIRMED' AND razorpay_order_id IS NOT NULL) "
            "OR provider_state = 'NEEDS_REVIEW'",
            name="ck_razorpay_order_state_matches_identifier",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False
    )
    checkout_authority_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    payment_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    # Absent until the provider confirms creation. An intent row is written first so a failure
    # between consuming the authority and creating the order leaves a record to reconcile.
    razorpay_order_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_state: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    receipt: Mapped[str] = mapped_column(String(40), nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProviderEvent(Base):
    __tablename__ = "provider_event"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider_event_id", name="uq_provider_event_tenant_event"),
        ForeignKeyConstraint(
            ["tenant_id", "payment_id"],
            ["payment.tenant_id", "payment.id"],
            name="fk_provider_event_payment_tenant",
            ondelete="RESTRICT",
        ),
        Index("ix_provider_event_tenant_payment", "tenant_id", "payment_id"),
        CheckConstraint(
            "event_type IN ('payment.authorized', 'payment.captured', "
            "'payment.failed', 'payment.refunded')",
            name="ck_provider_event_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False
    )
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payment_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    raw_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    signature: Mapped[str] = mapped_column(String(512), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_event"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "payment_request_id"],
            ["payment_request.tenant_id", "payment_request.id"],
            name="fk_audit_event_payment_request_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "payment_id"],
            ["payment.tenant_id", "payment.id"],
            name="fk_audit_event_payment_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "checkout_authority_id"],
            ["checkout_authority.tenant_id", "checkout_authority.id"],
            name="fk_audit_event_checkout_authority_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "provider_order_id"],
            ["razorpay_order.tenant_id", "razorpay_order.id"],
            name="fk_audit_event_provider_order_tenant",
            ondelete="RESTRICT",
        ),
        Index("ix_audit_event_tenant_payment_request", "tenant_id", "payment_request_id"),
        Index("ix_audit_event_tenant_payment", "tenant_id", "payment_id"),
        Index("ix_audit_event_tenant_checkout_authority", "tenant_id", "checkout_authority_id"),
        Index("ix_audit_event_tenant_provider_order", "tenant_id", "provider_order_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False
    )
    # These durable, tenant-scoped links drive receipt assembly. Payload remains useful event
    # detail, but is not an identity or a join contract.
    payment_request_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    payment_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    checkout_authority_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    provider_order_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    correlation_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    event_kind: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
