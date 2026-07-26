from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime settings.

    Environment variables use the ``TRANS_`` prefix. Provider API keys are
    deliberately not represented here: LiteLLM reads their standard
    environment variables directly and the API never serializes them.
    """

    model_config = SettingsConfigDict(
        env_prefix="TRANS_",
        env_file=(REPOSITORY_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        toml_file=REPOSITORY_ROOT / "settings.toml",
        populate_by_name=True,
        extra="ignore",
    )

    app_name: str = "AI Translation Workbench"
    app_version: str = "0.1.0"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    host: str = "127.0.0.1"
    port: int = 8000
    api_prefix: str = "/api"
    public_origin: str | None = None
    trusted_hosts: list[str] = Field(
        default_factory=lambda: ["127.0.0.1", "localhost", "testserver"]
    )
    trust_proxy_headers: bool = False

    state_dir: Path | None = None
    data_dir: Path = REPOSITORY_ROOT / "data"
    upload_dir: Path | None = None
    export_dir: Path | None = None
    temp_dir: Path | None = None
    backup_dir: Path | None = None
    frontend_dist: Path | None = None
    database_url: str | None = None
    run_migrations_on_startup: bool = True

    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ]
    )
    max_upload_mb: int = Field(default=250, ge=1, le=4096)
    sse_heartbeat_seconds: float = Field(default=15.0, ge=1.0, le=60.0)
    maintenance_interval_seconds: int = Field(default=3600, ge=60, le=86400)
    audit_retention_days: int = Field(default=90, ge=0, le=3650)
    default_model: str = "gpt-5-mini"
    default_temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    default_max_concurrency: int = Field(default=4, ge=1, le=32)
    generate_chapter_summaries: bool = True
    stream: bool = False
    context_token_budget: int = Field(
        default=1200,
        ge=100,
        le=16000,
        validation_alias=AliasChoices(
            "TRANS_CONTEXT_TOKEN_BUDGET",
            "TRANS_DEFAULT_CONTEXT_TOKEN_BUDGET",
            "context_token_budget",
        ),
    )
    segment_max_chars: int = Field(default=1500, ge=200, le=20000)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Highest priority first: constructor > environment > .env >
        # settings.toml > secrets > model defaults.
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )

    @field_validator(
        "state_dir",
        "data_dir",
        "upload_dir",
        "export_dir",
        "temp_dir",
        "backup_dir",
        "frontend_dist",
        mode="before",
    )
    @classmethod
    def expand_path(cls, value: Any) -> Any:
        if value is None:
            return None
        path = Path(value).expanduser()
        return path if path.is_absolute() else REPOSITORY_ROOT / path

    @field_validator("cors_origins", "trusted_hosts", mode="before")
    @classmethod
    def parse_string_lists(cls, value: Any) -> Any:
        if isinstance(value, str):
            if value.lstrip().startswith("["):
                parsed = json.loads(value)
                if not isinstance(parsed, list):
                    raise ValueError("configuration JSON value must be a list")
                return parsed
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @model_validator(mode="after")
    def validate_production_paths(self) -> Settings:
        if not self.is_production:
            return self
        if self.state_dir is None:
            raise ValueError("state_dir is required in production")
        if not self.state_dir.is_absolute():
            raise ValueError("state_dir must be absolute in production")
        if not self.public_origin or not self.public_origin.startswith("https://"):
            raise ValueError("public_origin must be an explicit HTTPS origin in production")
        if not self.trusted_hosts or "*" in self.trusted_hosts:
            raise ValueError("production trusted_hosts must be explicit")
        if self.cors_origins:
            raise ValueError("production CORS must remain disabled for same-origin deployment")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def resolved_state_dir(self) -> Path:
        return (self.state_dir or self.data_dir).resolve()

    @property
    def resolved_data_dir(self) -> Path:
        # ``data_dir`` remains a compatibility alias for local development.
        return self.resolved_state_dir

    @property
    def resolved_upload_dir(self) -> Path:
        return (self.upload_dir or (self.resolved_state_dir / "uploads")).resolve()

    @property
    def resolved_export_dir(self) -> Path:
        default = (
            self.resolved_state_dir / "exports"
            if self.state_dir
            else REPOSITORY_ROOT / "exports"
        )
        return (self.export_dir or default).resolve()

    @property
    def resolved_temp_dir(self) -> Path:
        return (self.temp_dir or (self.resolved_state_dir / "tmp")).resolve()

    @property
    def resolved_backup_dir(self) -> Path:
        return (self.backup_dir or (self.resolved_state_dir / "backups")).resolve()

    @property
    def resolved_frontend_dist(self) -> Path:
        return (self.frontend_dist or (REPOSITORY_ROOT / "frontend" / "dist")).resolve()

    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            prefix = "sqlite:///"
            if self.database_url.startswith(prefix):
                raw_path = self.database_url.removeprefix(prefix)
                if raw_path != ":memory:":
                    sqlite_path = Path(raw_path)
                    if not sqlite_path.is_absolute():
                        sqlite_path = (REPOSITORY_ROOT / sqlite_path).resolve()
                    return f"{prefix}{sqlite_path.as_posix()}"
            return self.database_url
        return f"sqlite:///{(self.resolved_state_dir / 'trans.db').as_posix()}"

    @property
    def default_provider_config(self) -> dict[str, Any]:
        return {
            "model": self.default_model,
            "temperature": self.default_temperature,
            "max_concurrency": self.default_max_concurrency,
            "context_token_budget": self.context_token_budget,
            "generate_chapter_summaries": self.generate_chapter_summaries,
            "stream": self.stream,
        }

    def ensure_directories(self) -> None:
        self.resolved_state_dir.mkdir(parents=True, exist_ok=True)
        self.resolved_upload_dir.mkdir(parents=True, exist_ok=True)
        self.resolved_export_dir.mkdir(parents=True, exist_ok=True)
        self.resolved_temp_dir.mkdir(parents=True, exist_ok=True)
        self.resolved_backup_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
