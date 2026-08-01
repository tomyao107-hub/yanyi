from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlmodel import Field, SQLModel


def utc_now() -> str:
    """Return a sortable, timezone-aware ISO-8601 timestamp."""

    return datetime.now(UTC).isoformat(timespec="milliseconds")


class Project(SQLModel, table=True):
    __tablename__ = "project"
    __table_args__ = (
        Index("ix_project_source_artifact_id", "source_artifact_id"),
        Index("ix_project_model_profile_id", "model_profile_id"),
        Index("ix_project_prompt_template_id", "prompt_template_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(sa_column=Column(Text, nullable=False))
    source_lang: str = Field(
        default="en", sa_column=Column(String(32), nullable=False, server_default="en")
    )
    target_lang: str = Field(
        default="zh-CN",
        sa_column=Column(String(32), nullable=False, server_default="zh-CN"),
    )
    source_type: str = Field(sa_column=Column(String(16), nullable=False))
    source_path: str = Field(sa_column=Column(Text, nullable=False))
    provider_cfg: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, default=dict, server_default="{}"),
    )
    source_artifact_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey(
                "stored_artifact.id",
                name="fk_project_source_artifact_id_stored_artifact",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    model_profile_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey(
                "model_profile.id",
                name="fk_project_model_profile_id_model_profile",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    prompt_template_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey(
                "prompt_template.id",
                name="fk_project_prompt_template_id_prompt_template",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    status: str = Field(
        default="created",
        sa_column=Column(String(24), nullable=False, server_default="created"),
    )
    created_at: str = Field(default_factory=utc_now, sa_column=Column(Text, nullable=False))
    updated_at: str = Field(default_factory=utc_now, sa_column=Column(Text, nullable=False))


class Chapter(SQLModel, table=True):
    __tablename__ = "chapter"
    __table_args__ = (UniqueConstraint("project_id", "ord", name="uq_chapter_project_ord"),)

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("project.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    ord: int = Field(sa_column=Column(Integer, nullable=False))
    title: str | None = Field(default=None, sa_column=Column(Text))
    href: str | None = Field(default=None, sa_column=Column(Text))
    summary: str | None = Field(default=None, sa_column=Column(Text))


class Segment(SQLModel, table=True):
    __tablename__ = "segment"
    __table_args__ = (
        UniqueConstraint("project_id", "stable_key", name="uq_segment_project_stable_key"),
        Index("ix_segment_proj_status", "project_id", "status"),
        Index("ix_segment_proj_ord", "project_id", "ord"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("project.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    chapter_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("chapter.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    ord: int = Field(sa_column=Column(Integer, nullable=False))
    stable_key: str = Field(sa_column=Column(String(128), nullable=False))
    struct_path: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    source_text: str = Field(sa_column=Column(Text, nullable=False))
    target_text: str | None = Field(default=None, sa_column=Column(Text))
    src_hash: str = Field(sa_column=Column(String(40), nullable=False, index=True))
    status: str = Field(
        default="pending",
        sa_column=Column(String(24), nullable=False, server_default="pending"),
    )
    error_msg: str | None = Field(default=None, sa_column=Column(Text))
    token_in: int | None = Field(default=None, sa_column=Column(Integer))
    token_out: int | None = Field(default=None, sa_column=Column(Integer))
    provider: str | None = Field(default=None, sa_column=Column(Text))
    updated_at: str = Field(default_factory=utc_now, sa_column=Column(Text, nullable=False))


class GlossaryTerm(SQLModel, table=True):
    __tablename__ = "glossary_term"
    __table_args__ = (
        UniqueConstraint("project_id", "source_term", name="uq_glossary_project_source"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("project.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    source_term: str = Field(sa_column=Column(Text, nullable=False))
    target_term: str = Field(sa_column=Column(Text, nullable=False))
    note: str | None = Field(default=None, sa_column=Column(Text))
    case_sensitive: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="0"),
    )
    enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="1"),
    )


class TMEntry(SQLModel, table=True):
    __tablename__ = "tm_entry"
    __table_args__ = (
        UniqueConstraint(
            "src_hash",
            "source_lang",
            "target_lang",
            name="uq_tm_hash_languages",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    src_hash: str = Field(sa_column=Column(String(40), nullable=False, index=True))
    source_lang: str = Field(sa_column=Column(String(32), nullable=False))
    target_lang: str = Field(sa_column=Column(String(32), nullable=False))
    source_text: str = Field(sa_column=Column(Text, nullable=False))
    target_text: str = Field(sa_column=Column(Text, nullable=False))
    hit_count: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )
    updated_at: str = Field(default_factory=utc_now, sa_column=Column(Text, nullable=False))


class AdminUser(SQLModel, table=True):
    __tablename__ = "admin_user"
    __table_args__ = (
        UniqueConstraint("normalized_username", name="uq_admin_user_normalized_username"),
        CheckConstraint("length(trim(username)) > 0", name="ck_admin_user_username_nonempty"),
        CheckConstraint(
            "length(normalized_username) > 0",
            name="ck_admin_user_normalized_username_nonempty",
        ),
        CheckConstraint(
            "normalized_username = lower(trim(normalized_username))",
            name="ck_admin_user_normalized_username_canonical",
        ),
        CheckConstraint("password_version >= 1", name="ck_admin_user_password_version"),
        CheckConstraint("failed_login_count >= 0", name="ck_admin_user_failed_login_count"),
        Index(
            "uq_admin_user_single_enabled",
            "enabled",
            unique=True,
            sqlite_where=text("enabled = 1"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(sa_column=Column(String(150), nullable=False))
    normalized_username: str = Field(sa_column=Column(String(150), nullable=False))
    password_hash: str = Field(sa_column=Column(Text, nullable=False))
    password_hash_algorithm: str = Field(
        default="argon2id",
        sa_column=Column(String(32), nullable=False, server_default="argon2id"),
    )
    password_hash_params: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, default=dict, server_default="{}"),
    )
    password_version: int = Field(
        default=1,
        sa_column=Column(Integer, nullable=False, server_default="1"),
    )
    enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="1"),
    )
    failed_login_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    last_failed_login_at: str | None = Field(default=None, sa_column=Column(Text))
    locked_until: str | None = Field(default=None, sa_column=Column(Text))
    last_login_at: str | None = Field(default=None, sa_column=Column(Text))
    password_changed_at: str = Field(
        default_factory=utc_now,
        sa_column=Column(Text, nullable=False),
    )
    created_at: str = Field(default_factory=utc_now, sa_column=Column(Text, nullable=False))
    updated_at: str = Field(default_factory=utc_now, sa_column=Column(Text, nullable=False))


class AdminSession(SQLModel, table=True):
    __tablename__ = "admin_session"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_admin_session_token_hash"),
        CheckConstraint("length(token_hash) = 64", name="ck_admin_session_token_hash_length"),
        CheckConstraint("password_version >= 1", name="ck_admin_session_password_version"),
        Index("ix_admin_session_user_id", "user_id"),
        Index("ix_admin_session_expires_at", "expires_at"),
        Index("ix_admin_session_idle_expires_at", "idle_expires_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    token_hash: str = Field(sa_column=Column(String(64), nullable=False))
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey(
                "admin_user.id",
                name="fk_admin_session_user_id_admin_user",
                ondelete="CASCADE",
            ),
            nullable=False,
        )
    )
    password_version: int = Field(sa_column=Column(Integer, nullable=False))
    expires_at: str = Field(sa_column=Column(Text, nullable=False))
    idle_expires_at: str = Field(sa_column=Column(Text, nullable=False))
    last_used_at: str = Field(sa_column=Column(Text, nullable=False))
    revoked_at: str | None = Field(default=None, sa_column=Column(Text))
    revoke_reason: str | None = Field(default=None, sa_column=Column(String(64)))
    client_ip_hash: str | None = Field(default=None, sa_column=Column(String(64)))
    user_agent: str | None = Field(default=None, sa_column=Column(String(512)))
    created_at: str = Field(default_factory=utc_now, sa_column=Column(Text, nullable=False))


class ProviderCredential(SQLModel, table=True):
    __tablename__ = "provider_credential"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "profile_label_normalized",
            name="uq_provider_credential_provider_profile",
        ),
        CheckConstraint("length(trim(provider)) > 0", name="ck_provider_credential_provider"),
        CheckConstraint(
            "length(profile_label_normalized) > 0",
            name="ck_provider_credential_profile_label",
        ),
        CheckConstraint(
            "profile_label_normalized = lower(trim(profile_label_normalized))",
            name="ck_provider_credential_profile_label_canonical",
        ),
        CheckConstraint(
            "master_key_version >= 1",
            name="ck_provider_credential_master_key_version",
        ),
        CheckConstraint(
            "test_status IN ('untested', 'testing', 'valid', 'invalid', 'error')",
            name="ck_provider_credential_test_status",
        ),
        Index("ix_provider_credential_provider_enabled", "provider", "enabled"),
    )

    id: int | None = Field(default=None, primary_key=True)
    provider: str = Field(sa_column=Column(String(64), nullable=False))
    profile_label: str = Field(sa_column=Column(String(150), nullable=False))
    profile_label_normalized: str = Field(sa_column=Column(String(150), nullable=False))
    encrypted_ciphertext: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    encryption_nonce: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    master_key_version: int = Field(sa_column=Column(Integer, nullable=False))
    masked_suffix: str | None = Field(default=None, sa_column=Column(String(16)))
    enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="1"),
    )
    test_status: str = Field(
        default="untested",
        sa_column=Column(String(16), nullable=False, server_default="untested"),
    )
    last_tested_at: str | None = Field(default=None, sa_column=Column(Text))
    last_test_error_code: str | None = Field(default=None, sa_column=Column(String(64)))
    last_test_error_summary: str | None = Field(default=None, sa_column=Column(Text))
    created_at: str = Field(default_factory=utc_now, sa_column=Column(Text, nullable=False))
    updated_at: str = Field(default_factory=utc_now, sa_column=Column(Text, nullable=False))


class ModelProfile(SQLModel, table=True):
    __tablename__ = "model_profile"
    __table_args__ = (
        UniqueConstraint("display_name", name="uq_model_profile_display_name"),
        CheckConstraint("length(trim(display_name)) > 0", name="ck_model_profile_display_name"),
        CheckConstraint("length(trim(provider)) > 0", name="ck_model_profile_provider"),
        CheckConstraint(
            "length(trim(litellm_model_id)) > 0",
            name="ck_model_profile_litellm_model_id",
        ),
        CheckConstraint("is_default = 0 OR enabled = 1", name="ck_model_profile_default_enabled"),
        CheckConstraint(
            "max_concurrency >= 1 AND max_concurrency <= 32",
            name="ck_model_profile_max_concurrency",
        ),
        CheckConstraint(
            "context_window_tokens >= 1",
            name="ck_model_profile_context_window_tokens",
        ),
        CheckConstraint(
            "max_output_tokens >= 1 AND max_output_tokens <= context_window_tokens",
            name="ck_model_profile_max_output_tokens",
        ),
        CheckConstraint(
            "input_price_per_million IS NULL OR input_price_per_million >= 0",
            name="ck_model_profile_input_price",
        ),
        CheckConstraint(
            "output_price_per_million IS NULL OR output_price_per_million >= 0",
            name="ck_model_profile_output_price",
        ),
        CheckConstraint(
            "cache_read_price_per_million IS NULL OR cache_read_price_per_million >= 0",
            name="ck_model_profile_cache_read_price",
        ),
        CheckConstraint(
            "cache_write_price_per_million IS NULL OR cache_write_price_per_million >= 0",
            name="ck_model_profile_cache_write_price",
        ),
        Index(
            "uq_model_profile_single_default",
            "is_default",
            unique=True,
            sqlite_where=text("is_default = 1"),
        ),
        Index("ix_model_profile_credential_id", "credential_id"),
        Index("ix_model_profile_provider_enabled", "provider", "enabled"),
    )

    id: int | None = Field(default=None, primary_key=True)
    display_name: str = Field(sa_column=Column(String(150), nullable=False))
    provider: str = Field(sa_column=Column(String(64), nullable=False))
    litellm_model_id: str = Field(sa_column=Column(String(255), nullable=False))
    credential_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey(
                "provider_credential.id",
                name="fk_model_profile_credential_id_provider_credential",
                ondelete="RESTRICT",
            ),
            nullable=True,
        ),
    )
    base_url: str | None = Field(default=None, sa_column=Column(Text))
    enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="1"),
    )
    is_default: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="0"),
    )
    max_concurrency: int = Field(
        default=4,
        sa_column=Column(Integer, nullable=False, server_default="4"),
    )
    context_window_tokens: int = Field(
        default=128000,
        sa_column=Column(Integer, nullable=False, server_default="128000"),
    )
    max_output_tokens: int = Field(
        default=4096,
        sa_column=Column(Integer, nullable=False, server_default="4096"),
    )
    generation_params: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, default=dict, server_default="{}"),
    )
    input_price_per_million: Decimal | None = Field(
        default=None,
        sa_column=Column(Numeric(18, 6), nullable=True),
    )
    output_price_per_million: Decimal | None = Field(
        default=None,
        sa_column=Column(Numeric(18, 6), nullable=True),
    )
    cache_read_price_per_million: Decimal | None = Field(
        default=None,
        sa_column=Column(Numeric(18, 6), nullable=True),
    )
    cache_write_price_per_million: Decimal | None = Field(
        default=None,
        sa_column=Column(Numeric(18, 6), nullable=True),
    )
    created_at: str = Field(default_factory=utc_now, sa_column=Column(Text, nullable=False))
    updated_at: str = Field(default_factory=utc_now, sa_column=Column(Text, nullable=False))


