from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlmodel import Session

from ..config import Settings, get_settings
from ..db import get_session
from ..models import Project, StoredArtifact
from ..storage import StorageError, StorageService

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


def _safe_download_filename(value: str) -> str:
    filename = Path(value).name
    filename = "".join(character for character in filename if ord(character) >= 32)
    return filename[:512] or "download"


@router.get("/{artifact_id}/download", response_class=FileResponse, summary="Download artifact")
def download_artifact(
    artifact_id: int,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    artifact = session.get(StoredArtifact, artifact_id)
    if (
        artifact is None
        or artifact.kind != "export"
        or artifact.status != "ready"
        or artifact.project_id is None
        or session.get(Project, artifact.project_id) is None
    ):
        raise HTTPException(status_code=404, detail="Artifact not found")

    storage = StorageService(settings)
    try:
        path = storage.object_path(
            artifact.kind,
            artifact.object_key,
            must_exist=True,
        )
    except (FileNotFoundError, StorageError):
        raise HTTPException(status_code=404, detail="Artifact not found") from None

    return FileResponse(
        path,
        media_type=artifact.media_type,
        filename=_safe_download_filename(artifact.download_filename),
    )
