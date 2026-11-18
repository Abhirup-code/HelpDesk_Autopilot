"""Count how often a ticket came back after being resolved.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

COLUMN = "reopen_count"


def has_column() -> bool:
    return any(c["name"] == COLUMN for c in inspect(op.get_bind()).get_columns("tickets"))


def upgrade() -> None:
    if has_column():
        return
    with op.batch_alter_table("tickets", schema=None) as batch_op:
        batch_op.add_column(sa.Column(COLUMN, sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    if not has_column():
        return
    with op.batch_alter_table("tickets", schema=None) as batch_op:
        batch_op.drop_column(COLUMN)
