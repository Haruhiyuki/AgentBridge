"""initial persistence schema

Revision ID: 0001_initial_persistence
Revises:
Create Date: 2026-06-25
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001_initial_persistence"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index(op.f("ix_projects_slug"), "projects", ["slug"], unique=True)

    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("path", sa.String(length=2048), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_workspaces_project_id"), "workspaces", ["project_id"])

    op.create_table(
        "chat_contexts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("bot_instance_id", sa.String(length=255), nullable=False),
        sa.Column("platform", sa.String(length=255), nullable=False),
        sa.Column("chat_space_id", sa.String(length=255), nullable=False),
        sa.Column("thread_id", sa.String(length=255), nullable=True),
        sa.Column("user_id", sa.String(length=255), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "bot_instance_id",
            "platform",
            "chat_space_id",
            "thread_id",
            "user_id",
            name="uq_chat_context_identity",
        ),
    )

    op.create_table(
        "project_bindings",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("chat_context_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_project_bindings_chat_context_id"),
        "project_bindings",
        ["chat_context_id"],
    )
    op.create_index(
        op.f("ix_project_bindings_project_id"),
        "project_bindings",
        ["project_id"],
    )

    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("short_code", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("short_code"),
    )
    op.create_index(op.f("ix_agent_sessions_project_id"), "agent_sessions", ["project_id"])
    op.create_index(op.f("ix_agent_sessions_short_code"), "agent_sessions", ["short_code"])
    op.create_index(op.f("ix_agent_sessions_status"), "agent_sessions", ["status"])
    op.create_index(op.f("ix_agent_sessions_workspace_id"), "agent_sessions", ["workspace_id"])

    op.create_table(
        "turns",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_turns_session_id"), "turns", ["session_id"])
    op.create_index(op.f("ix_turns_status"), "turns", ["status"])

    op.create_table(
        "interactions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_interactions_session_id"), "interactions", ["session_id"])
    op.create_index(op.f("ix_interactions_status"), "interactions", ["status"])

    op.create_table(
        "writer_leases",
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("session_id"),
    )

    op.create_table(
        "lease_epochs",
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("session_id"),
    )

    op.create_table(
        "command_results",
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("idempotency_key"),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=255), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("entry_hash", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entry_hash"),
        sa.UniqueConstraint("position"),
    )
    op.create_index(op.f("ix_audit_events_action"), "audit_events", ["action"])
    op.create_index(op.f("ix_audit_events_actor_id"), "audit_events", ["actor_id"])

    op.create_table(
        "semantic_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("stream_id", sa.String(length=255), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("position"),
        sa.UniqueConstraint("stream_id", "seq", name="uq_semantic_events_stream_seq"),
    )
    op.create_index(op.f("ix_semantic_events_stream_id"), "semantic_events", ["stream_id"])
    op.create_index(op.f("ix_semantic_events_type"), "semantic_events", ["type"])

    op.create_table(
        "event_stream_offsets",
        sa.Column("stream_id", sa.String(length=255), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("stream_id"),
    )


def downgrade() -> None:
    op.drop_table("event_stream_offsets")
    op.drop_index(op.f("ix_semantic_events_type"), table_name="semantic_events")
    op.drop_index(op.f("ix_semantic_events_stream_id"), table_name="semantic_events")
    op.drop_table("semantic_events")
    op.drop_index(op.f("ix_audit_events_actor_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_action"), table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("command_results")
    op.drop_table("lease_epochs")
    op.drop_table("writer_leases")
    op.drop_index(op.f("ix_interactions_status"), table_name="interactions")
    op.drop_index(op.f("ix_interactions_session_id"), table_name="interactions")
    op.drop_table("interactions")
    op.drop_index(op.f("ix_turns_status"), table_name="turns")
    op.drop_index(op.f("ix_turns_session_id"), table_name="turns")
    op.drop_table("turns")
    op.drop_index(op.f("ix_agent_sessions_workspace_id"), table_name="agent_sessions")
    op.drop_index(op.f("ix_agent_sessions_status"), table_name="agent_sessions")
    op.drop_index(op.f("ix_agent_sessions_short_code"), table_name="agent_sessions")
    op.drop_index(op.f("ix_agent_sessions_project_id"), table_name="agent_sessions")
    op.drop_table("agent_sessions")
    op.drop_index(op.f("ix_project_bindings_project_id"), table_name="project_bindings")
    op.drop_index(op.f("ix_project_bindings_chat_context_id"), table_name="project_bindings")
    op.drop_table("project_bindings")
    op.drop_table("chat_contexts")
    op.drop_index(op.f("ix_workspaces_project_id"), table_name="workspaces")
    op.drop_table("workspaces")
    op.drop_index(op.f("ix_projects_slug"), table_name="projects")
    op.drop_table("projects")
