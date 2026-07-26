from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..db import get_session
from ..models import GlossaryTerm, Project
from ..schemas import (
    GlossaryBulkCreate,
    GlossaryImportResult,
    GlossaryTermCreate,
    GlossaryTermPatch,
    GlossaryTermRead,
)

router = APIRouter(tags=["glossary"])
_MAX_GLOSSARY_CSV_BYTES = 5 * 1024 * 1024
_TERM_REQUEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["source_term", "target_term"],
    "properties": {
        "source_term": {"type": "string", "minLength": 1, "maxLength": 1000},
        "target_term": {"type": "string", "minLength": 1, "maxLength": 1000},
        "note": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "case_sensitive": {"type": "boolean", "default": False},
        "enabled": {"type": "boolean", "default": True},
    },
}


def _project_or_404(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _term_or_404(session: Session, term_id: int) -> GlossaryTerm:
    term = session.get(GlossaryTerm, term_id)
    if term is None:
        raise HTTPException(status_code=404, detail="Glossary term not found")
    return term


def _parse_bool(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def _csv_terms(content: bytes) -> list[GlossaryTermCreate]:
    if len(content) > _MAX_GLOSSARY_CSV_BYTES:
        raise ValueError("glossary CSV exceeds 5 MB")
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ValueError("CSV must have a header")
    normalized_headers = {header.strip().lower() for header in reader.fieldnames}
    if not {"source_term", "target_term"}.issubset(normalized_headers):
        raise ValueError("CSV header must include source_term,target_term")
    terms: list[GlossaryTermCreate] = []
    for row_number, raw_row in enumerate(reader, start=2):
        row = {
            (key or "").strip().lower(): value
            for key, value in raw_row.items()
        }
        try:
            terms.append(
                GlossaryTermCreate(
                    source_term=row.get("source_term", ""),
                    target_term=row.get("target_term", ""),
                    note=(row.get("note") or None),
                    case_sensitive=_parse_bool(row.get("case_sensitive"), False),
                    enabled=_parse_bool(row.get("enabled"), True),
                )
            )
        except (ValueError, TypeError) as exc:
            raise ValueError(f"invalid CSV row {row_number}: {exc}") from exc
    if not terms:
        raise ValueError("CSV contains no terms")
    return terms


async def _request_terms(request: Request) -> tuple[list[GlossaryTermCreate], bool]:
    content_type = request.headers.get("content-type", "").lower()
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise ValueError("multipart request must contain a CSV 'file'")
        try:
            content = await upload.read(_MAX_GLOSSARY_CSV_BYTES + 1)
        finally:
            close = getattr(upload, "close", None)
            if callable(close):
                await close()
        overwrite = _parse_bool(form.get("overwrite"), False)
        return _csv_terms(content), overwrite

    data = await request.json()
    if isinstance(data, list):
        return [GlossaryTermCreate.model_validate(item) for item in data], False
    if isinstance(data, dict) and "terms" in data:
        bulk = GlossaryBulkCreate.model_validate(data)
        return bulk.terms, bulk.overwrite
    return [GlossaryTermCreate.model_validate(data)], False


@router.get(
    "/projects/{project_id}/glossary",
    response_model=list[GlossaryTermRead],
    summary="List glossary terms",
)
def list_glossary(
    project_id: int,
    include_disabled: bool = True,
    search: str | None = Query(default=None, min_length=1, max_length=200),
    session: Session = Depends(get_session),
) -> list[GlossaryTermRead]:
    _project_or_404(session, project_id)
    query = select(GlossaryTerm).where(GlossaryTerm.project_id == project_id)
    if not include_disabled:
        query = query.where(GlossaryTerm.enabled.is_(True))
    if search:
        query = query.where(
            GlossaryTerm.source_term.contains(search)
            | GlossaryTerm.target_term.contains(search)
        )
    terms = session.exec(query.order_by(GlossaryTerm.id)).all()
    return [GlossaryTermRead.model_validate(term) for term in terms]


@router.post(
    "/projects/{project_id}/glossary",
    response_model=GlossaryImportResult,
    status_code=status.HTTP_201_CREATED,
    summary="Create glossary terms or import a UTF-8 CSV",
    description=(
        "Accepts one JSON term, {terms: [...], overwrite: true}, a JSON array, "
        "or multipart/form-data with a CSV file. CSV columns: source_term, "
        "target_term, note, case_sensitive, enabled."
    ),
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {
                        "oneOf": [
                            _TERM_REQUEST_SCHEMA,
                            {
                                "type": "object",
                                "required": ["terms"],
                                "properties": {
                                    "terms": {
                                        "type": "array",
                                        "minItems": 1,
                                        "items": _TERM_REQUEST_SCHEMA,
                                    },
                                    "overwrite": {
                                        "type": "boolean",
                                        "default": False,
                                    },
                                },
                            },
                            {
                                "type": "array",
                                "items": _TERM_REQUEST_SCHEMA,
                            },
                        ]
                    }
                },
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "overwrite": {"type": "boolean", "default": False},
                        },
                    }
                },
            }
        }
    },
)
async def create_glossary(
    project_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> GlossaryImportResult:
    _project_or_404(session, project_id)
    try:
        inputs, overwrite = await _request_terms(request)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid glossary input: {exc}") from exc
    if not inputs:
        raise HTTPException(status_code=422, detail="At least one glossary term is required")

    existing = {
        term.source_term: term
        for term in session.exec(
            select(GlossaryTerm).where(GlossaryTerm.project_id == project_id)
        ).all()
    }
    output: list[GlossaryTerm] = []
    created = 0
    updated = 0
    seen: set[str] = set()
    for item in inputs:
        if item.source_term in seen:
            raise HTTPException(
                status_code=422,
                detail=f"Duplicate source_term in request: {item.source_term!r}",
            )
        seen.add(item.source_term)
        term = existing.get(item.source_term)
        if term is not None:
            if not overwrite:
                raise HTTPException(
                    status_code=409,
                    detail=f"Glossary term already exists: {item.source_term!r}",
                )
            for field, value in item.model_dump().items():
                setattr(term, field, value)
            updated += 1
        else:
            term = GlossaryTerm(project_id=project_id, **item.model_dump())
            session.add(term)
            created += 1
        output.append(term)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Glossary term already exists") from exc
    for term in output:
        session.refresh(term)
    return GlossaryImportResult(
        items=[GlossaryTermRead.model_validate(term) for term in output],
        created=created,
        updated=updated,
    )


@router.patch(
    "/glossary/{term_id}",
    response_model=GlossaryTermRead,
    summary="Update a glossary term",
)
def update_glossary(
    term_id: int,
    patch: GlossaryTermPatch,
    session: Session = Depends(get_session),
) -> GlossaryTermRead:
    term = _term_or_404(session, term_id)
    changes = patch.model_dump(exclude_unset=True)
    if not changes:
        return GlossaryTermRead.model_validate(term)
    for key, value in changes.items():
        if key in {"source_term", "target_term"} and value is None:
            raise HTTPException(status_code=422, detail=f"{key} cannot be null")
        if key in {"source_term", "target_term"} and value is not None:
            value = value.strip()
        setattr(term, key, value)
    session.add(term)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="A term with this source_term already exists",
        ) from exc
    session.refresh(term)
    return GlossaryTermRead.model_validate(term)


@router.delete("/glossary/{term_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_glossary(
    term_id: int,
    session: Session = Depends(get_session),
) -> None:
    term = _term_or_404(session, term_id)
    session.delete(term)
    session.commit()
