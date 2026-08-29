"""Let the database own the parent's allocation, and check a root against its policy.

Two holes in the same place, and both made a documented claim untrue.

`ck_delegation_budget_partitioned` refuses `allocated_minor + spent_minor > budget_minor` on a
parent's own row, which reads like the aggregate is database-enforced. It is not: `allocated_minor`
was only ever incremented by `delegation.grant`, so an insert that skipped the application left it
at zero and the constraint had nothing to object to. Three children of 1000 each were written under
a parent holding 1000, and every per-edge check passed on the way in. The trigger now performs the
allocation itself, so the aggregate holds for any insert rather than for well-behaved callers.

The second: the trigger returned immediately for a root, which meant the policy bounds a root is
supposed to be cut from lived only in Python. A root of 99,999,999 was accepted against a policy
capping 200,000. Roots are now checked against the policy they name.

What this changes about the evidence is worth stating. The aggregate was covered by a mutation,
because it lived in code a mutation runner can edit; it is now covered by tests that violate it
directly, because it lives in a trigger. That is a stronger guarantee proven a weaker way, and
README says which guards are which.

Revision ID: 0016_delegation_allocation
Revises: 0015_delegation_frozen
Create Date: 2026-08-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016_delegation_allocation"
down_revision: str | None = "0015_delegation_frozen"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_WITH_ALLOCATION = """
CREATE OR REPLACE FUNCTION enforce_delegation_attenuation() RETURNS trigger AS $$
DECLARE
    parent delegation%ROWTYPE;
    rules  spending_policy%ROWTYPE;
BEGIN
    IF NEW.parent_id IS NULL THEN
        SELECT * INTO rules FROM spending_policy
         WHERE tenant_id = NEW.tenant_id AND id = NEW.policy_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'delegation policy % is not in tenant %',
                NEW.policy_id, NEW.tenant_id;
        END IF;
        IF NEW.policy_version <> rules.version THEN
            RAISE EXCEPTION 'delegation must name the version of the policy it cites';
        END IF;
        IF NEW.budget_minor > rules.max_daily_spend_minor THEN
            RAISE EXCEPTION 'delegation budget may not exceed the policy daily limit';
        END IF;
        IF NEW.max_amount_minor > rules.max_amount_minor THEN
            RAISE EXCEPTION 'delegation per-payment cap may not exceed the policy';
        END IF;
        IF NEW.expires_at > rules.expiry THEN
            RAISE EXCEPTION 'delegation may not outlive the policy it is cut from';
        END IF;

        RETURN NEW;
    END IF;

    SELECT * INTO parent FROM delegation
     WHERE tenant_id = NEW.tenant_id AND id = NEW.parent_id
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'delegation parent % is not in tenant %', NEW.parent_id, NEW.tenant_id;
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

    -- The aggregate, taken here rather than trusted to whoever remembered to call grant(). The
    -- predicate is what refuses a sibling reaching for budget its parent has already promised.
    UPDATE delegation
       SET allocated_minor = allocated_minor + NEW.budget_minor
     WHERE tenant_id = NEW.tenant_id
       AND id = NEW.parent_id
       AND allocated_minor + spent_minor + NEW.budget_minor <= budget_minor;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'DELEGATION_BUDGET_EXHAUSTED';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_WITHOUT_ALLOCATION = """
CREATE OR REPLACE FUNCTION enforce_delegation_attenuation() RETURNS trigger AS $$
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
        RAISE EXCEPTION 'delegation parent % is not in tenant %', NEW.parent_id, NEW.tenant_id;
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


def upgrade() -> None:
    op.execute(_WITH_ALLOCATION)


def downgrade() -> None:
    op.execute(_WITHOUT_ALLOCATION)
