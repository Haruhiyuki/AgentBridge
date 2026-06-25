"""add group role bindings

Revision ID: 0004_group_role_bindings
Revises: 0003_bot_delivery_retry_metadata
Create Date: 2026-06-25
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004_group_role_bindings"
down_revision = "0003_bot_delivery_retry_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "group_role_bindings",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("chat_context_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "chat_context_id",
            "actor_id",
            name="uq_group_role_binding_actor",
        ),
    )
    op.create_index(
        op.f("ix_group_role_bindings_actor_id"),
        "group_role_bindings",
        ["actor_id"],
    )
    op.create_index(
        op.f("ix_group_role_bindings_chat_context_id"),
        "group_role_bindings",
        ["chat_context_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_group_role_bindings_chat_context_id"),
        table_name="group_role_bindings",
    )
    op.drop_index(
        op.f("ix_group_role_bindings_actor_id"),
        table_name="group_role_bindings",
    )
    op.drop_table("group_role_bindings")
