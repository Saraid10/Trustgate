"""Create tenant-bound one-time checkout authorities.

Revision ID: 0007_checkout_authority
Revises: 0006_catalog_snapshot
Create Date: 2026-08-22
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_checkout_authority"
down_revision: str | None = "0006_catalog_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("uq_approval_tenant", "approval", ["tenant_id", "id"])
    op.create_table(
        "checkout_authority",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("payment_request_id", sa.Uuid(), nullable=False),
        sa.Column("payment_id", sa.Uuid(), nullable=False),
        sa.Column("approval_id", sa.Uuid(), nullable=True),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "payment_request_id"],
            ["payment_request.tenant_id", "payment_request.id"],
            name="fk_checkout_authority_payment_request_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "payment_id"],
            ["payment.tenant_id", "payment.id"],
            name="fk_checkout_authority_payment_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "approval_id"],
            ["approval.tenant_id", "approval.id"],
            name="fk_checkout_authority_approval_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "policy_version"],
            ["spending_policy.tenant_id", "spending_policy.version"],
            name="fk_checkout_authority_policy_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "payment_request_id", name="uq_checkout_authority_payment_request"
        ),
        sa.UniqueConstraint("tenant_id", "payment_id", name="uq_checkout_authority_payment"),
    )


def downgrade() -> None:
    op.drop_table("checkout_authority")
    op.drop_constraint("uq_approval_tenant", "approval", type_="unique")
