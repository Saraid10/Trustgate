"""Record provider order intent so a crashed order call can be reconciled.

Consuming a checkout authority commits before the provider is called. A failure between those two
points left the authority burned with no order and no record that one had been attempted, so the
purchase could not be retried or resolved without manual inspection.

Razorpay offers no idempotency for order creation. Verified against Test Mode on 2026-08-25: two
creates carrying the same receipt produced two distinct orders, and an `X-Razorpay-Idempotency-Key`
header did not deduplicate either. Recovery therefore has to be driven by state this system owns,
with the provider consulted only to discover whether an order already exists for a receipt.

Revision ID: 0010_provider_order_recovery
Revises: 0009_razorpay_order
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_provider_order_recovery"
down_revision: str | None = "0009_razorpay_order"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # An intent exists before the provider order does, so the identifier can be absent.
    op.alter_column(
        "razorpay_order",
        "razorpay_order_id",
        existing_type=sa.String(length=255),
        nullable=True,
    )
    op.add_column(
        "razorpay_order",
        sa.Column(
            "provider_state",
            sa.String(length=32),
            nullable=False,
            server_default="CONFIRMED",
        ),
    )
    op.add_column(
        "razorpay_order",
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_razorpay_order_provider_state",
        "razorpay_order",
        "provider_state IN ('PENDING', 'CONFIRMED', 'NEEDS_REVIEW')",
    )
    # A confirmed row must carry the provider's identifier; a pending one must not yet.
    op.create_check_constraint(
        "ck_razorpay_order_state_matches_identifier",
        "razorpay_order",
        "(provider_state = 'PENDING' AND razorpay_order_id IS NULL) "
        "OR (provider_state = 'CONFIRMED' AND razorpay_order_id IS NOT NULL) "
        "OR provider_state = 'NEEDS_REVIEW'",
    )
    op.alter_column("razorpay_order", "provider_state", server_default=None)


def downgrade() -> None:
    op.drop_constraint("ck_razorpay_order_state_matches_identifier", "razorpay_order")
    op.drop_constraint("ck_razorpay_order_provider_state", "razorpay_order")
    op.drop_column("razorpay_order", "reconciled_at")
    op.drop_column("razorpay_order", "provider_state")
    # Rows without an identifier are unconfirmed intents and cannot satisfy the original NOT NULL.
    op.execute("DELETE FROM razorpay_order WHERE razorpay_order_id IS NULL")
    op.alter_column(
        "razorpay_order",
        "razorpay_order_id",
        existing_type=sa.String(length=255),
        nullable=False,
    )
