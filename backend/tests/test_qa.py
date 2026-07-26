from __future__ import annotations

from pathlib import Path

from ebooklib import epub

from backend.app.models import GlossaryTerm, Project, Segment
from backend.app.parsers import parse_document
from backend.app.qa import (
    build_qa_report,
    check_project_structure,
    check_segment,
    extract_placeholders,
)
from backend.app.segment import create_segments


def make_segment(**overrides: object) -> Segment:
    values: dict[str, object] = {
        "id": 1,
        "project_id": 1,
        "chapter_id": 1,
        "ord": 0,
        "stable_key": "0000:00000:12345678",
        "struct_path": {"token_index": 1},
        "source_text": "Hello {name}, welcome to New York.",
        "target_text": "你好，欢迎来到纽约。",
        "src_hash": "0" * 40,
        "status": "done",
    }
    values.update(overrides)
    return Segment(**values)


def test_extract_placeholders_counts_duplicates() -> None:
    placeholders = extract_placeholders("Hi {name}, {{ role }}, {name}, %s, ${value}")
    assert placeholders["{name}"] == 2
    assert placeholders["{{ role }}"] == 1
    assert placeholders["%s"] == 1
    assert placeholders["${value}"] == 1


def test_segment_qa_detects_placeholder_and_glossary_mismatch() -> None:
    segment = make_segment()
    glossary = GlossaryTerm(
        id=2,
        project_id=1,
        source_term="New York",
        target_term="纽约市",
        enabled=True,
    )
    issues = check_segment(segment, [glossary], source_type="md")
    assert {issue.code for issue in issues} >= {
        "placeholder_missing",
        "glossary_mismatch",
    }


def test_missing_translation_is_an_error_without_noisy_followups() -> None:
    segment = make_segment(target_text=None, status="error")
    issues = check_segment(segment, source_type="md")
    assert [issue.code for issue in issues] == ["missing_translation"]
    assert issues[0].severity == "error"


def test_report_aggregates_severities() -> None:
    project = Project(
        id=1,
        title="Book",
        source_type="md",
        source_path="book.md",
    )
    report = build_qa_report(
        project,
        [
            make_segment(id=1, target_text=None, status="pending"),
            make_segment(
                id=2,
                stable_key="0000:00001:12345678",
                source_text="Short",
                target_text="This remains a fairly long English phrase here",
            ),
        ],
    )
    assert report.project_id == 1
    assert report.counts["total"] == len(report.issues)
    assert report.counts["error"] >= 1
    assert report.counts["warn"] >= 1


def test_markdown_structure_check_detects_translation_markup(tmp_path: Path) -> None:
    source_path = tmp_path / "book.md"
    source_path.write_text("Hello world.\n", encoding="utf-8")
    project = Project(
        id=1,
        title="Book",
        source_type="md",
        source_path=str(source_path),
    )
    valid = make_segment(
        struct_path={
            "source_type": "md",
            "token_index": 1,
            "char_start": 0,
            "char_end": 13,
            "inline_source": "Hello world.",
            "inline_offset": 0,
        },
        source_text="Hello world.",
        target_text="你好，世界。",
    )
    assert check_project_structure(project, [valid]) == []

    damaged = make_segment(
        struct_path=valid.struct_path,
        source_text="Hello world.",
        target_text="# Broken heading\n\nExtra paragraph",
    )
    issues = check_project_structure(project, [damaged])
    assert [issue.code for issue in issues] == ["structure_damage"]
    assert issues[0].severity == "error"


def test_markdown_structure_check_validates_inline_protected_content(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "inline.md"
    source_text = "Visit <https://example.com> and run `x=1`.\n"
    source_path.write_text(source_text, encoding="utf-8")
    project = Project(
        id=1,
        title="Book",
        source_type="md",
        source_path=str(source_path),
    )
    path = {
        "source_type": "md",
        "token_index": 1,
        "char_start": 0,
        "char_end": len(source_text),
        "inline_source": source_text.rstrip("\n"),
        "inline_offset": 0,
    }

    valid = make_segment(
        struct_path=path,
        source_text=source_text.rstrip("\n"),
        target_text="访问 <https://example.com> 并运行 `x=1`。",
    )
    assert check_project_structure(project, [valid]) == []

    reordered_plain_text = make_segment(
        struct_path=path,
        source_text=source_text.rstrip("\n"),
        target_text="<https://example.com> 请访问，然后运行 `x=1`。",
    )
    assert check_project_structure(project, [reordered_plain_text]) == []

    changed_href = make_segment(
        struct_path=path,
        source_text=source_text.rstrip("\n"),
        target_text="访问 <https://evil.example> 并运行 `x=1`。",
    )
    assert [issue.code for issue in check_project_structure(project, [changed_href])] == [
        "structure_damage"
    ]

    changed_code = make_segment(
        struct_path=path,
        source_text=source_text.rstrip("\n"),
        target_text="访问 <https://example.com> 并运行 `y=2`。",
    )
    assert [issue.code for issue in check_project_structure(project, [changed_code])] == [
        "structure_damage"
    ]


def test_markdown_structure_check_accepts_safe_table_cell_escaping(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "table.md"
    source_path.write_text(
        "| Key | Value |\n| --- | --- |\n| x | y |\n",
        encoding="utf-8",
    )
    project = Project(
        id=1,
        title="Book",
        source_type="md",
        source_path=str(source_path),
    )
    segments = create_segments(parse_document(source_path))
    for segment in segments:
        segment.status = "done"
        segment.target_text = (
            "甲 | 乙\n第二行" if segment.source_text == "x" else segment.source_text
        )

    assert check_project_structure(project, segments) == []


def test_epub_structure_check_strictly_parses_export(tmp_path: Path) -> None:
    source_path = tmp_path / "book.epub"
    book = epub.EpubBook()
    book.set_identifier("qa-book")
    book.set_title("QA Book")
    book.set_language("en")
    chapter = epub.EpubHtml(title="One", file_name="chapter.xhtml", lang="en")
    chapter.content = b"<html><body><p>Hello world.</p></body></html>"
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.toc = (chapter,)
    book.spine = ["nav", chapter]
    epub.write_epub(str(source_path), book)

    project = Project(
        id=1,
        title="Book",
        source_type="epub",
        source_path=str(source_path),
    )
    assert check_project_structure(project, []) == []
