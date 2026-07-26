"""Add the authenticated server-foundation data model.

Revision ID: 0002_server_foundation
Revises: 0001_initial
Create Date: 2026-07-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_server_foundation"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_user",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=150), nullable=False),
        sa.Column("normalized_username", sa.String(length=150), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "password_hash_algorithm",
            sa.String(length=32),
            server_default="argon2id",
            nullable=False,
        ),
        sa.Column("password_hash_params", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("password_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("failed_login_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_failed_login_at", sa.Text()),
        sa.Column("locked_until", sa.Text()),
        sa.Column("last_login_at", sa.Text()),
        sa.Column("password_changed_at", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint("length(trim(username)) > 0", name="ck_admin_user_username_nonempty"),
        sa.CheckConstraint(
            "length(normalized_username) > 0",
            name="ck_admin_user_normalized_username_nonempty",
        ),
        sa.CheckConstraint(
            "normalized_username = lower(trim(normalized_username))",
            name="ck_admin_user_normalized_username_canonical",
        ),
        sa.CheckConstraint("password_version >= 1", name="ck_admin_user_password_version"),
        sa.CheckConstraint("failed_login_count >= 0", name="ck_admin_user_failed_login_count"),
        sa.UniqueConstraint(
            "normalized_username",
            name="uq_admin_user_normalized_username",
        ),
    )
    op.create_index(
        "uq_admin_user_single_enabled",
        "admin_user",
        ["enabled"],
        unique=True,
        sqlite_where=sa.text("enabled = 1"),
    )

    op.create_table(
        "admin_session",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey(
                "admin_user.id",
                name="fk_admin_session_user_id_admin_user",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("password_version", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("idle_expires_at", sa.Text(), nullable=False),
        sa.Column("last_used_at", sa.Text(), nullable=False),
        sa.Column("revoked_at", sa.Text()),
        sa.Column("revoke_reason", sa.String(length=64)),
        sa.Column("client_ip_hash", sa.String(length=64)),
        sa.Column("user_agent", sa.String(length=512)),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "length(token_hash) = 64",
            name="ck_admin_session_token_hash_length",
        ),
        sa.CheckConstraint(
            "password_version >= 1",
            name="ck_admin_session_password_version",
        ),
        sa.UniqueConstraint("token_hash", name="uq_admin_session_token_hash"),
    )
    op.create_index("ix_admin_session_user_id", "admin_session", ["user_id"])
    op.create_index("ix_admin_session_expires_at", "admin_session", ["expires_at"])
    op.create_index(
        "ix_admin_session_idle_expires_at",
        "admin_session",
        ["idle_expires_at"],
    )

    op.create_table(
        "provider_credential",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("profile_label", sa.String(length=150), nullable=False),
        sa.Column("profile_label_normalized", sa.String(length=150), nullable=False),
        sa.Column("encrypted_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("encryption_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("master_key_version", sa.Integer(), nullable=False),
        sa.Column("masked_suffix", sa.String(length=16)),
        sa.Column("enabled", sa.Boolean(), server_default="1", nullable=False),
        sa.Column(
            "test_status",
            sa.String(length=16),
            server_default="untested",
            nullable=False,
        ),
        sa.Column("last_tested_at", sa.Text()),
        sa.Column("last_test_error_code", sa.String(length=64)),
        sa.Column("last_test_error_summary", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "length(trim(provider)) > 0",
            name="ck_provider_credential_provider",
        ),
        sa.CheckConstraint(
            "length(profile_label_normalized) > 0",
            name="ck_provider_credential_profile_label",
        ),
        sa.CheckConstraint(
            "profile_label_normalized = lower(trim(profile_label_normalized))",
            name="ck_provider_credential_profile_label_canonical",
        ),
        sa.CheckConstraint(
            "master_key_version >= 1",
            name="ck_provider_credential_master_key_version",
        ),
        sa.CheckConstraint(
            "test_status IN ('untested', 'testing', 'valid', 'invalid', 'error')",
            name="ck_provider_credential_test_status",
        ),
        sa.UniqueConstraint(
            "provider",
            "profile_label_normalized",
            name="uq_provider_credential_provider_profile",
        ),
    )
    op.create_index(
        "ix_provider_credential_provider_enabled",
        "provider_credential",
        ["provider", "enabled"],
    )

    op.create_table(
        "model_profile",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("display_name", sa.String(length=150), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("litellm_model_id", sa.String(length=255), nullable=False),
        sa.Column(
            "credential_id",
            sa.Integer(),
            sa.ForeignKey(
                "provider_credential.id",
                name="fk_model_profile_credential_id_provider_credential",
                ondelete="RESTRICT",
            ),
        ),
        sa.Column("base_url", sa.Text()),
        sa.Column("enabled", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("max_concurrency", sa.Integer(), server_default="4", nullable=False),
        sa.Column(
            "context_window_tokens",
            sa.Integer(),
            server_default="128000",
            nullable=False,
        ),
        sa.Column(
            "max_output_tokens",
            sa.Integer(),
            server_default="4096",
            nullable=False,
        ),
        sa.Column("generation_params", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("input_price_per_million", sa.Numeric(precision=18, scale=6)),
        sa.Column("output_price_per_million", sa.Numeric(precision=18, scale=6)),
        sa.Column("cache_read_price_per_million", sa.Numeric(precision=18, scale=6)),
        sa.Column("cache_write_price_per_million", sa.Numeric(precision=18, scale=6)),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "length(trim(display_name)) > 0",
            name="ck_model_profile_display_name",
        ),
        sa.CheckConstraint(
            "length(trim(provider)) > 0",
            name="ck_model_profile_provider",
        ),
        sa.CheckConstraint(
            "length(trim(litellm_model_id)) > 0",
            name="ck_model_profile_litellm_model_id",
        ),
        sa.CheckConstraint(
            "is_default = 0 OR enabled = 1",
            name="ck_model_profile_default_enabled",
        ),
        sa.CheckConstraint(
            "max_concurrency >= 1 AND max_concurrency <= 32",
            name="ck_model_profile_max_concurrency",
        ),
        sa.CheckConstraint(
            "context_window_tokens >= 1",
            name="ck_model_profile_context_window_tokens",
        ),
        sa.CheckConstraint(
            "max_output_tokens >= 1 AND max_output_tokens <= context_window_tokens",
            name="ck_model_profile_max_output_tokens",
        ),
        sa.CheckConstraint(
            "input_price_per_million IS NULL OR input_price_per_million >= 0",
            name="ck_model_profile_input_price",
        ),
        sa.CheckConstraint(
            "output_price_per_million IS NULL OR output_price_per_million >= 0",
            name="ck_model_profile_output_price",
        ),
        sa.CheckConstraint(
            "cache_read_price_per_million IS NULL OR cache_read_price_per_million >= 0",
            name="ck_model_profile_cache_read_price",
        ),
        sa.CheckConstraint(
            "cache_write_price_per_million IS NULL OR cache_write_price_per_million >= 0",
            name="ck_model_profile_cache_write_price",
        ),
        sa.UniqueConstraint("display_name", name="uq_model_profile_display_name"),
    )
    op.create_index(
        "uq_model_profile_single_default",
        "model_profile",
        ["is_default"],
        unique=True,
        sqlite_where=sa.text("is_default = 1"),
    )
    op.create_index(
        "ix_model_profile_credential_id",
        "model_profile",
        ["credential_id"],
    )
    op.create_index(
        "ix_model_profile_provider_enabled",
        "model_profile",
        ["provider", "enabled"],
    )

    # SQLite supports cyclic foreign-key declarations as long as both tables are
    # created before rows depending on the cycle are inserted.  The project link
    # is added after this table is present so fresh and upgraded DBs match.
    op.create_table(
        "stored_artifact",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("object_key", sa.String(length=255), nullable=False),
        sa.Column("original_filename", sa.String(length=512)),
        sa.Column("download_filename", sa.String(length=512), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("sha256", sa.String(length=64)),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey(
                "project.id",
                name="fk_stored_artifact_project_id_project",
                ondelete="CASCADE",
            ),
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("ready_at", sa.Text()),
        sa.Column("expires_at", sa.Text()),
        sa.Column("deleted_at", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('source', 'export')",
            name="ck_stored_artifact_kind",
        ),
        sa.CheckConstraint(
            "length(trim(object_key)) > 0",
            name="ck_stored_artifact_object_key",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'ready', 'deleting', 'deleted', 'error')",
            name="ck_stored_artifact_status",
        ),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name="ck_stored_artifact_size_bytes",
        ),
        sa.CheckConstraint(
            "sha256 IS NULL OR length(sha256) = 64",
            name="ck_stored_artifact_sha256_length",
        ),
        sa.CheckConstraint(
            "status != 'ready' OR (size_bytes IS NOT NULL AND sha256 IS NOT NULL)",
            name="ck_stored_artifact_ready_metadata",
        ),
        sa.UniqueConstraint("object_key", name="uq_stored_artifact_object_key"),
    )
    op.create_index(
        "ix_stored_artifact_project_status",
        "stored_artifact",
        ["project_id", "status"],
    )
    op.create_index("ix_stored_artifact_sha256", "stored_artifact", ["sha256"])
    op.create_index(
        "ix_stored_artifact_expires_at",
        "stored_artifact",
        ["expires_at"],
    )

    with op.batch_alter_table("project") as batch_op:
        batch_op.add_column(sa.Column("source_artifact_id", sa.Integer()))
        batch_op.add_column(sa.Column("model_profile_id", sa.Integer()))
        batch_op.create_foreign_key(
            "fk_project_source_artifact_id_stored_artifact",
            "stored_artifact",
            ["source_artifact_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_project_model_profile_id_model_profile",
            "model_profile",
            ["model_profile_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_project_source_artifact_id",
        "project",
        ["source_artifact_id"],
    )
    op.create_index(
        "ix_project_model_profile_id",
        "project",
        ["model_profile_id"],
    )

    op.create_table(
        "job",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="queued", nullable=False),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey(
                "project.id",
                name="fk_job_project_id_project",
                ondelete="SET NULL",
            ),
        ),
        sa.Column(
            "source_artifact_id",
            sa.Integer(),
            sa.ForeignKey(
                "stored_artifact.id",
                name="fk_job_source_artifact_id_stored_artifact",
                ondelete="SET NULL",
            ),
        ),
        sa.Column(
            "result_artifact_id",
            sa.Integer(),
            sa.ForeignKey(
                "stored_artifact.id",
                name="fk_job_result_artifact_id_stored_artifact",
                ondelete="SET NULL",
            ),
        ),
        sa.Column(
            "model_profile_id",
            sa.Integer(),
            sa.ForeignKey(
                "model_profile.id",
                name="fk_job_model_profile_id_model_profile",
                ondelete="SET NULL",
            ),
        ),
        sa.Column("progress_current", sa.Integer(), server_default="0", nullable=False),
        sa.Column("progress_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column("safe_error_summary", sa.Text()),
        sa.Column("cancel_requested_at", sa.Text()),
        sa.Column("cancelled_at", sa.Text()),
        sa.Column("lease_owner", sa.String(length=128)),
        sa.Column("lease_expires_at", sa.Text()),
        sa.Column("heartbeat_at", sa.Text()),
        sa.Column("payload_json", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("result_json", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text()),
        sa.Column("finished_at", sa.Text()),
        sa.CheckConstraint(
            "job_type IN ('parse', 'translate', 'retranslate', 'export', 'qa', "
            "'credential_test', 'cleanup', 'plan')",
            name="ck_job_type",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'stopping', 'succeeded', 'failed', "
            "'cancelled', 'interrupted')",
            name="ck_job_status",
        ),
        sa.CheckConstraint("progress_current >= 0", name="ck_job_progress_current"),
        sa.CheckConstraint("progress_total >= 0", name="ck_job_progress_total"),
        sa.CheckConstraint(
            "progress_total = 0 OR progress_current <= progress_total",
            name="ck_job_progress_bounds",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_job_attempt_count"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_job_max_attempts"),
    )
    op.create_index(
        "uq_job_one_active_per_project",
        "job",
        ["project_id"],
        unique=True,
        sqlite_where=sa.text(
            "project_id IS NOT NULL AND status IN ('queued', 'running', 'stopping')"
        ),
    )
    op.create_index("ix_job_status_created_at", "job", ["status", "created_at"])
    op.create_index("ix_job_project_created_at", "job", ["project_id", "created_at"])
    op.create_index("ix_job_lease_expires_at", "job", ["lease_expires_at"])
    op.create_index("ix_job_source_artifact_id", "job", ["source_artifact_id"])
    op.create_index("ix_job_result_artifact_id", "job", ["result_artifact_id"])
    op.create_index("ix_job_model_profile_id", "job", ["model_profile_id"])

    op.create_table(
        "usage_record",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("job.id", name="fk_usage_record_job_id_job", ondelete="SET NULL"),
        ),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey(
                "project.id",
                name="fk_usage_record_project_id_project",
                ondelete="SET NULL",
            ),
        ),
        sa.Column(
            "segment_id",
            sa.Integer(),
            sa.ForeignKey(
                "segment.id",
                name="fk_usage_record_segment_id_segment",
                ondelete="SET NULL",
            ),
        ),
        sa.Column(
            "model_profile_id",
            sa.Integer(),
            sa.ForeignKey(
                "model_profile.id",
                name="fk_usage_record_model_profile_id_model_profile",
                ondelete="SET NULL",
            ),
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("provider_request_id", sa.String(length=255)),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "cache_read_input_tokens",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "cache_creation_input_tokens",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("reasoning_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("input_price_per_million", sa.Numeric(precision=18, scale=6)),
        sa.Column("output_price_per_million", sa.Numeric(precision=18, scale=6)),
        sa.Column("cache_read_price_per_million", sa.Numeric(precision=18, scale=6)),
        sa.Column("cache_write_price_per_million", sa.Numeric(precision=18, scale=6)),
        sa.Column(
            "total_cost_usd",
            sa.Numeric(precision=20, scale=8),
            server_default="0",
            nullable=False,
        ),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("retry_number", sa.Integer(), server_default="0", nullable=False),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.Text()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('succeeded', 'failed', 'cancelled')",
            name="ck_usage_record_outcome",
        ),
        sa.CheckConstraint("input_tokens >= 0", name="ck_usage_record_input_tokens"),
        sa.CheckConstraint("output_tokens >= 0", name="ck_usage_record_output_tokens"),
        sa.CheckConstraint(
            "cache_read_input_tokens >= 0",
            name="ck_usage_record_cache_read_tokens",
        ),
        sa.CheckConstraint(
            "cache_creation_input_tokens >= 0",
            name="ck_usage_record_cache_creation_tokens",
        ),
        sa.CheckConstraint("reasoning_tokens >= 0", name="ck_usage_record_reasoning_tokens"),
        sa.CheckConstraint("retry_number >= 0", name="ck_usage_record_retry_number"),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_usage_record_duration",
        ),
        sa.CheckConstraint(
            "input_price_per_million IS NULL OR input_price_per_million >= 0",
            name="ck_usage_record_input_price",
        ),
        sa.CheckConstraint(
            "output_price_per_million IS NULL OR output_price_per_million >= 0",
            name="ck_usage_record_output_price",
        ),
        sa.CheckConstraint(
            "cache_read_price_per_million IS NULL OR cache_read_price_per_million >= 0",
            name="ck_usage_record_cache_read_price",
        ),
        sa.CheckConstraint(
            "cache_write_price_per_million IS NULL OR cache_write_price_per_million >= 0",
            name="ck_usage_record_cache_write_price",
        ),
        sa.CheckConstraint("total_cost_usd >= 0", name="ck_usage_record_total_cost"),
        sa.UniqueConstraint(
            "provider",
            "provider_request_id",
            name="uq_usage_record_provider_request_id",
        ),
    )
    op.create_index("ix_usage_record_job_id", "usage_record", ["job_id"])
    op.create_index(
        "ix_usage_record_project_created_at",
        "usage_record",
        ["project_id", "created_at"],
    )
    op.create_index("ix_usage_record_segment_id", "usage_record", ["segment_id"])
    op.create_index(
        "ix_usage_record_model_profile_id",
        "usage_record",
        ["model_profile_id"],
    )

    op.create_table(
        "audit_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey(
                "admin_user.id",
                name="fk_audit_event_actor_user_id_admin_user",
                ondelete="SET NULL",
            ),
        ),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey(
                "admin_session.id",
                name="fk_audit_event_session_id_admin_session",
                ondelete="SET NULL",
            ),
        ),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("request_id", sa.String(length=64)),
        sa.Column("http_method", sa.String(length=16)),
        sa.Column("request_path", sa.String(length=512)),
        sa.Column("client_ip_hash", sa.String(length=64)),
        sa.Column("user_agent", sa.String(length=512)),
        sa.Column("detail_json", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "actor_type IN ('admin', 'system', 'anonymous')",
            name="ck_audit_event_actor_type",
        ),
        sa.CheckConstraint(
            "result IN ('succeeded', 'denied', 'failed')",
            name="ck_audit_event_result",
        ),
        sa.CheckConstraint(
            "length(trim(event_type)) > 0",
            name="ck_audit_event_event_type",
        ),
    )
    op.create_index("ix_audit_event_created_at", "audit_event", ["created_at"])
    op.create_index(
        "ix_audit_event_event_type_created_at",
        "audit_event",
        ["event_type", "created_at"],
    )
    op.create_index(
        "ix_audit_event_actor_user_id_created_at",
        "audit_event",
        ["actor_user_id", "created_at"],
    )
    op.create_index("ix_audit_event_session_id", "audit_event", ["session_id"])

    op.create_table(
        "instance_lease",
        sa.Column("lease_name", sa.String(length=100), primary_key=True),
        sa.Column("holder_id", sa.String(length=128), nullable=False),
        sa.Column("fencing_token", sa.Integer(), server_default="1", nullable=False),
        sa.Column("acquired_at", sa.Text(), nullable=False),
        sa.Column("heartbeat_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "length(trim(lease_name)) > 0",
            name="ck_instance_lease_name",
        ),
        sa.CheckConstraint(
            "length(trim(holder_id)) > 0",
            name="ck_instance_lease_holder",
        ),
        sa.CheckConstraint(
            "fencing_token >= 1",
            name="ck_instance_lease_fencing_token",
        ),
    )
    op.create_index(
        "ix_instance_lease_expires_at",
        "instance_lease",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_instance_lease_expires_at", table_name="instance_lease")
    op.drop_table("instance_lease")

    op.drop_index("ix_audit_event_session_id", table_name="audit_event")
    op.drop_index("ix_audit_event_actor_user_id_created_at", table_name="audit_event")
    op.drop_index("ix_audit_event_event_type_created_at", table_name="audit_event")
    op.drop_index("ix_audit_event_created_at", table_name="audit_event")
    op.drop_table("audit_event")

    op.drop_index("ix_usage_record_model_profile_id", table_name="usage_record")
    op.drop_index("ix_usage_record_segment_id", table_name="usage_record")
    op.drop_index("ix_usage_record_project_created_at", table_name="usage_record")
    op.drop_index("ix_usage_record_job_id", table_name="usage_record")
    op.drop_table("usage_record")

    op.drop_index("ix_job_model_profile_id", table_name="job")
    op.drop_index("ix_job_result_artifact_id", table_name="job")
    op.drop_index("ix_job_source_artifact_id", table_name="job")
    op.drop_index("ix_job_lease_expires_at", table_name="job")
    op.drop_index("ix_job_project_created_at", table_name="job")
    op.drop_index("ix_job_status_created_at", table_name="job")
    op.drop_index("uq_job_one_active_per_project", table_name="job")
    op.drop_table("job")

    op.drop_index("ix_project_model_profile_id", table_name="project")
    op.drop_index("ix_project_source_artifact_id", table_name="project")
    with op.batch_alter_table("project") as batch_op:
        batch_op.drop_constraint(
            "fk_project_model_profile_id_model_profile",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_project_source_artifact_id_stored_artifact",
            type_="foreignkey",
        )
        batch_op.drop_column("model_profile_id")
        batch_op.drop_column("source_artifact_id")

    op.drop_index("ix_stored_artifact_expires_at", table_name="stored_artifact")
    op.drop_index("ix_stored_artifact_sha256", table_name="stored_artifact")
    op.drop_index("ix_stored_artifact_project_status", table_name="stored_artifact")
    op.drop_table("stored_artifact")

    op.drop_index("ix_model_profile_provider_enabled", table_name="model_profile")
    op.drop_index("ix_model_profile_credential_id", table_name="model_profile")
    op.drop_index("uq_model_profile_single_default", table_name="model_profile")
    op.drop_table("model_profile")

    op.drop_index(
        "ix_provider_credential_provider_enabled",
        table_name="provider_credential",
    )
    op.drop_table("provider_credential")

    op.drop_index("ix_admin_session_idle_expires_at", table_name="admin_session")
    op.drop_index("ix_admin_session_expires_at", table_name="admin_session")
    op.drop_index("ix_admin_session_user_id", table_name="admin_session")
    op.drop_table("admin_session")

    op.drop_index("uq_admin_user_single_enabled", table_name="admin_user")
    op.drop_table("admin_user")
