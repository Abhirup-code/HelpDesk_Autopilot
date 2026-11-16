"""Knowledge base articles.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TICKET_TYPES = ("password_reset", "new_hire", "offboarding", "access_request", "hardware")


def upgrade() -> None:
    if inspect(op.get_bind()).has_table("kb_articles"):
        return
    op.create_table(
        "kb_articles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "ticket_type",
            sa.Enum(*TICKET_TYPES, name="tickettype", native_enum=False),
            nullable=True,
        ),
        sa.Column("views", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("kb_articles", schema=None) as batch_op:
        batch_op.create_index("ix_kb_articles_slug", ["slug"], unique=True)


def downgrade() -> None:
    if not inspect(op.get_bind()).has_table("kb_articles"):
        return
    with op.batch_alter_table("kb_articles", schema=None) as batch_op:
        batch_op.drop_index("ix_kb_articles_slug")
    op.drop_table("kb_articles")
