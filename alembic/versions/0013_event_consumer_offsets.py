"""add event consumer offsets

Revision ID: 0013_event_consumer_offsets
Revises: 0012_event_payload_search_columns
Create Date: 2026-06-26
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0013_event_consumer_offsets"
down_revision = "0012_event_payload_search_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_consumer_offsets",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("stream_id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("consumer_id", sa.String(length=255), nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "stream_id",
            "consumer_id",
            name="uq_event_consumer_offsets_stream_consumer",
        ),
    )
    op.create_index(
        op.f("ix_event_consumer_offsets_stream_id"),
        "event_consumer_offsets",
        ["stream_id"],
    )
    op.create_index(
        op.f("ix_event_consumer_offsets_session_id"),
        "event_consumer_offsets",
        ["session_id"],
    )
    op.create_index(
        op.f("ix_event_consumer_offsets_consumer_id"),
        "event_consumer_offsets",
        ["consumer_id"],
    )
    op.create_index(
        op.f("ix_event_consumer_offsets_updated_at"),
        "event_consumer_offsets",
        ["updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_event_consumer_offsets_updated_at"),
        table_name="event_consumer_offsets",
    )
    op.drop_index(
        op.f("ix_event_consumer_offsets_consumer_id"),
        table_name="event_consumer_offsets",
    )
    op.drop_index(
        op.f("ix_event_consumer_offsets_session_id"),
        table_name="event_consumer_offsets",
    )
    op.drop_index(
        op.f("ix_event_consumer_offsets_stream_id"),
        table_name="event_consumer_offsets",
    )
    op.drop_table("event_consumer_offsets")
