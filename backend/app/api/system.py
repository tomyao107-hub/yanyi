from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlmodel import Session, select

from ..db import get_session
from ..models import ReleaseRecord
from ..schemas import ReleaseRecordRead, VersionInfo
from ..security.dependencies import AuthenticatedAdminDependency
from ..services.version import (
    code_schema_head,
    db_current_revision,
    load_build_identity,
)

router = APIRouter(tags=["system"])

_RELEASE_QUERY_LIMIT = 15


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.get("/system/version", response_model=VersionInfo)
def system_version(
    response: Response,
    admin: AuthenticatedAdminDependency,
    session: Annotated[Session, Depends(get_session)],
) -> VersionInfo:
    del admin  # route is admin-only; the dependency enforces the gate
    _no_store(response)

    identity = load_build_identity()
    code = code_schema_head()
    db_rev = db_current_revision()

    releases = list(
        session.scalars(
            select(ReleaseRecord)
            .order_by(ReleaseRecord.created_at.desc(), ReleaseRecord.id.desc())
            .limit(_RELEASE_QUERY_LIMIT)
        )
    )

    return VersionInfo(
        version=identity.version,
        git_sha=identity.git_sha,
        build_time=identity.build_time,
        code_schema_head=code,
        db_schema_revision=db_rev,
        schema_ok=(code == db_rev),
        releases=[
            ReleaseRecordRead(
                id=row.id,
                version=row.version,
                git_sha=row.git_sha,
                kind=row.kind,
                status=row.status,
                detail=row.detail,
                created_at=row.created_at,
            )
            for row in releases
        ],
        release_commands=[
            "python3 deploy/release.py status",
            "python3 deploy/release.py install trans-<版本>.tar.gz",
            "python3 deploy/release.py rollback <版本>",
        ],
    )
