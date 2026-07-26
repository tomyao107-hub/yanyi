from __future__ import annotations

from fastapi import APIRouter, Depends

from ..config import Settings, get_settings
from ..schemas import PublicSettings

router = APIRouter(tags=["settings"])


@router.get(
    "/settings",
    response_model=PublicSettings,
    summary="Get non-secret runtime defaults and supported options",
)
def public_settings(settings: Settings = Depends(get_settings)) -> PublicSettings:
    return PublicSettings(
        app_name=settings.app_name,
        app_version=settings.app_version,
        upload_limit_mb=settings.max_upload_mb,
        supported_source_types=["epub", "md"],
        supported_export_modes=["bilingual", "target_only"],
        provider_defaults=settings.default_provider_config,
        suggested_models=[
            "gpt-5-mini",
            "gpt-5.4-mini",
            "claude-sonnet-5",
            "gemini/gemini-3.6-flash",
            "gemini/gemini-3.5-flash-lite",
            "deepseek/deepseek-v4-flash",
            "deepseek/deepseek-v4-pro",
            "ollama/qwen3",
        ],
        segment_max_chars=settings.segment_max_chars,
    )
