"""Enforce approval, policy, and checkout authority integrity.

Revision ID: 0008_authority_hardening
Revises: 0007_checkout_authority
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_authority_hardening"
down_revision: str | None = "0007_checkout_authority"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_approval_active_per_payment",
        "approval",
        ["tenant_id", "payment_request_id"],
        unique=True,
        postgresql_where=sa.text("consumed_at IS NULL"),
    )
    op.create_check_constraint(
        "ck_checkout_authority_snapshot_hash",
        "checkout_authority",
        "snapshot_hash ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_checkout_authority_expiry_after_creation",
        "checkout_authority",
        "expires_at > created_at",
    )
    op.create_check_constraint(
        "ck_checkout_authority_use_after_creation",
        "checkout_authority",
        "used_at IS NULL OR used_at >= created_at",
    )
    op.execute(
        """
        CREATE FUNCTION prevent_spending_policy_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'spending_policy rows are immutable';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER spending_policy_immutable
        BEFORE UPDATE OR DELETE ON spending_policy
        FOR EACH ROW EXECUTE FUNCTION prevent_spending_policy_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER spending_policy_immutable ON spending_policy")
    op.execute("DROP FUNCTION prevent_spending_policy_mutation()")
    op.drop_constraint(
        "ck_checkout_authority_use_after_creation", "checkout_authority", type_="check"
    )
    op.drop_constraint(
        "ck_checkout_authority_expiry_after_creation", "checkout_authority", type_="check"
    )
    op.drop_constraint(
        "ck_checkout_authority_snapshot_hash", "checkout_authority", type_="check"
    )
    op.drop_index("uq_approval_active_per_payment", table_name="approval")
