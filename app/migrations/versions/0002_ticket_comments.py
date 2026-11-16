"""Ticket comments: replies to the requester and internal notes for IT.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if inspect(op.get_bind()).has_table("ticket_comments"):
        # A database built by create_all already has today's tables. It is adopted at
        # revision 0001 (see app/db.run_migrations), so this migration still runs.
        return
    op.create_table(
        "ticket_comments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("internal", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("ticket_comments", schema=None) as batch_op:
        batch_op.create_index("ix_ticket_comments_ticket_id", ["ticket_id"], unique=False)
        batch_op.create_index("ix_ticket_comments_created_at", ["created_at"], unique=False)


def downgrade() -> None:
    if not inspect(op.get_bind()).has_table("ticket_comments"):
        return
    with op.batch_alter_table("ticket_comments", schema=None) as batch_op:
        batch_op.drop_index("ix_ticket_comments_created_at")
        batch_op.drop_index("ix_ticket_comments_ticket_id")
    op.drop_table("ticket_comments")
