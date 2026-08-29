"""At most one live delegation per actor, so authorization never has to choose.

Wiring delegation into the payment path means looking one up by the actor making the request. If an
actor can hold two, that lookup has to pick, and every rule for picking is a rule someone has to
remember: the newest, the widest, the narrowest, the one that fits. A tie-break invented at the
lookup is a place for authority to be chosen rather than enforced.

A partial unique index removes the question instead of answering it. Granting a second live
delegation to an actor who already holds one fails at the write, where it is obvious, rather than
at the spend, where it would be a surprise.

Revision ID: 0017_one_delegation
Revises: 0016_delegation_allocation
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_one_delegation"
down_revision: str | None = "0016_delegation_allocation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_delegation_one_live_per_actor",
        "delegation",
        ["tenant_id", "delegate_actor_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_delegation_one_live_per_actor", table_name="delegation")
