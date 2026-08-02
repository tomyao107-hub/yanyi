"""Add release record table for boot/install/rollback history.

Revision ID: 0005_release_record
Revises: 0004_runtime_logs
Create Date: 2026-08-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_release_record"
down_revision = "0004_runtime_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "release_record",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("git_sha", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False, server_default="boot"),
        sa.Column("status", sa.Text(), nullable=False, server_default="ok"),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('boot', 'install', 'rollback')",
            name="ck_release_record_kind",
        ),
        sa.CheckConstraint(
            "status IN ('ok', 'failed')",
            name="ck_release_record_status",
        ),
    )
    op.create_index("ix_release_record_version", "release_record", ["version"])
    op.create_index("ix_release_record_created_at", "release_record", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_release_record_created_at", table_name="release_record")
    op.drop_index("ix_release_record_version", table_name="release_record")
    op.drop_table("release_record")
