"""add event payload search columns

Revision ID: 0012_event_payload_search_columns
Revises: 0011_audit_event_query_columns
Create Date: 2026-06-26
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op

revision = "0012_event_payload_search_columns"
down_revision = "0011_audit_event_query_columns"
branch_labels = None
depends_on = None

audit_events = sa.table(
    "audit_events",
    sa.column("id", sa.String(length=64)),
    sa.column("payload", sa.JSON()),
    sa.column("details_text", sa.String()),
)

semantic_events = sa.table(
    "semantic_events",
    sa.column("id", sa.String(length=64)),
    sa.column("payload", sa.JSON()),
    sa.column("payload_text", sa.String()),
)


def upgrade() -> None:
    op.add_column(
        "audit_events",
        sa.Column("details_text", sa.String(), nullable=False, server_default=""),
    )
    op.add_column(
        "semantic_events",
        sa.Column("payload_text", sa.String(), nullable=False, server_default=""),
    )

    connection = op.get_bind()
    for row in connection.execute(sa.select(audit_events.c.id, audit_events.c.payload)):
        payload = _payload_dict(row.payload)
        connection.execute(
            audit_events.update()
            .where(audit_events.c.id == row.id)
            .values(details_text=_search_text(payload.get("details") or {}))
        )
    for row in connection.execute(
        sa.select(semantic_events.c.id, semantic_events.c.payload)
    ):
        payload = _payload_dict(row.payload)
        connection.execute(
            semantic_events.update()
            .where(semantic_events.c.id == row.id)
            .values(payload_text=_search_text(payload.get("payload") or {}))
        )

    op.create_index(op.f("ix_audit_events_details_text"), "audit_events", ["details_text"])
    op.create_index(
        op.f("ix_semantic_events_payload_text"),
        "semantic_events",
        ["payload_text"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_semantic_events_payload_text"), table_name="semantic_events")
    op.drop_index(op.f("ix_audit_events_details_text"), table_name="audit_events")
    op.drop_column("semantic_events", "payload_text")
    op.drop_column("audit_events", "details_text")


def _payload_dict(payload: object) -> dict[str, object]:
    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(payload, dict):
        return payload
    return {}


def _search_text(payload: object) -> str:
    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    except TypeError:
        serialized = str(payload)
    return serialized.casefold()
