"""Record each delegation spend, so it can be repeated safely and undone.

A counter cannot answer either question an authorization path asks of it. It cannot say whether
this spend already happened, so a retried request charges twice. It cannot say what to give back
when the payment it paid for is later denied, so budget leaks and never returns.

`uq_delegation_spend_reference` is what makes a retry safe: the second attempt conflicts instead of
charging, and the caller cannot tell the difference from the first having worked.

Revision ID: 0013_delegation_spend
Revises: 0012_delegation_chain
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_delegation_spend"
down_revision: str | None = "0012_delegation_chain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "delegation_spend",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("delegation_id", sa.Uuid(), nullable=False),
        sa.Column("reference", sa.Uuid(), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "reference", name="uq_delegation_spend_reference"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "delegation_id"],
            ["delegation.tenant_id", "delegation.id"],
            name="fk_delegation_spend_delegation_tenant",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("amount_minor > 0", name="ck_delegation_spend_amount_positive"),
        sa.CheckConstraint(
            "released_at IS NULL OR released_at >= created_at",
            name="ck_delegation_spend_release_after_creation",
        ),
    )
    op.create_index(
        "ix_delegation_spend_unreleased",
        "delegation_spend",
        ["tenant_id", "delegation_id"],
        postgresql_where=sa.text("released_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_delegation_spend_unreleased", table_name="delegation_spend")
    op.drop_table("delegation_spend")
