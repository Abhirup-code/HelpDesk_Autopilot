"""Let the requester rate a resolved ticket.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

COLUMNS = {
    "satisfaction": sa.Integer(),
    "satisfaction_comment": sa.Text(),
    "satisfaction_at": sa.DateTime(timezone=True),
}


def present() -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns("tickets")}


def upgrade() -> None:
    missing = {name: kind for name, kind in COLUMNS.items() if name not in present()}
    if not missing:
        return
    with op.batch_alter_table("tickets", schema=None) as batch_op:
        for name, kind in missing.items():
            batch_op.add_column(sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    here = present()
    with op.batch_alter_table("tickets", schema=None) as batch_op:
        for name in COLUMNS:
            if name in here:
                batch_op.drop_column(name)
