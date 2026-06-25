"""add bot delivery platform state

Revision ID: 0006_bot_delivery_platform_state
Revises: 0005_approval_policy_overrides
Create Date: 2026-06-25
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006_bot_delivery_platform_state"
down_revision = "0005_approval_policy_overrides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bot_delivery_records",
        sa.Column(
            "platform_state",
            sa.String(length=64),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "bot_delivery_records",
        sa.Column("acknowledged_at", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "bot_delivery_records",
        sa.Column("edited_at", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "bot_delivery_records",
        sa.Column("deleted_at", sa.String(length=64), nullable=True),
    )
    op.create_index(
        op.f("ix_bot_delivery_records_platform_state"),
        "bot_delivery_records",
        ["platform_state"],
    )
    op.create_index(
        op.f("ix_bot_delivery_records_acknowledged_at"),
        "bot_delivery_records",
        ["acknowledged_at"],
    )
    op.create_index(
        op.f("ix_bot_delivery_records_deleted_at"),
        "bot_delivery_records",
        ["deleted_at"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_bot_delivery_records_deleted_at"),
        table_name="bot_delivery_records",
    )
    op.drop_index(
        op.f("ix_bot_delivery_records_acknowledged_at"),
        table_name="bot_delivery_records",
    )
    op.drop_index(
        op.f("ix_bot_delivery_records_platform_state"),
        table_name="bot_delivery_records",
    )
    op.drop_column("bot_delivery_records", "deleted_at")
    op.drop_column("bot_delivery_records", "edited_at")
    op.drop_column("bot_delivery_records", "acknowledged_at")
    op.drop_column("bot_delivery_records", "platform_state")
