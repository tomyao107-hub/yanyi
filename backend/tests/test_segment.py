from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

from backend.app.db import create_db_engine, init_db
from backend.app.models import Chapter as DBChapter
from backend.app.models import Project, Segment
from backend.app.parsers.base import Block, Chapter, DocModel
from backend.app.segment import (
    create_segments,
    join_segment_parts,
    normalize,
    persist_document,
    source_hash,
    split_long_text,
)


def sample_document() -> DocModel:
    return DocModel(
        source_type="md",
        chapters=[
            Chapter(
                ord=0,
                title="One",
                href="one",
                blocks=[
                    Block(
                        kind="para",
                        text="First sentence. Second sentence! Third sentence?",
                        struct_path={"source_type": "md", "token_index": 2},
                    ),
                    Block(
                        kind="code",
                        text="print(1)",
                        struct_path={"source_type": "md", "token_index": 3},
                        translatable=False,
                    ),
                ],
            )
        ],
    )


def test_normalize_and_hash_are_stable() -> None:
    assert normalize("  “Hello”\n world  ") == '"Hello" world'
    assert source_hash("A  B") == source_hash("A\nB")


def test_long_split_is_lossless_and_bounded() -> None:
    text = "One sentence. Two sentences! Three sentences? " * 20
    parts = split_long_text(text, max_chars=80)
    assert len(parts) > 1
    assert join_segment_parts(parts) == text
    assert all(len(part) <= 80 for part in parts)


def test_stable_keys_are_unique_and_repeatable() -> None:
    first = create_segments(sample_document(), max_chars=20)
    second = create_segments(sample_document(), max_chars=20)
    assert [segment.stable_key for segment in first] == [
        segment.stable_key for segment in second
    ]
    assert len({segment.stable_key for segment in first}) == len(first)
    assert all("#" in segment.stable_key for segment in first)
    assert join_segment_parts([segment.source_text for segment in first]) == (
        "First sentence. Second sentence! Third sentence?"
    )


def test_reparse_prunes_stale_rows_and_preserves_unchanged_done(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite:///{(tmp_path / 'ledger.db').as_posix()}")
    init_db(engine)
    initial = DocModel(
        "md",
        [
            Chapter(
                0,
                "One",
                "one",
                [
                    Block("para", "Keep", {"source_type": "md", "token_index": 1}),
                    Block("para", "Delete", {"source_type": "md", "token_index": 4}),
                ],
            )
        ],
    )
    updated = DocModel(
        "md",
        [
            Chapter(
                0,
                "One",
                "one",
                [Block("para", "Keep", {"source_type": "md", "token_index": 1})],
            )
        ],
    )
    with Session(engine) as session:
        project = Project(
            title="Ledger",
            source_type="md",
            source_path=str(tmp_path / "book.md"),
        )
        session.add(project)
        session.commit()
        session.refresh(project)
        assert project.id is not None
        rows = persist_document(session, project.id, initial)
        rows[0].status = "done"
        rows[0].target_text = "保留"
        session.add(rows[0])
        session.commit()
        persist_document(session, project.id, updated)
        remaining = session.exec(
            select(Segment).where(Segment.project_id == project.id)
        ).all()
        assert len(remaining) == 1
        assert remaining[0].status == "done"
        assert remaining[0].target_text == "保留"


def test_reparse_clears_summary_only_when_chapter_source_changes(
    tmp_path: Path,
) -> None:
    engine = create_db_engine(f"sqlite:///{(tmp_path / 'summary.db').as_posix()}")
    init_db(engine)
    initial = sample_document()
    with Session(engine) as session:
        project = Project(
            title="Summary",
            source_type="md",
            source_path=str(tmp_path / "book.md"),
        )
        session.add(project)
        session.commit()
        session.refresh(project)
        assert project.id is not None
        persist_document(session, project.id, initial)
        chapter = session.exec(
            select(DBChapter).where(DBChapter.project_id == project.id)
        ).one()
        chapter.summary = "cached summary"
        session.add(chapter)
        session.commit()

        persist_document(session, project.id, initial)
        session.refresh(chapter)
        assert chapter.summary == "cached summary"

        changed = sample_document()
        changed.chapters[0].blocks[0].text = "The chapter source changed."
        persist_document(session, project.id, changed)
        session.refresh(chapter)
        assert chapter.summary is None
