"""Give audit events durable, tenant-scoped purchase references.

Evidence receipts originally discovered lifecycle events by examining a handful of JSON payload
keys. That made the receipt depend on every event writer remembering a payload convention, and an
event with only a receipt or provider-order list could vanish from its own purchase trail.

Payload remains event detail. These nullable references are the audited object's identity and are
protected by the same composite tenant foreign-key pattern as the rest of the domain. Existing
rows are backfilled only when a reference can be resolved through a tenant-scoped durable record.

Revision ID: 0011_audit_event_references
Revises: 0010_provider_order_recovery
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_audit_event_references"
down_revision: str | None = "0010_provider_order_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in (
        "payment_request_id",
        "payment_id",
        "checkout_authority_id",
        "provider_order_id",
    ):
        op.add_column("audit_event", sa.Column(column, sa.Uuid(), nullable=True))

    # Recover references from older payloads only by joining a real row under the same tenant.
    # Casting payload text to UUID would make a malformed historic payload abort the migration.
    op.execute(
        """
        UPDATE audit_event AS event
        SET payment_request_id = request.id
        FROM payment_request AS request
        WHERE event.tenant_id = request.tenant_id
          AND event.payload ->> 'payment_request_id' = request.id::text
        """
    )
    op.execute(
        """
        UPDATE audit_event AS event
        SET payment_id = payment.id
        FROM payment
        WHERE event.tenant_id = payment.tenant_id
          AND event.payload ->> 'payment_id' = payment.id::text
        """
    )
    op.execute(
        """
        UPDATE audit_event AS event
        SET checkout_authority_id = authority.id
        FROM checkout_authority AS authority
        WHERE event.tenant_id = authority.tenant_id
          AND event.payload ->> 'checkout_authority_id' = authority.id::text
        """
    )
    op.execute(
        """
        UPDATE audit_event AS event
        SET provider_order_id = provider_order.id
        FROM razorpay_order AS provider_order
        WHERE event.tenant_id = provider_order.tenant_id
          AND event.payload ->> 'razorpay_order_id' = provider_order.razorpay_order_id
        """
    )
    # Historic review-required events have only the receipt, which is still sufficient to locate
    # the one local order intent they concern. Restrict this fallback to that event kind: receipt
    # is provider detail, not a general-purpose purchase identifier.
    op.execute(
        """
        UPDATE audit_event AS event
        SET provider_order_id = provider_order.id
        FROM razorpay_order AS provider_order
        WHERE event.provider_order_id IS NULL
          AND event.event_kind = 'razorpay_order_needs_review'
          AND event.tenant_id = provider_order.tenant_id
          AND event.payload ->> 'receipt' = provider_order.receipt
        """
    )

    # One known reference carries the rest of the purchase graph. Fill every derivable link so
    # historic receipts remain complete when the payload join is removed.
    op.execute(
        """
        UPDATE audit_event AS event
        SET checkout_authority_id = provider_order.checkout_authority_id,
            payment_id = provider_order.payment_id
        FROM razorpay_order AS provider_order
        WHERE event.tenant_id = provider_order.tenant_id
          AND event.provider_order_id = provider_order.id
        """
    )
    op.execute(
        """
        UPDATE audit_event AS event
        SET payment_request_id = authority.payment_request_id,
            payment_id = authority.payment_id
        FROM checkout_authority AS authority
        WHERE event.tenant_id = authority.tenant_id
          AND event.checkout_authority_id = authority.id
        """
    )
    op.execute(
        """
        UPDATE audit_event AS event
        SET payment_request_id = payment.payment_request_id
        FROM payment
        WHERE event.payment_request_id IS NULL
          AND event.tenant_id = payment.tenant_id
          AND event.payment_id = payment.id
        """
    )

    op.create_foreign_key(
        "fk_audit_event_payment_request_tenant",
        "audit_event",
        "payment_request",
        ["tenant_id", "payment_request_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_audit_event_payment_tenant",
        "audit_event",
        "payment",
        ["tenant_id", "payment_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_audit_event_checkout_authority_tenant",
        "audit_event",
        "checkout_authority",
        ["tenant_id", "checkout_authority_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_audit_event_provider_order_tenant",
        "audit_event",
        "razorpay_order",
        ["tenant_id", "provider_order_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_audit_event_tenant_payment_request",
        "audit_event",
        ["tenant_id", "payment_request_id"],
    )
    op.create_index("ix_audit_event_tenant_payment", "audit_event", ["tenant_id", "payment_id"])
    op.create_index(
        "ix_audit_event_tenant_checkout_authority",
        "audit_event",
        ["tenant_id", "checkout_authority_id"],
    )
    op.create_index(
        "ix_audit_event_tenant_provider_order",
        "audit_event",
        ["tenant_id", "provider_order_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_event_tenant_provider_order", table_name="audit_event")
    op.drop_index("ix_audit_event_tenant_checkout_authority", table_name="audit_event")
    op.drop_index("ix_audit_event_tenant_payment", table_name="audit_event")
    op.drop_index("ix_audit_event_tenant_payment_request", table_name="audit_event")
    op.drop_constraint("fk_audit_event_provider_order_tenant", "audit_event", type_="foreignkey")
    op.drop_constraint(
        "fk_audit_event_checkout_authority_tenant", "audit_event", type_="foreignkey"
    )
    op.drop_constraint("fk_audit_event_payment_tenant", "audit_event", type_="foreignkey")
    op.drop_constraint("fk_audit_event_payment_request_tenant", "audit_event", type_="foreignkey")
    op.drop_column("audit_event", "provider_order_id")
    op.drop_column("audit_event", "checkout_authority_id")
    op.drop_column("audit_event", "payment_id")
    op.drop_column("audit_event", "payment_request_id")
