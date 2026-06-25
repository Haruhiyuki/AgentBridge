"""add approval policy overrides

Revision ID: 0005_approval_policy_overrides
Revises: 0004_group_role_bindings
Create Date: 2026-06-25
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005_approval_policy_overrides"
down_revision = "0004_group_role_bindings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approval_policy_overrides",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("scope_type", sa.String(length=64), nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_type", "scope_id", name="uq_approval_policy_scope"),
    )
    op.create_index(
        op.f("ix_approval_policy_overrides_scope_id"),
        "approval_policy_overrides",
        ["scope_id"],
    )
    op.create_index(
        op.f("ix_approval_policy_overrides_scope_type"),
        "approval_policy_overrides",
        ["scope_type"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_approval_policy_overrides_scope_type"),
        table_name="approval_policy_overrides",
    )
    op.drop_index(
        op.f("ix_approval_policy_overrides_scope_id"),
        table_name="approval_policy_overrides",
    )
    op.drop_table("approval_policy_overrides")
