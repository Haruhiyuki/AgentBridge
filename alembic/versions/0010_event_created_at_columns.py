"""add event created-at query columns

Revision ID: 0010_event_created_at_columns
Revises: 0009_device_identities
Create Date: 2026-06-26
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision = "0010_event_created_at_columns"
down_revision = "0009_device_identities"
branch_labels = None
depends_on = None

DEFAULT_CREATED_AT = "1970-01-01T00:00:00.000000+00:00"

audit_events = sa.table(
    "audit_events",
    sa.column("id", sa.String(length=64)),
    sa.column("created_at", sa.String(length=64)),
    sa.column("payload", sa.JSON()),
)

semantic_events = sa.table(
    "semantic_events",
    sa.column("id", sa.String(length=64)),
    sa.column("created_at", sa.String(length=64)),
    sa.column("payload", sa.JSON()),
)


def upgrade() -> None:
    op.add_column(
        "audit_events",
        sa.Column(
            "created_at",
            sa.String(length=64),
            nullable=False,
            server_default=DEFAULT_CREATED_AT,
        ),
    )
    op.add_column(
        "semantic_events",
        sa.Column(
            "created_at",
            sa.String(length=64),
            nullable=False,
            server_default=DEFAULT_CREATED_AT,
        ),
    )

    connection = op.get_bind()
    _backfill_created_at(connection, audit_events)
    _backfill_created_at(connection, semantic_events)

    op.create_index(op.f("ix_audit_events_created_at"), "audit_events", ["created_at"])
    op.create_index(
        op.f("ix_semantic_events_created_at"),
        "semantic_events",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_semantic_events_created_at"), table_name="semantic_events")
    op.drop_index(op.f("ix_audit_events_created_at"), table_name="audit_events")
    op.drop_column("semantic_events", "created_at")
    op.drop_column("audit_events", "created_at")


def _backfill_created_at(connection, table: sa.TableClause) -> None:
    for row in connection.execute(sa.select(table.c.id, table.c.payload)):
        connection.execute(
            table.update()
            .where(table.c.id == row.id)
            .values(created_at=_created_at_key(_payload_created_at(row.payload)))
        )


def _payload_created_at(payload: object) -> object:
    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(payload, dict):
        return payload.get("created_at")
    return None


def _created_at_key(value: object) -> str:
    if not isinstance(value, str):
        return DEFAULT_CREATED_AT
    normalized = value.strip()
    if not normalized:
        return DEFAULT_CREATED_AT
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return DEFAULT_CREATED_AT
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    return parsed.isoformat(timespec="microseconds")
