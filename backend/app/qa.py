from __future__ import annotations

import re
import tempfile
import zipfile
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from lxml import etree
from markdown_it import MarkdownIt
from sqlmodel import Session, select

from .models import GlossaryTerm, Project, Segment, utc_now
from .schemas import QAItem, QAReport

_PLACEHOLDER_RE = re.compile(
    r"""
    \{\{[^{}\n]+\}\}            | # {{ variable }}
    \$\{[^{}\n]+\}              | # ${variable}
    \{[A-Za-z_][\w.:-]*\}       | # {variable}
    %\([A-Za-z_]\w*\)[#0 +\-]?(?:\d+|\*)?(?:\.\d+)?[diouxXeEfFgGcrs%] |
    %(?:[#0 +\-]?(?:\d+|\*)?(?:\.\d+)?[diouxXeEfFgGcrs%]) |
    \[\[[^\[\]\n]+\]\]          | # [[marker]]
    <[A-Za-z][^<>\n]*?>           # HTML/XML-like marker
    """,
    re.VERBOSE,
)
_ENGLISH_RUN_RE = re.compile(
    r"\b[A-Za-z][A-Za-z'’-]*(?:\s+[A-Za-z][A-Za-z'’-]*){4,}\b"
)
_WHITESPACE_RE = re.compile(r"\s+")


def extract_placeholders(text: str) -> Counter[str]:
    return Counter(match.group(0) for match in _PLACEHOLDER_RE.finditer(text or ""))


def _contains(text: str, term: str, case_sensitive: bool) -> bool:
    if case_sensitive:
        return term in text
    return term.casefold() in text.casefold()


def _compact_length(text: str) -> int:
    return len(_WHITESPACE_RE.sub("", text or ""))


def check_segment(
    segment: Segment,
    glossary: Sequence[GlossaryTerm] = (),
    *,
    source_type: str | None = None,
    min_length_ratio: float = 0.3,
    max_length_ratio: float = 3.0,
) -> list[QAItem]:
    """Run non-destructive, segment-level QA checks."""

    issues: list[QAItem] = []
    ref = {
        "segment_id": segment.id,
        "stable_key": segment.stable_key,
    }
    target = segment.target_text or ""
    complete = segment.status in {"done", "reviewed"}

    if not complete or not target.strip():
        reason = (
            "译文为空"
            if not target.strip()
            else f"段落状态为 {segment.status!r}，尚未完成"
        )
        issues.append(
            QAItem(
                code="missing_translation",
                severity="error",
                message=reason,
                **ref,
                details={"status": segment.status},
            )
        )
        # Further target-based checks would only add noise.
        return issues

    source_len = _compact_length(segment.source_text)
    target_len = _compact_length(target)
    if source_len >= 4:
        ratio = target_len / source_len
        if ratio < min_length_ratio or ratio > max_length_ratio:
            issues.append(
                QAItem(
                    code="length_ratio",
                    severity="warn",
                    message=f"译文与原文长度比异常（{ratio:.2f}）",
                    **ref,
                    details={
                        "ratio": round(ratio, 4),
                        "minimum": min_length_ratio,
                        "maximum": max_length_ratio,
                        "source_length": source_len,
                        "target_length": target_len,
                    },
                )
            )

    source_placeholders = extract_placeholders(segment.source_text)
    target_placeholders = extract_placeholders(target)
    missing_placeholders = list((source_placeholders - target_placeholders).elements())
    if missing_placeholders:
        issues.append(
            QAItem(
                code="placeholder_missing",
                severity="warn",
                message="译文丢失了原文中的占位符或标记",
                **ref,
                details={"missing": missing_placeholders},
            )
        )

    for term in glossary:
        if not term.enabled:
            continue
        if _contains(segment.source_text, term.source_term, term.case_sensitive) and not _contains(
            target, term.target_term, term.case_sensitive
        ):
            issues.append(
                QAItem(
                    code="glossary_mismatch",
                    severity="warn",
                    message=f"术语 {term.source_term!r} 未使用指定译名 {term.target_term!r}",
                    **ref,
                    details={
                        "glossary_term_id": term.id,
                        "source_term": term.source_term,
                        "target_term": term.target_term,
                    },
                )
            )

    english_runs = [match.group(0) for match in _ENGLISH_RUN_RE.finditer(target)]
    ascii_letters = sum(character.isascii() and character.isalpha() for character in target)
    visible_chars = max(1, _compact_length(target))
    if english_runs and ascii_letters / visible_chars >= 0.35:
        issues.append(
            QAItem(
                code="english_residual",
                severity="warn",
                message="译文中可能残留了较长的未译英文",
                **ref,
                details={"examples": english_runs[:3]},
            )
        )

    path = segment.struct_path
    path_valid = isinstance(path, dict) and bool(path)
    if path_valid and source_type == "epub":
        path_valid = bool(path.get("file") and path.get("xpath"))
    elif path_valid and source_type == "md":
        path_valid = isinstance(path.get("token_index"), int)
    if not path_valid:
        issues.append(
            QAItem(
                code="invalid_structure_path",
                severity="error",
                message="段落缺少有效的回写定位信息",
                **ref,
                details={"source_type": source_type, "struct_path": path},
            )
        )

    return issues


