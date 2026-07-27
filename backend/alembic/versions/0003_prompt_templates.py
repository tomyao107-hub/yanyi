"""Add reusable prompt templates and the project-level selection.

Revision ID: 0003_prompt_templates
Revises: 0002_server_foundation
Create Date: 2026-07-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_prompt_templates"
down_revision = "0002_server_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prompt_template",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("name_normalized", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("user_prefix", sa.Text()),
        sa.Column("enabled", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("is_builtin", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_prompt_template_name"),
        sa.CheckConstraint(
            "length(name_normalized) > 0",
            name="ck_prompt_template_name_normalized",
        ),
        sa.CheckConstraint(
            "name_normalized = lower(trim(name_normalized))",
            name="ck_prompt_template_name_canonical",
        ),
        sa.CheckConstraint(
            "length(trim(system_prompt)) > 0",
            name="ck_prompt_template_system_prompt",
        ),
        sa.CheckConstraint(
            "is_default = 0 OR enabled = 1",
            name="ck_prompt_template_default_enabled",
        ),
        sa.UniqueConstraint("name_normalized", name="uq_prompt_template_name_normalized"),
    )
    op.create_index(
        "uq_prompt_template_single_default",
        "prompt_template",
        ["is_default"],
        unique=True,
        sqlite_where=sa.text("is_default = 1"),
    )
    op.create_index("ix_prompt_template_enabled", "prompt_template", ["enabled"])

    with op.batch_alter_table("project") as batch_op:
        batch_op.add_column(sa.Column("prompt_template_id", sa.Integer()))
        batch_op.create_foreign_key(
            "fk_project_prompt_template_id_prompt_template",
            "prompt_template",
            ["prompt_template_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_project_prompt_template_id",
        "project",
        ["prompt_template_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_project_prompt_template_id", table_name="project")
    with op.batch_alter_table("project") as batch_op:
        batch_op.drop_constraint(
            "fk_project_prompt_template_id_prompt_template",
            type_="foreignkey",
        )
        batch_op.drop_column("prompt_template_id")

    op.drop_index("ix_prompt_template_enabled", table_name="prompt_template")
    op.drop_index("uq_prompt_template_single_default", table_name="prompt_template")
    op.drop_table("prompt_template")
