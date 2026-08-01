from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

ProjectStatus = Literal["created", "parsing", "ready", "translating", "done", "error"]
SegmentStatus = Literal["pending", "processing", "done", "error", "reviewed"]


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProviderConfig(APIModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_concurrency: int | None = Field(default=None, ge=1, le=32)
    context_token_budget: int | None = Field(default=None, ge=100, le=16000)
    context_segments: int | None = Field(default=None, ge=0, le=12)
    generate_chapter_summaries: bool | None = None
    stream: bool | None = None

    def without_none(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class ProjectPatch(APIModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    title: str | None = Field(default=None, min_length=1, max_length=500)
    provider_cfg: ProviderConfig | None = None
    # Explicit null clears the assignment and reverts to the fallback behaviour,
    # so these are distinguished from "absent" via exclude_unset.
    model_profile_id: int | None = None
    prompt_template_id: int | None = None

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("title cannot be blank")
        return value.strip() if value else value


class Progress(APIModel):
    total: int = 0
    pending: int = 0
    processing: int = 0
    done: int = 0
    error: int = 0
    reviewed: int = 0
    completed: int = 0
    percent: float = 0.0
    token_in: int = 0
    token_out: int = 0


class ChapterRead(APIModel):
    id: int
    project_id: int
    ord: int
    title: str | None = None
    href: str | None = None
    summary: str | None = None
    segment_count: int = 0
    completed_count: int = 0


class ProjectRead(APIModel):
    id: int
    title: str
    source_lang: str
    target_lang: str
    source_type: str
    provider_cfg: dict[str, Any]
    model_profile_id: int | None = None
    prompt_template_id: int | None = None
    status: str
    created_at: str
    updated_at: str
    progress: Progress = Field(default_factory=Progress)


class ProjectDetail(ProjectRead):
    chapters: list[ChapterRead] = Field(default_factory=list)


class ProjectList(APIModel):
    items: list[ProjectRead]
    total: int


class SegmentRead(APIModel):
    id: int
    project_id: int
    chapter_id: int
    ord: int
    stable_key: str
    struct_path: dict[str, Any]
    source_text: str
    target_text: str | None = None
    status: str
    error_msg: str | None = None
    token_in: int | None = None
    token_out: int | None = None
    provider: str | None = None
    updated_at: str


class SegmentPage(APIModel):
    items: list[SegmentRead]
    total: int
    page: int
    page_size: int
    pages: int


class SegmentIdList(APIModel):
    ids: list[int]
    total: int


class SegmentPatch(APIModel):
    target_text: str | None = None
    status: Literal["pending", "done", "error", "reviewed"] | None = None

    @model_validator(mode="after")
    def require_change(self) -> SegmentPatch:
        if "target_text" not in self.model_fields_set and self.status is None:
            raise ValueError("target_text or status is required")
        return self


class GlossaryTermCreate(APIModel):
    source_term: str = Field(min_length=1, max_length=1000)
    target_term: str = Field(min_length=1, max_length=1000)
    note: str | None = Field(default=None, max_length=4000)
    case_sensitive: bool = False
    enabled: bool = True

    @field_validator("source_term", "target_term")
    @classmethod
    def strip_nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("term cannot be blank")
        return value


class GlossaryBulkCreate(APIModel):
    terms: list[GlossaryTermCreate] = Field(min_length=1, max_length=10000)
    overwrite: bool = False


class GlossaryTermPatch(APIModel):
    source_term: str | None = Field(default=None, min_length=1, max_length=1000)
    target_term: str | None = Field(default=None, min_length=1, max_length=1000)
    note: str | None = Field(default=None, max_length=4000)
    case_sensitive: bool | None = None
    enabled: bool | None = None


class GlossaryTermRead(GlossaryTermCreate):
    id: int
    project_id: int


class GlossaryImportResult(APIModel):
    items: list[GlossaryTermRead]
    created: int
    updated: int


class QAItem(APIModel):
    code: str
    severity: Literal["error", "warn", "info"]
    message: str
    segment_id: int | None = None
    stable_key: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class QAReport(APIModel):
    project_id: int
    generated_at: str
    issues: list[QAItem]
    counts: dict[str, int]


class ExportOptions(APIModel):
    mode: Literal["bilingual", "target_only"] = "bilingual"
    include_untranslated: bool = True
    format: Literal["epub", "md"] | None = None


class ExportResult(APIModel):
    artifact_id: int
    filename: str
    download_url: str
    path: str  # filesystem path for tests and debugging


class TranslateRequest(APIModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    retry_errors: bool = True
    # Optional scope. With neither field the whole project's eligible segments
    # are translated. ``chapter_id`` limits to one chapter; ``segment_ids``
    # limits to an explicit selection. They may be combined (the intersection).
    # Scoped runs never overwrite existing translations — only pending (and,
    # when retry_errors is set, error) segments in scope are queued.
    chapter_id: int | None = None
    segment_ids: list[int] | None = Field(default=None, max_length=10000)


class TaskState(APIModel):
    project_id: int
    running: bool
    status: str
    message: str | None = None


class RuntimeLogRead(APIModel):
    id: int
    project_id: int
    job_id: int | None = None
    segment_id: int | None = None
    chapter_id: int | None = None
    level: str
    event_type: str
    message: str
    details_json: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class RuntimeLogPage(APIModel):
    items: list[RuntimeLogRead]
    total: int
    page: int
    page_size: int
    pages: int


class CostEstimate(APIModel):
    project_id: int
    model: str
    total_segments: int
    remaining_segments: int
    chapters_to_summarize: int
    estimated_translation_input_tokens: int
    estimated_translation_output_tokens: int
    estimated_summary_input_tokens: int
    estimated_summary_output_tokens: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_total_tokens: int
    input_usd_per_million: float | None = None
    output_usd_per_million: float | None = None
    estimated_cost_usd: float | None = None
    pricing_note: str


class SegmentBulkAction(APIModel):
    """A scoped bulk action.

    With no ``segment_ids``/filters the operation applies to all segments in
    the project; callers should show a confirmation before submitting that.
    """

    action: Literal["mark_reviewed", "set_pending", "retranslate"]
    segment_ids: list[int] | None = Field(default=None, max_length=10000)
    chapter_id: int | None = None
    statuses: list[SegmentStatus] | None = None
    start_translation: bool = True


class BulkActionResult(APIModel):
    project_id: int
    matched: int
    updated: int
    translation_started: bool = False


class ProviderOption(APIModel):
    """A provider the admin may configure, described for the settings UI."""

    name: str
    label: str
    hint: str = ""
    # ``custom``/``ollama`` have no fixed endpoint, so a base URL is mandatory.
    requires_base_url: bool = False


class ProviderCredentialCreate(APIModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    provider: str = Field(min_length=1, max_length=64)
    profile_label: str = Field(min_length=1, max_length=150)
    # SecretStr keeps the key out of tracebacks, logs and error bodies.
    api_key: SecretStr = Field(min_length=1, max_length=4096)


class ProviderCredentialRotate(APIModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    api_key: SecretStr = Field(min_length=1, max_length=4096)


class ProviderCredentialRead(APIModel):
    """A credential as exposed to the UI. Never carries the secret itself."""

    id: int
    provider: str
    profile_label: str
    configured: bool
    masked_key: str
    enabled: bool = True
    test_status: str
    last_tested_at: str | None = None
    last_test_error_code: str | None = None
    last_test_error_summary: str | None = None
    created_at: str
    updated_at: str


class ModelProfileBase(APIModel):
    display_name: str = Field(min_length=1, max_length=150)
    provider: str = Field(min_length=1, max_length=64)
    litellm_model_id: str = Field(min_length=1, max_length=255)
    credential_id: int | None = None
    base_url: str | None = Field(default=None, max_length=2048)
    enabled: bool = True
    is_default: bool = False
    max_concurrency: int = Field(default=4, ge=1, le=32)
    context_window_tokens: int = Field(default=128000, ge=1, le=10_000_000)
    max_output_tokens: int = Field(default=4096, ge=1, le=10_000_000)
    generation_params: dict[str, Any] = Field(default_factory=dict)
    input_price_per_million: float | None = Field(default=None, ge=0)
    output_price_per_million: float | None = Field(default=None, ge=0)
    cache_read_price_per_million: float | None = Field(default=None, ge=0)
    cache_write_price_per_million: float | None = Field(default=None, ge=0)


class ModelProfileCreate(ModelProfileBase):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ModelProfilePatch(APIModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    display_name: str | None = Field(default=None, min_length=1, max_length=150)
    provider: str | None = Field(default=None, min_length=1, max_length=64)
    litellm_model_id: str | None = Field(default=None, min_length=1, max_length=255)
    credential_id: int | None = None
    base_url: str | None = Field(default=None, max_length=2048)
    enabled: bool | None = None
    is_default: bool | None = None
    max_concurrency: int | None = Field(default=None, ge=1, le=32)
    context_window_tokens: int | None = Field(default=None, ge=1, le=10_000_000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=10_000_000)
    generation_params: dict[str, Any] | None = None
    input_price_per_million: float | None = Field(default=None, ge=0)
    output_price_per_million: float | None = Field(default=None, ge=0)
    cache_read_price_per_million: float | None = Field(default=None, ge=0)
    cache_write_price_per_million: float | None = Field(default=None, ge=0)


class ModelProfileRead(ModelProfileBase):
    id: int
    # True when a key would travel unencrypted to a non-local host.
    insecure_transport: bool = False
    created_at: str
    updated_at: str


class ConnectionTestResult(APIModel):
    """The outcome of one minimal live request against a model profile.

    Errors are reduced to a stable code plus a short human summary; provider
    exception text is never forwarded, since it can echo request headers.
    """

    ok: bool
    provider: str
    model: str
    tested_at: str
    error_code: str | None = None
    error_summary: str | None = None


class PromptTemplateCreate(APIModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    name: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=500)
    system_prompt: str = Field(min_length=1, max_length=8000)
    user_prefix: str | None = Field(default=None, max_length=2000)
    enabled: bool = True
    is_default: bool = False


class PromptTemplatePatch(APIModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    name: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=500)
    system_prompt: str | None = Field(default=None, min_length=1, max_length=8000)
    user_prefix: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None
    is_default: bool | None = None


class PromptTemplateRead(APIModel):
    id: int
    name: str
    description: str | None = None
    system_prompt: str
    user_prefix: str | None = None
    enabled: bool
    is_default: bool
    is_builtin: bool
    created_at: str
    updated_at: str


class PromptPreviewRequest(APIModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    system_prompt: str | None = Field(default=None, max_length=8000)
    user_prefix: str | None = Field(default=None, max_length=2000)
    template_id: int | None = None
    source_lang: str = Field(default="en", max_length=32)
    target_lang: str = Field(default="zh-CN", max_length=32)


class PromptPreviewResponse(APIModel):
    rendered: str
    placeholders: list[str]


class PublicSettings(APIModel):
    app_name: str
    app_version: str
    upload_limit_mb: int
    supported_source_types: list[str]
    supported_export_modes: list[str]
    provider_defaults: dict[str, Any]
    suggested_models: list[str]
    segment_max_chars: int
    # Providers the admin may configure, with UI hints.
    providers: list[ProviderOption] = Field(default_factory=list)
    generation_param_keys: list[str] = Field(default_factory=list)
    prompt_placeholders: list[str] = Field(default_factory=list)
    # Shown before a connection test, which spends real provider quota.
    connection_test_notice: str = ""
    # True when credentials are protected by an auto-generated development key
    # rather than an operator-managed one; the UI warns in that case.
    credential_key_is_ephemeral: bool = False


class TMStats(APIModel):
    project_id: int
    global_entries: int
    language_pair_entries: int
    total_hits: int
    project_segments: int
    project_tm_matches: int
    reusable_remaining_segments: int
    completed_without_provider: int


class TMEntryRead(APIModel):
    id: int
    src_hash: str
    source_lang: str
    target_lang: str
    source_text: str
    target_text: str
    hit_count: int
    updated_at: str


class TMEntryPage(APIModel):
    items: list[TMEntryRead]
    total: int
    page: int
    page_size: int
    pages: int


class LoginRequest(APIModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    username: str = Field(min_length=1, max_length=150)
    password: SecretStr = Field(min_length=1, max_length=1024)


class AdminRead(APIModel):
    id: int
    username: str


class AuthSessionRead(APIModel):
    authenticated: bool
    admin: AdminRead | None = None
    idle_expires_at: str | None = None
    absolute_expires_at: str | None = None


class CSRFResponse(APIModel):
    token: str


class ChangePasswordRequest(APIModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    current_password: SecretStr = Field(min_length=1, max_length=1024)
    new_password: SecretStr = Field(min_length=12, max_length=1024)


class MessageResponse(APIModel):
    message: str


class HealthResponse(APIModel):
    status: Literal["ok"]
    version: str
