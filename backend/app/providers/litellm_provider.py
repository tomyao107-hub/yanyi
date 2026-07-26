from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

from .base import TranslationResult

# Only options that affect inference behavior or bounded transport behavior may
# flow from a stored model profile into LiteLLM. Authentication and routing are
# supplied through their dedicated arguments below.
# SECURITY: extra_headers and metadata removed to prevent credential leakage.
ALLOWED_COMPLETION_OPTIONS = frozenset(
    {
        "cache",
        "cache_control",
        "drop_params",
        "frequency_penalty",
        "logprobs",
        "max_retries",
        "max_tokens",
        "num_retries",
        "presence_penalty",
        "reasoning_effort",
        "response_format",
        "seed",
        "stop",
        "stream_options",
        "temperature",
        "thinking",
        "timeout",
        "top_k",
        "top_logprobs",
        "top_p",
    }
)
_SAMPLING_OPTIONS = ("temperature", "top_p", "top_k")
# These models reject all legacy sampling controls. Prefix/provider wrappers
# are intentionally tolerated because LiteLLM IDs commonly look like
# ``anthropic/claude-opus-4-8``.
_NO_SAMPLING_MODEL_MARKERS = (
    "claude-fable-5",
    "claude-mythos-5",
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-sonnet-5",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
)


