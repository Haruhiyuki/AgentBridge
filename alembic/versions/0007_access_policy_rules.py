"""add access policy rules

Revision ID: 0007_access_policy_rules
Revises: 0006_bot_delivery_platform_state
Create Date: 2026-06-25
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007_access_policy_rules"
down_revision = "0006_bot_delivery_platform_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "access_policy_rules",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("effect", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=128), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_access_policy_rules_action"), "access_policy_rules", ["action"])
    op.create_index(
        op.f("ix_access_policy_rules_effect"), "access_policy_rules", ["effect"]
    )
    op.create_index(
        op.f("ix_access_policy_rules_enabled"), "access_policy_rules", ["enabled"]
    )
    op.create_index(
        op.f("ix_access_policy_rules_priority"), "access_policy_rules", ["priority"]
    )
    op.create_index(
        op.f("ix_access_policy_rules_resource_id"),
        "access_policy_rules",
        ["resource_id"],
    )
    op.create_index(
        op.f("ix_access_policy_rules_resource_type"),
        "access_policy_rules",
        ["resource_type"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_access_policy_rules_resource_type"), table_name="access_policy_rules")
    op.drop_index(op.f("ix_access_policy_rules_resource_id"), table_name="access_policy_rules")
    op.drop_index(op.f("ix_access_policy_rules_priority"), table_name="access_policy_rules")
    op.drop_index(op.f("ix_access_policy_rules_enabled"), table_name="access_policy_rules")
    op.drop_index(op.f("ix_access_policy_rules_effect"), table_name="access_policy_rules")
    op.drop_index(op.f("ix_access_policy_rules_action"), table_name="access_policy_rules")
    op.drop_table("access_policy_rules")
