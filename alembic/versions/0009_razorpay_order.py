"""Persist server-side Razorpay checkout order bindings.

Revision ID: 0009_razorpay_order
Revises: 0008_authority_hardening
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_razorpay_order"
down_revision: str | None = "0008_authority_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_checkout_authority_tenant", "checkout_authority", ["tenant_id", "id"]
    )
    op.create_table(
        "razorpay_order",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("checkout_authority_id", sa.Uuid(), nullable=False),
        sa.Column("payment_id", sa.Uuid(), nullable=False),
        sa.Column("razorpay_order_id", sa.String(length=255), nullable=False),
        sa.Column("receipt", sa.String(length=40), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("amount_minor >= 0", name="ck_razorpay_order_amount_nonnegative"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "checkout_authority_id"],
            ["checkout_authority.tenant_id", "checkout_authority.id"],
            name="fk_razorpay_order_authority_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "payment_id"],
            ["payment.tenant_id", "payment.id"],
            name="fk_razorpay_order_payment_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_razorpay_order_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "checkout_authority_id", name="uq_razorpay_order_authority"
        ),
        sa.UniqueConstraint("razorpay_order_id", name="uq_razorpay_order_provider_id"),
    )


def downgrade() -> None:
    op.drop_table("razorpay_order")
    op.drop_constraint("uq_checkout_authority_tenant", "checkout_authority", type_="unique")
