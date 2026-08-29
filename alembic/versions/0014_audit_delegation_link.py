"""Let an audit event name the delegation it concerns.

The audit table links what it evidences with tenant-scoped columns and says so in its own comment:
payload is useful detail and explicitly not a join contract. A delegation event recorded only in
the payload would be readable and unqueryable, which is the wrong half.

This is the column the accountability chain hangs off - given a payment, walk to the delegation,
then up the chain to the human at the root.

Revision ID: 0014_audit_delegation
Revises: 0013_delegation_spend
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_audit_delegation"
down_revision: str | None = "0013_delegation_spend"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audit_event", sa.Column("delegation_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_audit_event_delegation_tenant",
        "audit_event",
        "delegation",
        ["tenant_id", "delegation_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_audit_event_tenant_delegation", "audit_event", ["tenant_id", "delegation_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_audit_event_tenant_delegation", table_name="audit_event")
    op.drop_constraint("fk_audit_event_delegation_tenant", "audit_event", type_="foreignkey")
    op.drop_column("audit_event", "delegation_id")
