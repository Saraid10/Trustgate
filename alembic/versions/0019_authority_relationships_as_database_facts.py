"""Make three authority relationships database facts rather than application habits.

Each of these was already true in practice and true only because the application happened to do it
that way. This project's stated position is that an important authority relationship belongs in the
schema, and these three were the ones still relying on nobody making a mistake.

**One payment per request.** `payment` had a tenant-scoped foreign key to `payment_request` and no
uniqueness. A second row would give one purchase two independent state machines, each able to
authorize, capture, and release budget without the other knowing - and every lock in this codebase
takes *a* payment row, so none of them would have noticed the rival.

**A decision cites a policy that exists.** `checkout_authority.policy_version` was already keyed to
a real policy row; `authorization_decision.policy_version` and `approval.policy_version` were bare
integers. `CHECKOUT_AUTHORITY_POLICY_DRIFT` compares an authority's version against the current
policy, so a decision citing a version this tenant never published would make that comparison a
statement about a number instead of a policy.

Nothing in the application changes. If these constraints ever fire, something was already wrong.

Revision ID: 0019_authority_facts
Revises: 0018_request_delegation
Create Date: 2026-09-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019_authority_facts"
down_revision: str | None = "0018_request_delegation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_payment_one_per_request", "payment", ["tenant_id", "payment_request_id"]
    )
    op.create_foreign_key(
        "fk_authorization_decision_policy_tenant",
        "authorization_decision",
        "spending_policy",
        ["tenant_id", "policy_version"],
        ["tenant_id", "version"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_approval_policy_tenant",
        "approval",
        "spending_policy",
        ["tenant_id", "policy_version"],
        ["tenant_id", "version"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_approval_policy_tenant", "approval", type_="foreignkey")
    op.drop_constraint(
        "fk_authorization_decision_policy_tenant", "authorization_decision", type_="foreignkey"
    )
    op.drop_constraint("uq_payment_one_per_request", "payment", type_="unique")
