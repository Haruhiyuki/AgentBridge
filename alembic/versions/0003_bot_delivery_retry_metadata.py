"""add bot delivery retry metadata

Revision ID: 0003_bot_delivery_retry_metadata
Revises: 0002_bot_delivery_records
Create Date: 2026-06-25
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003_bot_delivery_retry_metadata"
down_revision = "0002_bot_delivery_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bot_delivery_records",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "bot_delivery_records",
        sa.Column("next_retry_at", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "bot_delivery_records",
        sa.Column("updated_at", sa.String(length=64), nullable=False, server_default=""),
    )
    op.create_index(
        op.f("ix_bot_delivery_records_next_retry_at"),
        "bot_delivery_records",
        ["next_retry_at"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_bot_delivery_records_next_retry_at"),
        table_name="bot_delivery_records",
    )
    op.drop_column("bot_delivery_records", "updated_at")
    op.drop_column("bot_delivery_records", "next_retry_at")
    op.drop_column("bot_delivery_records", "attempt_count")
