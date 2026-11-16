"""Give a ticket an owner: the IT agent working on it.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def has_column(name: str) -> bool:
    return any(c["name"] == name for c in inspect(op.get_bind()).get_columns("tickets"))


def upgrade() -> None:
    if has_column("assignee_id"):
        return
    with op.batch_alter_table("tickets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("assignee_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_tickets_assignee_id", ["assignee_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_tickets_assignee_id_users", "users", ["assignee_id"], ["id"]
        )


def downgrade() -> None:
    if not has_column("assignee_id"):
        return
    with op.batch_alter_table("tickets", schema=None) as batch_op:
        batch_op.drop_constraint("fk_tickets_assignee_id_users", type_="foreignkey")
        batch_op.drop_index("ix_tickets_assignee_id")
        batch_op.drop_column("assignee_id")