def _markdown_parser() -> MarkdownIt:
    parser = MarkdownIt("commonmark", {"html": True})
    try:
        parser.enable("table")
    except ValueError:
        pass
    return parser


MarkdownTokenSignature = tuple[str, str, int, str | None, str | None]


def _markdown_structure(text: str) -> list[MarkdownTokenSignature]:
    """Flatten block and inline tokens into a structure-only signature.

    Ordinary text content is deliberately ignored so a translation does not
    produce false positives. Link destinations and inline-code payloads are
    protected content, however, and therefore form part of the signature.
    """

    signatures: list[MarkdownTokenSignature] = []

    def append_token(token: Any) -> None:
        # Plain text nodes may be split, merged, or move around inline markup
        # as part of a natural translation. They carry no Markdown structure.
        if token.type == "text":
            return
        # Writers encode a translated table-cell line break as an inline <br>
        # so the physical Markdown row and column count remain unchanged.
        if token.type == "html_inline" and re.fullmatch(
            r"<br\s*/?>",
            token.content.strip(),
            flags=re.IGNORECASE,
        ):
            return
        href = token.attrGet("href") if token.type == "link_open" else None
        code = token.content if token.type == "code_inline" else None
        signatures.append((token.type, token.tag, token.nesting, href, code))
        for child in token.children or ():
            append_token(child)

    for token in _markdown_parser().parse(text):
        append_token(token)
    return signatures


def _first_difference(
    left: Sequence[MarkdownTokenSignature],
    right: Sequence[MarkdownTokenSignature],
) -> int | None:
    for index, (source, rendered) in enumerate(zip(left, right, strict=False)):
        if source != rendered:
            return index
    return min(len(left), len(right)) if len(left) != len(right) else None


