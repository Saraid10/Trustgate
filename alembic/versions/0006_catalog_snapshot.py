"""Persist an immutable catalog display snapshot with each catalog request.

Revision ID: 0006_catalog_snapshot
Revises: 0005_daily_spend
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_catalog_snapshot"
down_revision: str | None = "0005_daily_spend"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SNAPSHOT_CHECK = (
    "(catalog_item_id IS NULL AND quantity IS NULL AND purpose IS NULL "
    "AND catalog_sku IS NULL AND catalog_name IS NULL AND merchant_display_name IS NULL) OR "
    "(catalog_item_id IS NOT NULL AND quantity > 0 AND purpose IS NOT NULL "
    "AND catalog_sku IS NOT NULL AND catalog_name IS NOT NULL "
    "AND merchant_display_name IS NOT NULL)"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_payment_request_catalog_snapshot_complete", "payment_request", type_="check"
    )
    op.add_column("payment_request", sa.Column("catalog_sku", sa.String(length=64), nullable=True))
    op.add_column(
        "payment_request", sa.Column("catalog_name", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "payment_request", sa.Column("merchant_display_name", sa.String(length=255), nullable=True)
    )
    op.create_check_constraint(
        "ck_payment_request_catalog_snapshot_complete", "payment_request", _SNAPSHOT_CHECK
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_payment_request_catalog_snapshot_complete", "payment_request", type_="check"
    )
    op.drop_column("payment_request", "merchant_display_name")
    op.drop_column("payment_request", "catalog_name")
    op.drop_column("payment_request", "catalog_sku")
    op.create_check_constraint(
        "ck_payment_request_catalog_snapshot_complete",
        "payment_request",
        "(catalog_item_id IS NULL AND quantity IS NULL AND purpose IS NULL) OR "
        "(catalog_item_id IS NOT NULL AND quantity > 0 AND purpose IS NOT NULL)",
    )
