"""Create the tenant-scoped TrustGate synthetic catalog.

Revision ID: 0003_catalog_foundation
Revises: 0002_tenant_fk
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_catalog_foundation"
down_revision: str | None = "0002_tenant_fk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_item",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description_untrusted", sa.Text(), nullable=False),
        sa.Column("price_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("max_quantity", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("max_quantity > 0", name="ck_catalog_item_max_quantity_positive"),
        sa.CheckConstraint("price_minor > 0", name="ck_catalog_item_price_positive"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "merchant_id"],
            ["merchant.tenant_id", "merchant.id"],
            name="fk_catalog_item_merchant_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_catalog_item_tenant"),
        sa.UniqueConstraint("tenant_id", "sku", name="uq_catalog_item_tenant_sku"),
    )
    op.create_index("ix_catalog_item_tenant_active", "catalog_item", ["tenant_id", "active"])


def downgrade() -> None:
    op.drop_index("ix_catalog_item_tenant_active", table_name="catalog_item")
    op.drop_table("catalog_item")
