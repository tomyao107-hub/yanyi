"""Secure provider credentials, model-profile, and prompt-template services."""

from .prompts import (
    PromptTemplateError,
    PromptTemplateNotFoundError,
    PromptTemplateService,
    prompt_template_read_dto,
    resolve_project_prompt,
    seed_builtin_templates,
)
from .providers import (
    CONNECTION_TEST_NOTICE,
    SUPPORTED_PROVIDERS,
    CredentialNotFoundError,
    ModelProfileNotFoundError,
    ProviderConfigurationError,
    ProviderCredentialService,
    ProviderRuntime,
    build_provider_runtime,
    create_provider_for_profile,
    credential_read_dto,
    decrypt_credential,
    model_profile_read_dto,
    rotate_credentials,
    run_connection_test,
)

__all__ = [
    "CONNECTION_TEST_NOTICE",
    "SUPPORTED_PROVIDERS",
    "CredentialNotFoundError",
    "ModelProfileNotFoundError",
    "PromptTemplateError",
    "PromptTemplateNotFoundError",
    "PromptTemplateService",
    "ProviderConfigurationError",
    "ProviderCredentialService",
    "ProviderRuntime",
    "build_provider_runtime",
    "create_provider_for_profile",
    "credential_read_dto",
    "decrypt_credential",
    "model_profile_read_dto",
    "prompt_template_read_dto",
    "resolve_project_prompt",
    "rotate_credentials",
    "run_connection_test",
    "seed_builtin_templates",
]
