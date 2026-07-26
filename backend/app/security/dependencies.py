from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlmodel import Session

from ..config import Settings, get_settings
from ..db import get_session
from ..models import AdminSession, AdminUser
from .csrf import enforce_csrf
from .sessions import SESSION_COOKIE_NAME, authenticate_session


@dataclass(frozen=True, slots=True)
class AuthenticatedAdmin:
    admin: AdminUser
    session: AdminSession


def require_authenticated_session(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> AuthenticatedAdmin:
    authenticated = authenticate_session(session, token)
    if authenticated is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"Cache-Control": "no-store"},
        )
    enforce_csrf(request, settings)
    request.state.admin_user_id = authenticated.admin.id
    request.state.admin_session_id = authenticated.session.id
    return AuthenticatedAdmin(
        admin=authenticated.admin,
        session=authenticated.session,
    )


AuthenticatedAdminDependency = Annotated[
    AuthenticatedAdmin,
    Depends(require_authenticated_session),
]
