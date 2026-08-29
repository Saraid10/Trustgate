"""Fix a hop's bounds at grant, so attenuation cannot be undone by an update.

`delegation_attenuates` fires BEFORE INSERT and only BEFORE INSERT, which meant every bound it
checked could be raised afterwards by an ordinary UPDATE. A hop granted a budget of 1000 was
rewritten to 999999 with its scope widened to arbitrary SKUs and its expiry pushed a decade out,
and nothing objected - so a child could be widened past its parent and the whole chain with it.

0012's own docstring said a row that widens its parent "cannot be written even by code that never
consults the parent". That was true of INSERT and false of UPDATE, which is the more likely of the
two to be written by accident.

Bounds are now fixed at grant. Only what a hop does after being granted may change: what it has
promised downward, what it has spent, and whether it has been revoked. Revocation is one-way for
the same reason - an unrevoked hop is authority coming back from the dead.

Revision ID: 0015_delegation_frozen
Revises: 0014_audit_delegation
Create Date: 2026-08-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015_delegation_frozen"
down_revision: str | None = "0014_audit_delegation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION freeze_delegation_bounds() RETURNS trigger AS $$
        BEGIN
            IF NEW.tenant_id <> OLD.tenant_id
               OR NEW.parent_id IS DISTINCT FROM OLD.parent_id
               OR NEW.depth <> OLD.depth
               OR NEW.policy_id <> OLD.policy_id
               OR NEW.policy_version <> OLD.policy_version
               OR NEW.root_actor_id <> OLD.root_actor_id
               OR NEW.delegator_actor_id <> OLD.delegator_actor_id
               OR NEW.delegate_actor_id <> OLD.delegate_actor_id
               OR NEW.budget_minor <> OLD.budget_minor
               OR NEW.max_amount_minor <> OLD.max_amount_minor
               OR NEW.allowed_skus <> OLD.allowed_skus
               OR NEW.purpose <> OLD.purpose
               OR NEW.expires_at <> OLD.expires_at
               OR NEW.created_at <> OLD.created_at
            THEN
                RAISE EXCEPTION
                    'delegation bounds are fixed at grant: only allocation, spending, and '
                    'revocation may change';
            END IF;

            IF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
                RAISE EXCEPTION 'a revoked delegation cannot be revived';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER delegation_bounds_are_frozen
        BEFORE UPDATE ON delegation
        FOR EACH ROW EXECUTE FUNCTION freeze_delegation_bounds();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER delegation_bounds_are_frozen ON delegation")
    op.execute("DROP FUNCTION freeze_delegation_bounds()")
