"""Create the translation ledger tables.

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_lang", sa.String(length=32), server_default="en", nullable=False),
        sa.Column(
            "target_lang",
            sa.String(length=32),
            server_default="zh-CN",
            nullable=False,
        ),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("provider_cfg", sa.JSON(), server_default="{}", nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default="created",
            nullable=False,
        ),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )

    op.create_table(
        "chapter",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ord", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("href", sa.Text()),
        sa.Column("summary", sa.Text()),
        sa.UniqueConstraint("project_id", "ord", name="uq_chapter_project_ord"),
    )
    op.create_index("ix_chapter_project_id", "chapter", ["project_id"])

    op.create_table(
        "segment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chapter_id",
            sa.Integer(),
            sa.ForeignKey("chapter.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ord", sa.Integer(), nullable=False),
        sa.Column("stable_key", sa.String(length=128), nullable=False),
        sa.Column("struct_path", sa.JSON(), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("target_text", sa.Text()),
        sa.Column("src_hash", sa.String(length=40), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("error_msg", sa.Text()),
        sa.Column("token_in", sa.Integer()),
        sa.Column("token_out", sa.Integer()),
        sa.Column("provider", sa.Text()),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "stable_key",
            name="uq_segment_project_stable_key",
        ),
    )
    op.create_index("ix_segment_chapter_id", "segment", ["chapter_id"])
    op.create_index("ix_segment_src_hash", "segment", ["src_hash"])
    op.create_index(
        "ix_segment_proj_status",
        "segment",
        ["project_id", "status"],
    )
    op.create_index("ix_segment_proj_ord", "segment", ["project_id", "ord"])

    op.create_table(
        "glossary_term",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_term", sa.Text(), nullable=False),
        sa.Column("target_term", sa.Text(), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column(
            "case_sensitive",
            sa.Boolean(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), server_default="1", nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "source_term",
            name="uq_glossary_project_source",
        ),
    )
    op.create_index(
        "ix_glossary_term_project_id",
        "glossary_term",
        ["project_id"],
    )

    op.create_table(
        "tm_entry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("src_hash", sa.String(length=40), nullable=False),
        sa.Column("source_lang", sa.String(length=32), nullable=False),
        sa.Column("target_lang", sa.String(length=32), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("target_text", sa.Text(), nullable=False),
        sa.Column("hit_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "src_hash",
            "source_lang",
            "target_lang",
            name="uq_tm_hash_languages",
        ),
    )
    op.create_index("ix_tm_entry_src_hash", "tm_entry", ["src_hash"])


def downgrade() -> None:
    op.drop_index("ix_tm_entry_src_hash", table_name="tm_entry")
    op.drop_table("tm_entry")
    op.drop_index("ix_glossary_term_project_id", table_name="glossary_term")
    op.drop_table("glossary_term")
    op.drop_index("ix_segment_proj_ord", table_name="segment")
    op.drop_index("ix_segment_proj_status", table_name="segment")
    op.drop_index("ix_segment_src_hash", table_name="segment")
    op.drop_index("ix_segment_chapter_id", table_name="segment")
    op.drop_table("segment")
    op.drop_index("ix_chapter_project_id", table_name="chapter")
    op.drop_table("chapter")
    op.drop_table("project")

