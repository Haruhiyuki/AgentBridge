"""add audit event query columns

Revision ID: 0011_audit_event_query_columns
Revises: 0010_event_created_at_columns
Create Date: 2026-06-26
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op

revision = "0011_audit_event_query_columns"
down_revision = "0010_event_created_at_columns"
branch_labels = None
depends_on = None

audit_events = sa.table(
    "audit_events",
    sa.column("id", sa.String(length=64)),
    sa.column("payload", sa.JSON()),
    sa.column("trace_id", sa.String(length=255)),
    sa.column("chat_context_id", sa.String(length=64)),
    sa.column("project_id", sa.String(length=64)),
    sa.column("session_id", sa.String(length=64)),
    sa.column("interaction_id", sa.String(length=64)),
)


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("trace_id", sa.String(length=255)))
    op.add_column("audit_events", sa.Column("chat_context_id", sa.String(length=64)))
    op.add_column("audit_events", sa.Column("project_id", sa.String(length=64)))
    op.add_column("audit_events", sa.Column("session_id", sa.String(length=64)))
    op.add_column("audit_events", sa.Column("interaction_id", sa.String(length=64)))

    connection = op.get_bind()
    for row in connection.execute(sa.select(audit_events.c.id, audit_events.c.payload)):
        payload = row.payload
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            payload = {}
        connection.execute(
            audit_events.update()
            .where(audit_events.c.id == row.id)
            .values(
                trace_id=payload.get("trace_id"),
                chat_context_id=payload.get("chat_context_id"),
                project_id=payload.get("project_id"),
                session_id=payload.get("session_id"),
                interaction_id=payload.get("interaction_id"),
            )
        )

    op.create_index(op.f("ix_audit_events_trace_id"), "audit_events", ["trace_id"])
    op.create_index(
        op.f("ix_audit_events_chat_context_id"),
        "audit_events",
        ["chat_context_id"],
    )
    op.create_index(op.f("ix_audit_events_project_id"), "audit_events", ["project_id"])
    op.create_index(op.f("ix_audit_events_session_id"), "audit_events", ["session_id"])
    op.create_index(
        op.f("ix_audit_events_interaction_id"),
        "audit_events",
        ["interaction_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_events_interaction_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_session_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_project_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_chat_context_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_trace_id"), table_name="audit_events")
    op.drop_column("audit_events", "interaction_id")
    op.drop_column("audit_events", "session_id")
    op.drop_column("audit_events", "project_id")
    op.drop_column("audit_events", "chat_context_id")
    op.drop_column("audit_events", "trace_id")