def estimate_tokens(text: str, model: str | None = None) -> int:
    if not text:
        return 0
    # tiktoken is not a Claude tokenizer. Use the conservative multilingual
    # fallback for Anthropic models rather than reporting misleading precision.
    if "claude" not in (model or "").casefold():
        try:
            import tiktoken

            try:
                encoding = tiktoken.encoding_for_model(model or "gpt-4o")
            except KeyError:
                encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        except (ImportError, ModuleNotFoundError):
            pass
    cjk = sum("㐀" <= char <= "鿿" for char in text)
    return max(1, cjk + (len(text) - cjk + 3) // 4)


def _value(container: Any, key: str, default: Any = None) -> Any:
    if isinstance(container, dict):
        return container.get(key, default)
    return getattr(container, key, default)


def _nested_value(container: Any, *keys: str, default: Any = None) -> Any:
    current = container
    for key in keys:
        current = _value(current, key)
        if current is None:
            return default
    return current


def _nonnegative_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _content_from_response(response: Any) -> str:
    choices = _value(response, "choices", []) or []
    if not choices:
        return ""
    message = _value(choices[0], "message", {})
    content = _value(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(_value(part, "text", ""))
            for part in content
            if _value(part, "type", "text") in {"text", "output_text"}
        )
    return str(content or "")


def _chunk_content(chunk: Any) -> str:
    choices = _value(chunk, "choices", []) or []
    if not choices:
        return ""
    delta = _value(choices[0], "delta", {})
    content = _value(delta, "content", "")
    return content if isinstance(content, str) else ""


def _usage_metadata(
    response: Any,
    input_text: str,
    output_text: str,
    model: str,
) -> tuple[int, int, int, int, int]:
    usage = _value(response, "usage", {}) or {}
    token_in = _value(usage, "prompt_tokens")
    token_out = _value(usage, "completion_tokens")
    cache_read = _value(usage, "cache_read_input_tokens")
    if cache_read is None:
        cache_read = _nested_value(usage, "prompt_tokens_details", "cached_tokens")
    cache_creation = _value(usage, "cache_creation_input_tokens")
    reasoning = _value(usage, "reasoning_tokens")
    if reasoning is None:
        reasoning = _nested_value(usage, "completion_tokens_details", "reasoning_tokens")
    return (
        _nonnegative_int(token_in, estimate_tokens(input_text, model)),
        _nonnegative_int(token_out, estimate_tokens(output_text, model)),
        _nonnegative_int(cache_read),
        _nonnegative_int(cache_creation),
        _nonnegative_int(reasoning),
    )


def _request_id(response: Any) -> str | None:
    candidates = (
        _value(response, "_request_id"),
        _value(response, "request_id"),
        _nested_value(response, "_hidden_params", "request_id"),
        _nested_value(response, "_hidden_params", "additional_headers", "request-id"),
        _nested_value(response, "_hidden_params", "additional_headers", "x-request-id"),
        _nested_value(response, "response", "headers", "request-id"),
        _nested_value(response, "response", "headers", "x-request-id"),
    )
    for candidate in candidates:
        if candidate is not None:
            value = str(candidate).strip()
            if value:
                return value[:255]
    return None


def _user_message(context: str, text: str) -> str:
    context_section = context.strip() or "（无额外上下文）"
    return (
        "【上下文，仅供理解，不要翻译或复述】\n"
        f"{context_section}\n\n"
        "【待译原文】\n"
        f"{text}"
    )


def supports_temperature(model: str, *, capability: bool | None = None) -> bool:
    """Return whether sampling controls may be sent for a model.

    A provider-discovered capability takes precedence over the conservative
    marker list. ``False`` is useful for profiles sourced from a live models
    endpoint; ``True`` permits sampling for an otherwise unknown model.
    """

    if capability is not None:
        return capability
    normalized = model.casefold()
    return not any(marker in normalized for marker in _NO_SAMPLING_MODEL_MARKERS)


def _allowlisted_options(options: Mapping[str, Any] | None) -> dict[str, Any]:
    if not options:
        return {}
    return {key: value for key, value in options.items() if key in ALLOWED_COMPLETION_OPTIONS}


class LiteLLMProvider:
    """Dependency-lazy LiteLLM wrapper with explicit, request-scoped secrets."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
        supports_sampling_parameters: bool | None = None,
        **completion_options: Any,
    ) -> None:
        self.api_key = api_key
        self.api_base = api_base
        self.supports_sampling_parameters = supports_sampling_parameters
        self.completion_options = _allowlisted_options(completion_options)

    @staticmethod
    def _acompletion() -> Any:
        try:
            from litellm import acompletion
        except ImportError as exc:
            raise RuntimeError(
                "LiteLLM is not installed. Install project dependencies before translating."
            ) from exc
        return acompletion

    def _options(
        self,
        *,
        model: str,
        system_prompt: str,
        user_message: str,
        temperature: float,
        stream: bool,
        api_key: str | None,
        api_base: str | None,
        completion_options: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        options = dict(self.completion_options)
        options.update(_allowlisted_options(completion_options))
        options.update(
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            }
        )
        effective_key = api_key if api_key is not None else self.api_key
        effective_base = api_base if api_base is not None else self.api_base
        if effective_key is not None:
            options["api_key"] = effective_key
        if effective_base is not None:
            options["api_base"] = effective_base
        if stream:
            options["stream"] = True
            stream_options = options.get("stream_options")
            if stream_options is None:
                options["stream_options"] = {"include_usage": True}
        sampling_capability = self.supports_sampling_parameters
        if completion_options and "supports_sampling_parameters" in completion_options:
            value = completion_options["supports_sampling_parameters"]
            if isinstance(value, bool):
                sampling_capability = value
        if supports_temperature(model, capability=sampling_capability):
            options["temperature"] = temperature
        else:
            for unsupported in _SAMPLING_OPTIONS:
                options.pop(unsupported, None)
        return options

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
    ) -> TranslationResult:
        user_message = _user_message(context, text)
        options = self._options(
            model=model,
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=temperature,
            stream=stream,
            api_key=api_key,
            api_base=api_base,
            completion_options=completion_options,
        )
        response = await self._acompletion()(**options)
        if stream:
            chunks: list[str] = []
            last_metadata: Any = None
            async for chunk in response:
                last_metadata = chunk
                content = _chunk_content(chunk)
                if content:
                    chunks.append(content)
            output = "".join(chunks).strip()
            if not output:
                raise ValueError("Provider returned an empty translation")
            actual_model = str(_value(last_metadata, "model", model) or model)
            token_in, token_out, cache_read, cache_creation, reasoning = _usage_metadata(
                last_metadata,
                system_prompt + user_message,
                output,
                actual_model,
            )
            return TranslationResult(
                text=output,
                token_in=token_in,
                token_out=token_out,
                model=actual_model,
                request_id=_request_id(last_metadata),
                cache_read_input_tokens=cache_read,
                cache_creation_input_tokens=cache_creation,
                reasoning_tokens=reasoning,
            )

        output = _content_from_response(response).strip()
        if not output:
            raise ValueError("Provider returned an empty translation")
        actual_model = str(_value(response, "model", model) or model)
        token_in, token_out, cache_read, cache_creation, reasoning = _usage_metadata(
            response,
            system_prompt + user_message,
            output,
            actual_model,
        )
        return TranslationResult(
            text=output,
            token_in=token_in,
            token_out=token_out,
            model=actual_model,
            request_id=_request_id(response),
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_creation,
            reasoning_tokens=reasoning,
        )

    async def stream_translate(
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
    ) -> AsyncIterator[str]:
        options = self._options(
            model=model,
            system_prompt=system_prompt,
            user_message=_user_message(context, text),
            temperature=temperature,
            stream=True,
            api_key=api_key,
            api_base=api_base,
            completion_options=completion_options,
        )
        response = await self._acompletion()(**options)
        async for chunk in response:
            content = _chunk_content(chunk)
            if content:
                yield content


__all__ = [
    "ALLOWED_COMPLETION_OPTIONS",
    "LiteLLMProvider",
    "estimate_tokens",
    "supports_temperature",
]
