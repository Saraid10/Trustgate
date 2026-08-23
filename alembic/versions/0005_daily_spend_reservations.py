"""Add atomic UTC daily spend reservations.

Revision ID: 0005_daily_spend
Revises: 0004_catalog_purchase
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_daily_spend"
down_revision: str | None = "0004_catalog_purchase"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "daily_spend_reservation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("spend_date", sa.Date(), nullable=False),
        sa.Column("reserved_amount_minor", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "actor_id", "spend_date", name="uq_daily_spend_reservation_actor_day"
        ),
        sa.CheckConstraint(
            "reserved_amount_minor >= 0", name="ck_daily_spend_reservation_amount_nonnegative"
        ),
    )


def downgrade() -> None:
    op.drop_table("daily_spend_reservation")
