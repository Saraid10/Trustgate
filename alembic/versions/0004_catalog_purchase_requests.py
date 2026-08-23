"""Bind catalog purchase snapshots to payment requests.

Revision ID: 0004_catalog_purchase
Revises: 0003_catalog_foundation
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_catalog_purchase"
down_revision: str | None = "0003_catalog_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("payment_request", sa.Column("catalog_item_id", sa.Uuid(), nullable=True))
    op.add_column("payment_request", sa.Column("quantity", sa.Integer(), nullable=True))
    op.add_column("payment_request", sa.Column("purpose", sa.String(length=255), nullable=True))
    op.add_column(
        "payment_request",
        sa.Column("source", sa.String(length=32), server_default=sa.text("'API'"), nullable=False),
    )
    op.add_column(
        "payment_request",
        sa.Column("request_revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.create_foreign_key(
        "fk_payment_request_catalog_item_tenant",
        "payment_request",
        "catalog_item",
        ["tenant_id", "catalog_item_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_payment_request_tenant_catalog_item",
        "payment_request",
        ["tenant_id", "catalog_item_id"],
    )
    op.create_check_constraint(
        "ck_payment_request_catalog_snapshot_complete",
        "payment_request",
        "(catalog_item_id IS NULL AND quantity IS NULL AND purpose IS NULL) OR "
        "(catalog_item_id IS NOT NULL AND quantity > 0 AND purpose IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_payment_request_source",
        "payment_request",
        "source IN ('API', 'MCP_AGENT', 'ATTACK_HARNESS')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_payment_request_source", "payment_request", type_="check")
    op.drop_constraint(
        "ck_payment_request_catalog_snapshot_complete", "payment_request", type_="check"
    )
    op.drop_index("ix_payment_request_tenant_catalog_item", table_name="payment_request")
    op.drop_constraint(
        "fk_payment_request_catalog_item_tenant", "payment_request", type_="foreignkey"
    )
    op.drop_column("payment_request", "request_revision")
    op.drop_column("payment_request", "source")
    op.drop_column("payment_request", "purpose")
    op.drop_column("payment_request", "quantity")
    op.drop_column("payment_request", "catalog_item_id")
