from __future__ import annotations

from collections import Counter

from sqlalchemy import case, func
from sqlmodel import Session, select

from ..models import Chapter, Project, Segment
from ..schemas import ChapterRead, Progress, ProjectDetail, ProjectRead


def get_project_or_none(session: Session, project_id: int) -> Project | None:
    return session.get(Project, project_id)


def project_progress(session: Session, project_id: int) -> Progress:
    rows = session.exec(
        select(Segment.status, func.count(Segment.id))
        .where(Segment.project_id == project_id)
        .group_by(Segment.status)
    ).all()
    counts: Counter[str] = Counter()
    for status, count in rows:
        counts[str(status)] = int(count)

    token_row = session.exec(
        select(
            func.coalesce(func.sum(Segment.token_in), 0),
            func.coalesce(func.sum(Segment.token_out), 0),
        ).where(Segment.project_id == project_id)
    ).one()
    total = sum(counts.values())
    completed = counts["done"] + counts["reviewed"]
    return Progress(
        total=total,
        pending=counts["pending"],
        processing=counts["processing"],
        done=counts["done"],
        error=counts["error"],
        reviewed=counts["reviewed"],
        completed=completed,
        percent=round(completed * 100 / total, 2) if total else 0.0,
        token_in=int(token_row[0] or 0),
        token_out=int(token_row[1] or 0),
    )


def project_to_read(session: Session, project: Project) -> ProjectRead:
    return ProjectRead(
        id=project.id or 0,
        title=project.title,
        source_lang=project.source_lang,
        target_lang=project.target_lang,
        source_type=project.source_type,
        provider_cfg=project.provider_cfg,
        model_profile_id=project.model_profile_id,
        prompt_template_id=project.prompt_template_id,
        status=project.status,
        created_at=project.created_at,
        updated_at=project.updated_at,
        progress=project_progress(session, project.id or 0),
    )


def chapter_reads(session: Session, project_id: int) -> list[ChapterRead]:
    rows = session.exec(
        select(
            Chapter,
            func.count(Segment.id),
            func.coalesce(
                func.sum(case((Segment.status.in_(("done", "reviewed")), 1), else_=0)),
                0,
            ),
        )
        .outerjoin(Segment, Segment.chapter_id == Chapter.id)
        .where(Chapter.project_id == project_id)
        .group_by(Chapter.id)
        .order_by(Chapter.ord, Chapter.id)
    ).all()
    return [
        ChapterRead(
            id=chapter.id or 0,
            project_id=chapter.project_id,
            ord=chapter.ord,
            title=chapter.title,
            href=chapter.href,
            summary=chapter.summary,
            segment_count=int(segment_count),
            completed_count=int(completed_count),
        )
        for chapter, segment_count, completed_count in rows
    ]


def project_to_detail(session: Session, project: Project) -> ProjectDetail:
    return ProjectDetail.model_validate(
        {
            **project_to_read(session, project).model_dump(),
            "chapters": chapter_reads(session, project.id or 0),
        }
    )