class PromptTemplate(SQLModel, table=True):
    """A reusable translation prompt, selectable per project."""

    __tablename__ = "prompt_template"
    __table_args__ = (
        UniqueConstraint("name_normalized", name="uq_prompt_template_name_normalized"),
        CheckConstraint("length(trim(name)) > 0", name="ck_prompt_template_name"),
        CheckConstraint(
            "length(name_normalized) > 0",
            name="ck_prompt_template_name_normalized",
        ),
        CheckConstraint(
            "name_normalized = lower(trim(name_normalized))",
            name="ck_prompt_template_name_canonical",
        ),
        CheckConstraint(
            "length(trim(system_prompt)) > 0",
            name="ck_prompt_template_system_prompt",
        ),
        CheckConstraint("is_default = 0 OR enabled = 1", name="ck_prompt_template_default_enabled"),
        Index(
            "uq_prompt_template_single_default",
            "is_default",
            unique=True,
            sqlite_where=text("is_default = 1"),
        ),
        Index("ix_prompt_template_enabled", "enabled"),
    )

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(sa_column=Column(String(150), nullable=False))
    name_normalized: str = Field(sa_column=Column(String(150), nullable=False))
    description: str | None = Field(default=None, sa_column=Column(Text))
    # Supports {source_lang}/{target_lang} placeholders, rendered at run time.
    system_prompt: str = Field(sa_column=Column(Text, nullable=False))
    # Optional extra guidance appended to the per-segment user message.
    user_prefix: str | None = Field(default=None, sa_column=Column(Text))
    enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="1"),
    )
    is_default: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="0"),
    )
    is_builtin: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="0"),
    )
    created_at: str = Field(default_factory=utc_now, sa_column=Column(Text, nullable=False))
    updated_at: str = Field(default_factory=utc_now, sa_column=Column(Text, nullable=False))


