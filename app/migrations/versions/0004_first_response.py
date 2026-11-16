"""Record when a ticket first got a reply.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

COLUMN = "first_response_at"


def has_column() -> bool:
    return any(c["name"] == COLUMN for c in inspect(op.get_bind()).get_columns("tickets"))


def upgrade() -> None:
    if has_column():
        return
    with op.batch_alter_table("tickets", schema=None) as batch_op:
        batch_op.add_column(sa.Column(COLUMN, sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    if not has_column():
        return
    with op.batch_alter_table("tickets", schema=None) as batch_op:
        batch_op.drop_column(COLUMN)
