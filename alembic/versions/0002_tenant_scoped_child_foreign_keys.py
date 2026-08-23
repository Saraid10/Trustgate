"""Enforce tenant consistency for every tenant-scoped parent-child link.

Revision ID: 0002_tenant_fk
Revises: 0001_initial
Create Date: 2026-08-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_tenant_fk"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_policy_tenant_version", "spending_policy", ["tenant_id", "version"]
    )
    op.drop_constraint("payment_request_merchant_id_fkey", "payment_request", type_="foreignkey")
    op.create_foreign_key(
        "fk_payment_request_merchant_tenant",
        "payment_request",
        "merchant",
        ["tenant_id", "merchant_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_payment_request_tenant_merchant", "payment_request", ["tenant_id", "merchant_id"]
    )
    op.drop_constraint("approval_payment_request_id_fkey", "approval", type_="foreignkey")
    op.create_foreign_key(
        "fk_approval_payment_request_tenant",
        "approval",
        "payment_request",
        ["tenant_id", "payment_request_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_approval_tenant_payment_request", "approval", ["tenant_id", "payment_request_id"]
    )
    op.drop_constraint(
        "authorization_decision_payment_request_id_fkey",
        "authorization_decision",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_authorization_decision_payment_request_tenant",
        "authorization_decision",
        "payment_request",
        ["tenant_id", "payment_request_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_authorization_decision_tenant_payment_request",
        "authorization_decision",
        ["tenant_id", "payment_request_id"],
    )
    op.drop_constraint("payment_payment_request_id_fkey", "payment", type_="foreignkey")
    op.create_foreign_key(
        "fk_payment_payment_request_tenant",
        "payment",
        "payment_request",
        ["tenant_id", "payment_request_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_payment_tenant_payment_request", "payment", ["tenant_id", "payment_request_id"]
    )
    op.drop_constraint("provider_event_payment_id_fkey", "provider_event", type_="foreignkey")
    op.create_foreign_key(
        "fk_provider_event_payment_tenant",
        "provider_event",
        "payment",
        ["tenant_id", "payment_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_provider_event_tenant_payment", "provider_event", ["tenant_id", "payment_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_provider_event_tenant_payment", table_name="provider_event")
    op.drop_constraint("fk_provider_event_payment_tenant", "provider_event", type_="foreignkey")
    op.create_foreign_key(
        "provider_event_payment_id_fkey",
        "provider_event",
        "payment",
        ["payment_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_index("ix_payment_tenant_payment_request", table_name="payment")
    op.drop_constraint("fk_payment_payment_request_tenant", "payment", type_="foreignkey")
    op.create_foreign_key(
        "payment_payment_request_id_fkey",
        "payment",
        "payment_request",
        ["payment_request_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_index(
        "ix_authorization_decision_tenant_payment_request", table_name="authorization_decision"
    )
    op.drop_constraint(
        "fk_authorization_decision_payment_request_tenant",
        "authorization_decision",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "authorization_decision_payment_request_id_fkey",
        "authorization_decision",
        "payment_request",
        ["payment_request_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_index("ix_approval_tenant_payment_request", table_name="approval")
    op.drop_constraint("fk_approval_payment_request_tenant", "approval", type_="foreignkey")
    op.create_foreign_key(
        "approval_payment_request_id_fkey",
        "approval",
        "payment_request",
        ["payment_request_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_index("ix_payment_request_tenant_merchant", table_name="payment_request")
    op.drop_constraint("fk_payment_request_merchant_tenant", "payment_request", type_="foreignkey")
    op.create_foreign_key(
        "payment_request_merchant_id_fkey",
        "payment_request",
        "merchant",
        ["merchant_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint("uq_policy_tenant_version", "spending_policy", type_="unique")
