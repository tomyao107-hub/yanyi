"""Add persistent project runtime logs.

Revision ID: 0004_runtime_logs
Revises: 0003_prompt_templates
Create Date: 2026-08-01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_runtime_logs"
down_revision = "0003_prompt_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runtime_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer()),
        sa.Column("segment_id", sa.Integer()),
        sa.Column("chapter_id", sa.Integer()),
        sa.Column("level", sa.String(length=16), server_default="info", nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details_json", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "level IN ('debug', 'info', 'warning', 'error')",
            name="ck_runtime_log_level",
        ),
        sa.CheckConstraint(
            "length(trim(event_type)) > 0",
            name="ck_runtime_log_event_type",
        ),
        sa.CheckConstraint(
            "length(trim(message)) > 0",
            name="ck_runtime_log_message",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name="fk_runtime_log_project_id_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["job.id"],
            name="fk_runtime_log_job_id_job",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["segment_id"],
            ["segment.id"],
            name="fk_runtime_log_segment_id_segment",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["chapter_id"],
            ["chapter.id"],
            name="fk_runtime_log_chapter_id_chapter",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_runtime_log_project_id_id", "runtime_log", ["project_id", "id"])
    op.create_index("ix_runtime_log_job_id_id", "runtime_log", ["job_id", "id"])
    op.create_index(
        "ix_runtime_log_project_level_id",
        "runtime_log",
        ["project_id", "level", "id"],
    )
    op.create_index("ix_runtime_log_created_at", "runtime_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_runtime_log_created_at", table_name="runtime_log")
    op.drop_index("ix_runtime_log_project_level_id", table_name="runtime_log")
    op.drop_index("ix_runtime_log_job_id_id", table_name="runtime_log")
    op.drop_index("ix_runtime_log_project_id_id", table_name="runtime_log")
    op.drop_table("runtime_log")
