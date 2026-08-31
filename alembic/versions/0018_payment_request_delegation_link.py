"""Let a payment request name the delegation it spent.

The link existed already, through `delegation_spend.reference` - a bare uuid column with no
foreign key, documented as such in `docs/limitations.md`. That is enough to *find* a spend and not
enough to trust one: nothing stopped a reference naming a request that never existed, and nothing
made the join a contract the database would keep.

Checkout is what needed it to be a key. A delegation is consulted once, at authorization, and the
money moves later; re-asking the chain before it moves means starting from the request and walking
to the delegation, which is a join that has to be right every time rather than usually.

Nullable because most requests spend no delegation at all, and an actor holding none must take the
path it took before any of this existed.

Revision ID: 0018_request_delegation
Revises: 0017_one_delegation
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018_request_delegation"
down_revision: str | None = "0017_one_delegation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("payment_request", sa.Column("delegation_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_payment_request_delegation_tenant",
        "payment_request",
        "delegation",
        ["tenant_id", "delegation_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_payment_request_tenant_delegation", "payment_request", ["tenant_id", "delegation_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_payment_request_tenant_delegation", table_name="payment_request")
    op.drop_constraint(
        "fk_payment_request_delegation_tenant", "payment_request", type_="foreignkey"
    )
    op.drop_column("payment_request", "delegation_id")
