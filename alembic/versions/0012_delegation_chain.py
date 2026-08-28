"""Add multi-hop delegated spending authority with enforced attenuation.

Two rules keep a chain honest, and they are not the same rule.

Per-edge narrowing says a hop may not be wider than the hop above it. That is what the
`delegation_attenuates` trigger checks, against the parent row, inside the same transaction that
writes the child.

Aggregate partitioning says the hops below a node may not, between them, promise more than the
node holds. Per-edge narrowing does not imply it: two children each exactly as wide as their
parent satisfy every comparison and together spend twice the parent's budget. That is what
`ck_delegation_budget_partitioned` refuses, on the parent's own row, where no application mistake
can reach around it.

Revision ID: 0012_delegation_chain
Revises: 0011_audit_event_references
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_delegation_chain"
down_revision: str | None = "0011_audit_event_references"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "delegation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("root_actor_id", sa.String(length=255), nullable=False),
        sa.Column("delegator_actor_id", sa.String(length=255), nullable=False),
        sa.Column("delegate_actor_id", sa.String(length=255), nullable=False),
        sa.Column("budget_minor", sa.Integer(), nullable=False),
        sa.Column("allocated_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("spent_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_amount_minor", sa.Integer(), nullable=False),
        sa.Column("allowed_skus", postgresql.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_delegation_tenant"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            ["delegation.tenant_id", "delegation.id"],
            name="fk_delegation_parent_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            ["spending_policy.tenant_id", "spending_policy.id"],
            name="fk_delegation_policy_tenant",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("depth >= 0", name="ck_delegation_depth_nonnegative"),
        sa.CheckConstraint("depth <= 8", name="ck_delegation_depth_bounded"),
        sa.CheckConstraint(
            "(parent_id IS NULL) = (depth = 0)", name="ck_delegation_root_is_the_only_orphan"
        ),
        sa.CheckConstraint("budget_minor >= 0", name="ck_delegation_budget_nonnegative"),
        sa.CheckConstraint("allocated_minor >= 0", name="ck_delegation_allocated_nonnegative"),
        sa.CheckConstraint("spent_minor >= 0", name="ck_delegation_spent_nonnegative"),
        sa.CheckConstraint(
            "allocated_minor + spent_minor <= budget_minor",
            name="ck_delegation_budget_partitioned",
        ),
        sa.CheckConstraint("max_amount_minor >= 0", name="ck_delegation_max_amount_nonnegative"),
        sa.CheckConstraint("expires_at > created_at", name="ck_delegation_expiry_after_creation"),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_delegation_revocation_after_creation",
        ),
        sa.CheckConstraint(
            "cardinality(allowed_skus) > 0", name="ck_delegation_scope_is_not_empty"
        ),
    )
    op.create_index(
        "ix_delegation_active_delegate",
        "delegation",
        ["tenant_id", "delegate_actor_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index("ix_delegation_parent", "delegation", ["tenant_id", "parent_id"])

    op.execute(
        """
        CREATE FUNCTION enforce_delegation_attenuation() RETURNS trigger AS $$
        DECLARE
            parent delegation%ROWTYPE;
        BEGIN
            IF NEW.parent_id IS NULL THEN
                RETURN NEW;
            END IF;

            SELECT * INTO parent FROM delegation
             WHERE tenant_id = NEW.tenant_id AND id = NEW.parent_id
             FOR UPDATE;

            IF NOT FOUND THEN
                RAISE EXCEPTION 'delegation parent % is not in tenant %',
                    NEW.parent_id, NEW.tenant_id;
            END IF;
            IF parent.revoked_at IS NOT NULL THEN
                RAISE EXCEPTION 'a revoked delegation cannot be delegated onward';
            END IF;
            IF NEW.depth <> parent.depth + 1 THEN
                RAISE EXCEPTION 'delegation depth must be exactly one below its parent';
            END IF;
            IF NEW.delegator_actor_id <> parent.delegate_actor_id THEN
                RAISE EXCEPTION 'only the holder of a delegation may delegate it onward';
            END IF;
            IF NEW.root_actor_id <> parent.root_actor_id THEN
                RAISE EXCEPTION 'delegation must keep the root principal of its parent';
            END IF;
            IF NEW.policy_version <> parent.policy_version THEN
                RAISE EXCEPTION 'delegation must be cut from its parent policy version';
            END IF;
            IF NEW.budget_minor > parent.budget_minor THEN
                RAISE EXCEPTION 'delegation budget may not exceed its parent';
            END IF;
            IF NEW.max_amount_minor > parent.max_amount_minor THEN
                RAISE EXCEPTION 'delegation per-payment cap may not exceed its parent';
            END IF;
            IF NEW.expires_at > parent.expires_at THEN
                RAISE EXCEPTION 'delegation may not outlive its parent';
            END IF;
            IF NOT (NEW.allowed_skus <@ parent.allowed_skus) THEN
                RAISE EXCEPTION 'delegation scope may not widen its parent';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER delegation_attenuates
        BEFORE INSERT ON delegation
        FOR EACH ROW EXECUTE FUNCTION enforce_delegation_attenuation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER delegation_attenuates ON delegation")
    op.execute("DROP FUNCTION enforce_delegation_attenuation()")
    op.drop_index("ix_delegation_parent", table_name="delegation")
    op.drop_index("ix_delegation_active_delegate", table_name="delegation")
    op.drop_table("delegation")
