from __future__ import annotations

import ipaddress
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlmodel import Session

from ..config import Settings, get_settings
from ..db import get_session
from ..schemas import (
    AdminRead,
    AuthSessionRead,
    ChangePasswordRequest,
    CSRFResponse,
    LoginRequest,
    MessageResponse,
)
from ..security.csrf import CSRF_COOKIE_NAME, enforce_same_origin, new_csrf_token
from ..security.dependencies import AuthenticatedAdminDependency
from ..security.sessions import (
    SESSION_ABSOLUTE_TTL,
    SESSION_COOKIE_NAME,
    change_admin_password,
    create_admin_session,
    revoke_session,
    verify_login,
)

router = APIRouter(prefix="/auth", tags=["authentication"])
_UNIFORM_LOGIN_ERROR = "Invalid username or password"


def _client_ip(request: Request) -> str | None:
    # Proxy forwarding is intentionally not interpreted here. The ASGI server's
    # trusted-proxy configuration must first replace request.client safely.
    if request.client is None:
        return None
    try:
        return ipaddress.ip_address(request.client.host).compressed
    except ValueError:
        return "unknown"


def _set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    # strict: the session cookie is never sent on cross-site navigation. The
    # SPA is served same-origin, so this costs nothing and blunts CSRF further.
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=int(SESSION_ABSOLUTE_TTL.total_seconds()),
        secure=settings.is_production,
        httponly=True,
        samesite="strict",
        path="/",
    )


def _set_csrf_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        CSRF_COOKIE_NAME,
        token,
        max_age=int(SESSION_ABSOLUTE_TTL.total_seconds()),
        secure=settings.is_production,
        httponly=False,
        samesite="lax",
        path="/",
    )


def _clear_auth_cookies(response: Response, settings: Settings) -> None:
    for name, httponly, samesite in (
        (SESSION_COOKIE_NAME, True, "strict"),
        (CSRF_COOKIE_NAME, False, "lax"),
    ):
        response.delete_cookie(
            name,
            secure=settings.is_production,
            httponly=httponly,
            samesite=samesite,
            path="/",
        )


@router.post(
    "/login",
    response_model=AuthSessionRead,
    responses={401: {"description": _UNIFORM_LOGIN_ERROR}},
)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthSessionRead:
    enforce_same_origin(request, settings)
    admin = verify_login(
        session,
        username=payload.username,
        password=payload.password.get_secret_value(),
        client_ip=_client_ip(request),
    )
    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_UNIFORM_LOGIN_ERROR,
            headers={"Cache-Control": "no-store"},
        )

    token, record = create_admin_session(
        session,
        admin,
        client_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _set_session_cookie(response, token, settings)
    _set_csrf_cookie(response, new_csrf_token(), settings)
    response.headers["Cache-Control"] = "no-store"
    return AuthSessionRead(
        authenticated=True,
        admin=AdminRead(id=admin.id or 0, username=admin.username),
        idle_expires_at=record.idle_expires_at,
        absolute_expires_at=record.expires_at,
    )


@router.post("/logout", response_model=MessageResponse)
def logout(
    request: Request,
    response: Response,
    authenticated: AuthenticatedAdminDependency,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MessageResponse:
    del authenticated
    revoke_session(session, request.cookies.get(SESSION_COOKIE_NAME))
    _clear_auth_cookies(response, settings)
    response.headers["Cache-Control"] = "no-store"
    return MessageResponse(message="Logged out")


@router.get("/session", response_model=AuthSessionRead)
def current_session(
    response: Response,
    authenticated: AuthenticatedAdminDependency,
) -> AuthSessionRead:
    response.headers["Cache-Control"] = "no-store"
    return AuthSessionRead(
        authenticated=True,
        admin=AdminRead(
            id=authenticated.admin.id or 0,
            username=authenticated.admin.username,
        ),
        idle_expires_at=authenticated.session.idle_expires_at,
        absolute_expires_at=authenticated.session.expires_at,
    )


@router.get("/csrf", response_model=CSRFResponse)
def issue_csrf_token(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    authenticated: AuthenticatedAdminDependency,
) -> CSRFResponse:
    del authenticated
    token = new_csrf_token()
    _set_csrf_cookie(response, token, settings)
    response.headers["Cache-Control"] = "no-store"
    return CSRFResponse(token=token)


@router.post("/change-password", response_model=MessageResponse)
def change_password(
    payload: ChangePasswordRequest,
    response: Response,
    authenticated: AuthenticatedAdminDependency,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MessageResponse:
    try:
        changed = change_admin_password(
            session,
            authenticated.admin,
            current_password=payload.current_password.get_secret_value(),
            new_password=payload.new_password.get_secret_value(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not changed:
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    _clear_auth_cookies(response, settings)
    response.headers["Cache-Control"] = "no-store"
    return MessageResponse(message="Password changed; sign in again")
