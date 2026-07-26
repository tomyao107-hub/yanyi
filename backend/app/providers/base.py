from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class TranslationResult:
    text: str
    token_in: int = 0
    token_out: int = 0
    model: str = ""
    request_id: str | None = None
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    reasoning_tokens: int = 0


@runtime_checkable
class TranslationProvider(Protocol):
    async def translate(
        self,
        text: str,
        *,
        system_prompt: str,
        context: str,
        model: str,
        temperature: float = 0.3,
        stream: bool = False,
        api_key: str | None = None,
        api_base: str | None = None,
        completion_options: Mapping[str, Any] | None = None,
    ) -> TranslationResult: ...


class StreamingTranslationProvider(TranslationProvider, Protocol):
    def stream_translate(
        self,
        text: str,
        *,
        system_prompt: str,
        context: str,
        model: str,
        temperature: float = 0.3,
        api_key: str | None = None,
        api_base: str | None = None,
        completion_options: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[str]: ...