def check_project_structure(
    project: Project,
    segments: Sequence[Segment],
    *,
    temporary_directory: Path | None = None,
) -> list[QAItem]:
    """Render in-memory/to a temp file and verify export structure."""

    source_path = Path(project.source_path)
    if not source_path.is_file():
        return [
            QAItem(
                code="structure_damage",
                severity="error",
                message="无法读取源文件，未能完成回写结构验证",
                details={"reason": "source_not_found"},
            )
        ]

    try:
        if project.source_type == "md":
            from .writers.md_writer import render_markdown

            source = source_path.read_text(encoding="utf-8-sig")
            rendered = render_markdown(
                source,
                segments,
                mode="target_only",
                include_untranslated=True,
            )
            source_structure = _markdown_structure(source)
            rendered_structure = _markdown_structure(rendered)
            mismatch = _first_difference(source_structure, rendered_structure)
            if mismatch is not None:
                return [
                    QAItem(
                        code="structure_damage",
                        severity="error",
                        message="Markdown 回写后的 token 类型或数量与原文不一致",
                        details={
                            "source_token_count": len(source_structure),
                            "rendered_token_count": len(rendered_structure),
                            "first_mismatch": mismatch,
                            "source_token": (
                                source_structure[mismatch]
                                if mismatch < len(source_structure)
                                else None
                            ),
                            "rendered_token": (
                                rendered_structure[mismatch]
                                if mismatch < len(rendered_structure)
                                else None
                            ),
                        },
                    )
                ]
            return []

        if project.source_type == "epub":
            from .writers.epub_writer import write_epub

            temporary_root = (
                temporary_directory.resolve()
                if temporary_directory is not None
                else None
            )
            if temporary_root is not None:
                temporary_root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix="trans-qa-",
                dir=str(temporary_root) if temporary_root is not None else None,
            ) as temporary:
                output_path = Path(temporary) / "qa-target.epub"
                write_epub(
                    source_path,
                    segments,
                    output_path,
                    mode="target_only",
                    include_untranslated=True,
                )
                document_count = 0
                with zipfile.ZipFile(output_path) as archive:
                    damaged_member = archive.testzip()
                    if damaged_member is not None:
                        raise ValueError(f"ZIP CRC failed for {damaged_member}")
                    for name in archive.namelist():
                        if Path(name).suffix.lower() not in {".xhtml", ".html", ".htm"}:
                            continue
                        document_count += 1
                        etree.fromstring(
                            archive.read(name),
                            parser=etree.XMLParser(
                                recover=False,
                                resolve_entities=False,
                                no_network=True,
                            ),
                        )
                if document_count == 0:
                    raise ValueError("export contains no XHTML/HTML documents")
            return []
    except Exception as exc:
        return [
            QAItem(
                code="structure_damage",
                severity="error",
                message=f"{project.source_type.upper()} 回写结构验证失败",
                details={"reason": "structure_validation_failed", "error_type": type(exc).__name__},
            )
        ]

    return [
        QAItem(
            code="structure_damage",
            severity="error",
            message=f"不支持对 {project.source_type!r} 执行结构验证",
            details={"source_type": project.source_type},
        )
    ]


def build_qa_report(
    project: Project,
    segments: Iterable[Segment],
    glossary: Sequence[GlossaryTerm] = (),
    *,
    temporary_directory: Path | None = None,
) -> QAReport:
    segment_rows = list(segments)
    issues: list[QAItem] = []
    for segment in segment_rows:
        issues.extend(
            check_segment(segment, glossary, source_type=project.source_type)
        )
    issues.extend(
        check_project_structure(
            project,
            segment_rows,
            temporary_directory=temporary_directory,
        )
    )

    counts = {"error": 0, "warn": 0, "info": 0, "total": len(issues)}
    for issue in issues:
        counts[issue.severity] += 1

    return QAReport(
        project_id=project.id or 0,
        generated_at=utc_now(),
        issues=issues,
        counts=counts,
    )


def run_project_qa(
    session: Session,
    project_id: int,
    *,
    temporary_directory: Path | None = None,
) -> QAReport:
    project = session.get(Project, project_id)
    if project is None:
        raise LookupError(f"project {project_id} not found")

    segments = list(
        session.exec(
            select(Segment)
            .where(Segment.project_id == project_id)
            .order_by(Segment.ord, Segment.id)
        ).all()
    )
    glossary = list(
        session.exec(
            select(GlossaryTerm)
            .where(GlossaryTerm.project_id == project_id)
            .order_by(GlossaryTerm.id)
        ).all()
    )
    return build_qa_report(
        project,
        segments,
        glossary,
        temporary_directory=temporary_directory,
    )


def qa_report_as_dict(report: QAReport) -> dict[str, Any]:
    """Compatibility helper for CLI callers."""

    return report.model_dump(mode="json")
