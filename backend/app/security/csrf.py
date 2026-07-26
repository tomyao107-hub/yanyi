from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status

from ..config import Settings

CSRF_COOKIE_NAME = "trans_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def _normalized_origin(value: str) -> tuple[str, str, int | None] | None:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower() if parsed.hostname else ""
    if scheme not in {"http", "https"} or not hostname or parsed.username or parsed.password:
        return None
    port = parsed.port
    if port == (443 if scheme == "https" else 80):
        port = None
    return scheme, hostname, port


def request_origin_is_allowed(request: Request, settings: Settings) -> bool:
    """Require browser requests to identify the configured/current origin."""

    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        return False
    origin = request.headers.get("origin")
    if settings.public_origin:
        expected = _normalized_origin(settings.public_origin)
        if origin:
            return expected is not None and _normalized_origin(origin) == expected
        referer = request.headers.get("referer")
        return bool(referer and expected is not None and _normalized_origin(referer) == expected)

    # Without an explicit public origin, bind browser requests to the trusted Host.
    expected = _normalized_origin(str(request.base_url).rstrip("/"))
    if origin:
        return expected is not None and _normalized_origin(origin) == expected
    referer = request.headers.get("referer")
    if referer:
        return expected is not None and _normalized_origin(referer) == expected
    return not settings.is_production


def enforce_same_origin(request: Request, settings: Settings) -> None:
    if not request_origin_is_allowed(request, settings):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Request origin is not allowed",
        )


def enforce_csrf(request: Request, settings: Settings) -> None:
    if request.method.upper() not in _UNSAFE_METHODS:
        return
    enforce_same_origin(request, settings)
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get(CSRF_HEADER_NAME)
    if (
        not cookie_token
        or not header_token
        or len(cookie_token) > 256
        or not secrets.compare_digest(cookie_token, header_token)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed",
        )
