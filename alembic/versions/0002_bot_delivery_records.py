"""add bot delivery records

Revision ID: 0002_bot_delivery_records
Revises: 0001_initial_persistence
Create Date: 2026-06-25
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002_bot_delivery_records"
down_revision = "0001_initial_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_delivery_records",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("platform", sa.String(length=64), nullable=False),
        sa.Column("chat_context_id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("event_seq", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        op.f("ix_bot_delivery_records_chat_context_id"),
        "bot_delivery_records",
        ["chat_context_id"],
    )
    op.create_index(
        op.f("ix_bot_delivery_records_event_id"),
        "bot_delivery_records",
        ["event_id"],
    )
    op.create_index(
        op.f("ix_bot_delivery_records_platform"),
        "bot_delivery_records",
        ["platform"],
    )
    op.create_index(
        op.f("ix_bot_delivery_records_status"),
        "bot_delivery_records",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_bot_delivery_records_status"), table_name="bot_delivery_records")
    op.drop_index(op.f("ix_bot_delivery_records_platform"), table_name="bot_delivery_records")
    op.drop_index(op.f("ix_bot_delivery_records_event_id"), table_name="bot_delivery_records")
    op.drop_index(
        op.f("ix_bot_delivery_records_chat_context_id"),
        table_name="bot_delivery_records",
    )
    op.drop_table("bot_delivery_records")