class StoredArtifact(SQLModel, table=True):
    __tablename__ = "stored_artifact"
    __table_args__ = (
        UniqueConstraint("object_key", name="uq_stored_artifact_object_key"),
        CheckConstraint(
            "kind IN ('source', 'export')",
            name="ck_stored_artifact_kind",
        ),
        CheckConstraint("length(trim(object_key)) > 0", name="ck_stored_artifact_object_key"),
        CheckConstraint(
            "status IN ('pending', 'ready', 'deleting', 'deleted', 'error')",
            name="ck_stored_artifact_status",
        ),
        CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name="ck_stored_artifact_size_bytes",
        ),
        CheckConstraint(
            "sha256 IS NULL OR length(sha256) = 64",
            name="ck_stored_artifact_sha256_length",
        ),
        CheckConstraint(
            "status != 'ready' OR (size_bytes IS NOT NULL AND sha256 IS NOT NULL)",
            name="ck_stored_artifact_ready_metadata",
        ),
        Index("ix_stored_artifact_project_status", "project_id", "status"),
        Index("ix_stored_artifact_sha256", "sha256"),
        Index("ix_stored_artifact_expires_at", "expires_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(sa_column=Column(String(32), nullable=False))
    object_key: str = Field(sa_column=Column(String(255), nullable=False))
    original_filename: str | None = Field(default=None, sa_column=Column(String(512)))
    download_filename: str = Field(sa_column=Column(String(512), nullable=False))
    media_type: str = Field(sa_column=Column(String(255), nullable=False))
    size_bytes: int | None = Field(default=None, sa_column=Column(Integer))
    sha256: str | None = Field(default=None, sa_column=Column(String(64)))
    project_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey(
                "project.id",
                name="fk_stored_artifact_project_id_project",
                ondelete="CASCADE",
            ),
            nullable=True,
        ),
    )
    status: str = Field(
        default="pending",
        sa_column=Column(String(16), nullable=False, server_default="pending"),
    )
    ready_at: str | None = Field(default=None, sa_column=Column(Text))
    expires_at: str | None = Field(default=None, sa_column=Column(Text))
    deleted_at: str | None = Field(default=None, sa_column=Column(Text))
    created_at: str = Field(default_factory=utc_now, sa_column=Column(Text, nullable=False))
    updated_at: str = Field(default_factory=utc_now, sa_column=Column(Text, nullable=False))


