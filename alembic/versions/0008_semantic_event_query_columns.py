"""add semantic event query columns

Revision ID: 0008_semantic_event_query_columns
Revises: 0007_access_policy_rules
Create Date: 2026-06-25
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op

revision = "0008_semantic_event_query_columns"
down_revision = "0007_access_policy_rules"
branch_labels = None
depends_on = None

semantic_events = sa.table(
    "semantic_events",
    sa.column("id", sa.String(length=64)),
    sa.column("payload", sa.JSON()),
    sa.column("source", sa.String(length=64)),
    sa.column("trace_id", sa.String(length=255)),
    sa.column("project_id", sa.String(length=64)),
    sa.column("session_id", sa.String(length=64)),
    sa.column("turn_id", sa.String(length=64)),
    sa.column("interaction_id", sa.String(length=64)),
)


def upgrade() -> None:
    op.add_column("semantic_events", sa.Column("source", sa.String(length=64), nullable=True))
    op.add_column(
        "semantic_events",
        sa.Column("trace_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "semantic_events",
        sa.Column("project_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "semantic_events",
        sa.Column("session_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "semantic_events",
        sa.Column("turn_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "semantic_events",
        sa.Column("interaction_id", sa.String(length=64), nullable=True),
    )

    connection = op.get_bind()
    for row in connection.execute(sa.select(semantic_events.c.id, semantic_events.c.payload)):
        payload = row.payload
        if isinstance(payload, str):
            payload = json.loads(payload)
        connection.execute(
            semantic_events.update()
            .where(semantic_events.c.id == row.id)
            .values(
                source=payload.get("source"),
                trace_id=payload.get("trace_id"),
                project_id=payload.get("project_id"),
                session_id=payload.get("session_id"),
                turn_id=payload.get("turn_id"),
                interaction_id=payload.get("interaction_id"),
            )
        )

    op.create_index(op.f("ix_semantic_events_source"), "semantic_events", ["source"])
    op.create_index(op.f("ix_semantic_events_trace_id"), "semantic_events", ["trace_id"])
    op.create_index(
        op.f("ix_semantic_events_project_id"),
        "semantic_events",
        ["project_id"],
    )
    op.create_index(
        op.f("ix_semantic_events_session_id"),
        "semantic_events",
        ["session_id"],
    )
    op.create_index(op.f("ix_semantic_events_turn_id"), "semantic_events", ["turn_id"])
    op.create_index(
        op.f("ix_semantic_events_interaction_id"),
        "semantic_events",
        ["interaction_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_semantic_events_interaction_id"), table_name="semantic_events")
    op.drop_index(op.f("ix_semantic_events_turn_id"), table_name="semantic_events")
    op.drop_index(op.f("ix_semantic_events_session_id"), table_name="semantic_events")
    op.drop_index(op.f("ix_semantic_events_project_id"), table_name="semantic_events")
    op.drop_index(op.f("ix_semantic_events_trace_id"), table_name="semantic_events")
    op.drop_index(op.f("ix_semantic_events_source"), table_name="semantic_events")
    op.drop_column("semantic_events", "interaction_id")
    op.drop_column("semantic_events", "turn_id")
    op.drop_column("semantic_events", "session_id")
    op.drop_column("semantic_events", "project_id")
    op.drop_column("semantic_events", "trace_id")
    op.drop_column("semantic_events", "source")
