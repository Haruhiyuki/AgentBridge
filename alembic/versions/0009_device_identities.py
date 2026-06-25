"""add managed device identities

Revision ID: 0009_device_identities
Revises: 0008_semantic_event_query_columns
Create Date: 2026-06-25
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0009_device_identities"
down_revision = "0008_semantic_event_query_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_identities",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id"),
    )
    op.create_index(
        op.f("ix_device_identities_device_id"),
        "device_identities",
        ["device_id"],
    )
    op.create_index(
        op.f("ix_device_identities_status"),
        "device_identities",
        ["status"],
    )
    op.create_index(
        op.f("ix_device_identities_created_at"),
        "device_identities",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_device_identities_created_at"), table_name="device_identities")
    op.drop_index(op.f("ix_device_identities_status"), table_name="device_identities")
    op.drop_index(op.f("ix_device_identities_device_id"), table_name="device_identities")
    op.drop_table("device_identities")