class Job(SQLModel, table=True):
    __tablename__ = "job"
    __table_args__ = (
        CheckConstraint(
            "job_type IN ('parse', 'translate', 'retranslate', 'export', 'qa', "
            "'credential_test', 'cleanup', 'plan')",
            name="ck_job_type",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'stopping', 'succeeded', 'failed', "
            "'cancelled', 'interrupted')",
            name="ck_job_status",
        ),
        CheckConstraint("progress_current >= 0", name="ck_job_progress_current"),
        CheckConstraint("progress_total >= 0", name="ck_job_progress_total"),
        CheckConstraint(
            "progress_total = 0 OR progress_current <= progress_total",
            name="ck_job_progress_bounds",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_job_attempt_count"),
        CheckConstraint("max_attempts >= 1", name="ck_job_max_attempts"),
        Index(
            "uq_job_one_active_per_project",
            "project_id",
            unique=True,
            sqlite_where=text(
                "project_id IS NOT NULL AND status IN "
                "('queued', 'running', 'stopping')"
            ),
        ),
        Index("ix_job_status_created_at", "status", "created_at"),
        Index("ix_job_project_created_at", "project_id", "created_at"),
        Index("ix_job_lease_expires_at", "lease_expires_at"),
        Index("ix_job_source_artifact_id", "source_artifact_id"),
        Index("ix_job_result_artifact_id", "result_artifact_id"),
        Index("ix_job_model_profile_id", "model_profile_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    job_type: str = Field(sa_column=Column(String(32), nullable=False))
    status: str = Field(
        default="queued",
        sa_column=Column(String(24), nullable=False, server_default="queued"),
    )
    project_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey(
                "project.id",
                name="fk_job_project_id_project",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    source_artifact_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey(
                "stored_artifact.id",
                name="fk_job_source_artifact_id_stored_artifact",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    result_artifact_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey(
                "stored_artifact.id",
                name="fk_job_result_artifact_id_stored_artifact",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    model_profile_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey(
                "model_profile.id",
                name="fk_job_model_profile_id_model_profile",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    progress_current: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    progress_total: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    attempt_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    max_attempts: int = Field(
        default=5,
        sa_column=Column(Integer, nullable=False, server_default="5"),
    )
    error_code: str | None = Field(default=None, sa_column=Column(String(64)))
    safe_error_summary: str | None = Field(default=None, sa_column=Column(Text))
    cancel_requested_at: str | None = Field(default=None, sa_column=Column(Text))
    cancelled_at: str | None = Field(default=None, sa_column=Column(Text))
    lease_owner: str | None = Field(default=None, sa_column=Column(String(128)))
    lease_expires_at: str | None = Field(default=None, sa_column=Column(Text))
    heartbeat_at: str | None = Field(default=None, sa_column=Column(Text))
    payload_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, default=dict, server_default="{}"),
    )
    result_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, default=dict, server_default="{}"),
    )
    created_at: str = Field(default_factory=utc_now, sa_column=Column(Text, nullable=False))
    updated_at: str = Field(default_factory=utc_now, sa_column=Column(Text, nullable=False))
    started_at: str | None = Field(default=None, sa_column=Column(Text))
    finished_at: str | None = Field(default=None, sa_column=Column(Text))


class RuntimeLog(SQLModel, table=True):
    """Persistent, user-visible execution trace for project background work."""

    __tablename__ = "runtime_log"
    __table_args__ = (
        CheckConstraint(
            "level IN ('debug', 'info', 'warning', 'error')",
            name="ck_runtime_log_level",
        ),
        CheckConstraint("length(trim(event_type)) > 0", name="ck_runtime_log_event_type"),
        CheckConstraint("length(trim(message)) > 0", name="ck_runtime_log_message"),
        Index("ix_runtime_log_project_id_id", "project_id", "id"),
        Index("ix_runtime_log_job_id_id", "job_id", "id"),
        Index("ix_runtime_log_project_level_id", "project_id", "level", "id"),
        Index("ix_runtime_log_created_at", "created_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey(
                "project.id",
                name="fk_runtime_log_project_id_project",
                ondelete="CASCADE",
            ),
            nullable=False,
        )
    )
    job_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey(
                "job.id",
                name="fk_runtime_log_job_id_job",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    segment_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey(
                "segment.id",
                name="fk_runtime_log_segment_id_segment",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    chapter_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey(
                "chapter.id",
                name="fk_runtime_log_chapter_id_chapter",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    level: str = Field(
        default="info",
        sa_column=Column(String(16), nullable=False, server_default="info"),
    )
    event_type: str = Field(sa_column=Column(String(64), nullable=False))
    message: str = Field(sa_column=Column(Text, nullable=False))
    details_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, default=dict, server_default="{}"),
    )
    created_at: str = Field(default_factory=utc_now, sa_column=Column(Text, nullable=False))


class UsageRecord(SQLModel, table=True):
    __tablename__ = "usage_record"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_request_id",
            name="uq_usage_record_provider_request_id",
        ),
        CheckConstraint(
            "outcome IN ('succeeded', 'failed', 'cancelled')",
            name="ck_usage_record_outcome",
        ),
        CheckConstraint("input_tokens >= 0", name="ck_usage_record_input_tokens"),
        CheckConstraint("output_tokens >= 0", name="ck_usage_record_output_tokens"),
        CheckConstraint(
            "cache_read_input_tokens >= 0",
            name="ck_usage_record_cache_read_tokens",
        ),
        CheckConstraint(
            "cache_creation_input_tokens >= 0",
            name="ck_usage_record_cache_creation_tokens",
        ),
        CheckConstraint("reasoning_tokens >= 0", name="ck_usage_record_reasoning_tokens"),
        CheckConstraint("retry_number >= 0", name="ck_usage_record_retry_number"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_usage_record_duration"),
        CheckConstraint(
            "input_price_per_million IS NULL OR input_price_per_million >= 0",
            name="ck_usage_record_input_price",
        ),
        CheckConstraint(
            "output_price_per_million IS NULL OR output_price_per_million >= 0",
            name="ck_usage_record_output_price",
        ),
        CheckConstraint(
            "cache_read_price_per_million IS NULL OR cache_read_price_per_million >= 0",
            name="ck_usage_record_cache_read_price",
        ),
        CheckConstraint(
            "cache_write_price_per_million IS NULL OR cache_write_price_per_million >= 0",
            name="ck_usage_record_cache_write_price",
        ),
        CheckConstraint("total_cost_usd >= 0", name="ck_usage_record_total_cost"),
        Index("ix_usage_record_job_id", "job_id"),
        Index("ix_usage_record_project_created_at", "project_id", "created_at"),
        Index("ix_usage_record_segment_id", "segment_id"),
        Index("ix_usage_record_model_profile_id", "model_profile_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    job_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("job.id", name="fk_usage_record_job_id_job", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    project_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey(
                "project.id",
                name="fk_usage_record_project_id_project",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    segment_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey(
                "segment.id",
                name="fk_usage_record_segment_id_segment",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    model_profile_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey(
                "model_profile.id",
                name="fk_usage_record_model_profile_id_model_profile",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    provider: str = Field(sa_column=Column(String(64), nullable=False))
    model_id: str = Field(sa_column=Column(String(255), nullable=False))
    provider_request_id: str | None = Field(default=None, sa_column=Column(String(255)))
    input_tokens: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    output_tokens: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    cache_read_input_tokens: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    cache_creation_input_tokens: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    reasoning_tokens: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    input_price_per_million: Decimal | None = Field(
        default=None,
        sa_column=Column(Numeric(18, 6), nullable=True),
    )
    output_price_per_million: Decimal | None = Field(
        default=None,
        sa_column=Column(Numeric(18, 6), nullable=True),
    )
    cache_read_price_per_million: Decimal | None = Field(
        default=None,
        sa_column=Column(Numeric(18, 6), nullable=True),
    )
    cache_write_price_per_million: Decimal | None = Field(
        default=None,
        sa_column=Column(Numeric(18, 6), nullable=True),
    )
    total_cost_usd: Decimal = Field(
        default=Decimal("0"),
        sa_column=Column(Numeric(20, 8), nullable=False, server_default="0"),
    )
    outcome: str = Field(sa_column=Column(String(16), nullable=False))
    retry_number: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    started_at: str = Field(sa_column=Column(Text, nullable=False))
    completed_at: str | None = Field(default=None, sa_column=Column(Text))
    duration_ms: int | None = Field(default=None, sa_column=Column(Integer))
    created_at: str = Field(default_factory=utc_now, sa_column=Column(Text, nullable=False))


class AuditEvent(SQLModel, table=True):
    __tablename__ = "audit_event"
    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('admin', 'system', 'anonymous')",
            name="ck_audit_event_actor_type",
        ),
        CheckConstraint(
            "result IN ('succeeded', 'denied', 'failed')",
            name="ck_audit_event_result",
        ),
        CheckConstraint("length(trim(event_type)) > 0", name="ck_audit_event_event_type"),
        Index("ix_audit_event_created_at", "created_at"),
        Index("ix_audit_event_event_type_created_at", "event_type", "created_at"),
        Index("ix_audit_event_actor_user_id_created_at", "actor_user_id", "created_at"),
        Index("ix_audit_event_session_id", "session_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    actor_type: str = Field(sa_column=Column(String(16), nullable=False))
    actor_user_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey(
                "admin_user.id",
                name="fk_audit_event_actor_user_id_admin_user",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    session_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey(
                "admin_session.id",
                name="fk_audit_event_session_id_admin_session",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    event_type: str = Field(sa_column=Column(String(100), nullable=False))
    result: str = Field(sa_column=Column(String(16), nullable=False))
    request_id: str | None = Field(default=None, sa_column=Column(String(64)))
    http_method: str | None = Field(default=None, sa_column=Column(String(16)))
    request_path: str | None = Field(default=None, sa_column=Column(String(512)))
    client_ip_hash: str | None = Field(default=None, sa_column=Column(String(64)))
    user_agent: str | None = Field(default=None, sa_column=Column(String(512)))
    detail_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, default=dict, server_default="{}"),
    )
    created_at: str = Field(default_factory=utc_now, sa_column=Column(Text, nullable=False))


class InstanceLease(SQLModel, table=True):
    __tablename__ = "instance_lease"
    __table_args__ = (
        CheckConstraint("length(trim(lease_name)) > 0", name="ck_instance_lease_name"),
        CheckConstraint("length(trim(holder_id)) > 0", name="ck_instance_lease_holder"),
        CheckConstraint("fencing_token >= 1", name="ck_instance_lease_fencing_token"),
        Index("ix_instance_lease_expires_at", "expires_at"),
    )

    lease_name: str = Field(sa_column=Column(String(100), primary_key=True))
    holder_id: str = Field(sa_column=Column(String(128), nullable=False))
    fencing_token: int = Field(
        default=1,
        sa_column=Column(Integer, nullable=False, server_default="1"),
    )
    acquired_at: str = Field(sa_column=Column(Text, nullable=False))
    heartbeat_at: str = Field(sa_column=Column(Text, nullable=False))
    expires_at: str = Field(sa_column=Column(Text, nullable=False))
