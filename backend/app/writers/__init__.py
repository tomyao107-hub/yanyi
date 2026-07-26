from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

from ..models import Project, Segment
from .epub_writer import write_epub
from .md_writer import render_markdown, write_markdown


def export_project(
    session: Session,
    project_id: int,
    output_path: str | Path,
    mode: str = "bilingual",
    include_untranslated: bool = True,
) -> Path:
    project = session.get(Project, project_id)
    if project is None:
        raise LookupError(f"Project {project_id} does not exist")
    segments = list(
        session.exec(
            select(Segment)
            .where(Segment.project_id == project_id)
            .order_by(Segment.ord)
        ).all()
    )
    if project.source_type == "epub":
        return write_epub(
            project.source_path,
            segments,
            output_path,
            mode=mode,
            include_untranslated=include_untranslated,
            project=project,
        )
    if project.source_type == "md":
        return write_markdown(
            project.source_path,
            segments,
            output_path,
            mode=mode,
            include_untranslated=include_untranslated,
        )
    raise ValueError(f"Unsupported source type: {project.source_type}")


__all__ = [
    "export_project",
    "render_markdown",
    "write_epub",
    "write_markdown",
]
