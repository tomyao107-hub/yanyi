from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session, select

from .models import Chapter as DBChapter
from .models import Segment as DBSegment
from .models import utc_now
from .parsers.base import DocModel

_SPACE_RE = re.compile(r"\s+")
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?。！？；;])(?:[ \t]+|(?=\n))|\n{2,}")
_MARKDOWN_ATOMIC_RE = re.compile(
    r"`+.*?`+"
    r"|!\[[^\]\n]*\]\([^\)\n]*\)"
    r"|(?<=\]\()[^\)\n]+(?=\))"
    r"|<(?:https?://|mailto:)[^>\n]+>"
    r"|https?://[^\s<>\]\)]+"
    r"|\{\{[^{}\n]+\}\}|\{[A-Za-z_][^{}\n]*\}",
    re.IGNORECASE | re.DOTALL,
)
_INTERNAL_MARKER_RE = re.compile(r"⟦[A-Z]+\d{4}⟧")
_QUOTE_TRANSLATION = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u00a0": " ",
    }
)


def normalize(text: str) -> str:
    """Normalize only for hashing; stored source text remains untouched."""

    normalized = unicodedata.normalize("NFC", text)
    normalized = normalized.translate(_QUOTE_TRANSLATION)
    return _SPACE_RE.sub(" ", normalized).strip()


normalize_text = normalize


def source_hash(text: str) -> str:
    return hashlib.sha1(normalize(text).encode("utf-8")).hexdigest()


def _path_hash(struct_path: dict[str, Any]) -> str:
    canonical = json.dumps(
        struct_path,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:8]


def _stable_struct_path(struct_path: dict[str, Any]) -> dict[str, Any]:
    """Strip parser snapshots that are useful to writers but not structural IDs."""

    source_type = struct_path.get("source_type")
    if source_type == "md":
        return {
            "source_type": "md",
            "token_index": struct_path.get("token_index"),
        }
    if source_type == "epub":
        return {
            "source_type": "epub",
            "file": struct_path.get("file"),
            "xpath": struct_path.get("xpath"),
        }
    volatile = {
        "raw_block",
        "inline_source",
        "inline_offset",
        "block_text",
        "char_start",
        "char_end",
        "line_start",
        "line_end",
        "part_index",
        "part_count",
    }
    return {key: value for key, value in struct_path.items() if key not in volatile}


def make_stable_key(chapter_ord: int, block_index: int, struct_path: dict[str, Any]) -> str:
    return f"{chapter_ord:04d}:{block_index:05d}:{_path_hash(_stable_struct_path(struct_path))}"


def split_long_text(text: str, max_chars: int = 1500) -> list[str]:
    """Soft-split long blocks while preserving every input character exactly."""

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if len(text) <= max_chars:
        return [text]

    candidates = [match.end() for match in _SENTENCE_BOUNDARY_RE.finditer(text)]
    parts: list[str] = []
    cursor = 0
    while cursor < len(text):
        hard_end = min(cursor + max_chars, len(text))
        if hard_end == len(text):
            parts.append(text[cursor:])
            break
        boundaries = [point for point in candidates if cursor < point <= hard_end]
        end = boundaries[-1] if boundaries else hard_end
        for marker_match in _INTERNAL_MARKER_RE.finditer(text):
            if marker_match.start() < end < marker_match.end():
                end = (
                    marker_match.start()
                    if marker_match.start() > cursor
                    else marker_match.end()
                )
                break
        # Avoid cutting directly between a UTF-16 surrogate pair (relevant when
        # text originated from a narrow Python build or malformed fixture).
        if (
            end < len(text)
            and end - 1 > cursor
            and 0xD800 <= ord(text[end - 1]) <= 0xDBFF
            and 0xDC00 <= ord(text[end]) <= 0xDFFF
        ):
            end -= 1
        parts.append(text[cursor:end])
        cursor = end
    return parts


def split_markdown_text(text: str, max_chars: int = 1500) -> list[str]:
    """Split Markdown without cutting through code, URLs, images or placeholders."""

    if len(text) <= max_chars:
        return [text]
    atomic_ranges = [match.span() for match in _MARKDOWN_ATOMIC_RE.finditer(text)]
    parts: list[str] = []
    cursor = 0
    candidates = [match.end() for match in _SENTENCE_BOUNDARY_RE.finditer(text)]
    while cursor < len(text):
        hard_end = min(cursor + max_chars, len(text))
        if hard_end == len(text):
            parts.append(text[cursor:])
            break
        boundaries = [point for point in candidates if cursor < point <= hard_end]
        end = boundaries[-1] if boundaries else hard_end
        for atomic_start, atomic_end in atomic_ranges:
            if atomic_start < end < atomic_end:
                end = atomic_start if atomic_start > cursor else atomic_end
                break
        if end <= cursor:
            end = hard_end
        parts.append(text[cursor:end])
        cursor = end
    return parts


@dataclass(slots=True)
class SegmentDraft:
    ord: int
    chapter_ord: int
    block_index: int
    stable_key: str
    struct_path: dict[str, Any]
    source_text: str
    src_hash: str
    status: str = "pending"
    target_text: str | None = None
    error_msg: str | None = None
    token_in: int | None = None
    token_out: int | None = None
    provider: str | None = None


def create_segments(doc: DocModel, max_chars: int = 1500) -> list[SegmentDraft]:
    segments: list[SegmentDraft] = []
    ordinal = 0
    for chapter in doc.chapters:
        for block_index, block in enumerate(chapter.blocks):
            if not block.translatable or block.text is None or not block.text.strip():
                continue
            base_key = make_stable_key(chapter.ord, block_index, block.struct_path)
            parts = (
                split_markdown_text(block.text, max_chars=max_chars)
                if block.struct_path.get("source_type") == "md"
                else split_long_text(block.text, max_chars=max_chars)
            )
            for part_index, part in enumerate(parts):
                path = dict(block.struct_path)
                path.update(
                    {
                        "chapter_ord": chapter.ord,
                        "block_index": block_index,
                        "block_kind": block.kind,
                        "part_index": part_index,
                        "part_count": len(parts),
                        "block_text": block.text,
                    }
                )
                stable_key = base_key if len(parts) == 1 else f"{base_key}#{part_index}"
                segments.append(
                    SegmentDraft(
                        ord=ordinal,
                        chapter_ord=chapter.ord,
                        block_index=block_index,
                        stable_key=stable_key,
                        struct_path=path,
                        source_text=part,
                        src_hash=source_hash(part),
                    )
                )
                ordinal += 1
    return segments


segment_document = create_segments
build_segments = create_segments


def join_segment_parts(parts: Sequence[str]) -> str:
    return "".join(parts)


def persist_document(
    session: Session,
    project_id: int,
    doc: DocModel,
    *,
    max_chars: int = 1500,
) -> list[DBSegment]:
    """Upsert chapters and the segment ledger without overwriting completed work."""

    chapters_by_ord = {
        chapter.ord: chapter
        for chapter in session.exec(
            select(DBChapter).where(DBChapter.project_id == project_id)
        ).all()
    }
    for parsed in doc.chapters:
        chapter = chapters_by_ord.get(parsed.ord)
        if chapter is None:
            chapter = DBChapter(
                project_id=project_id,
                ord=parsed.ord,
                title=parsed.title,
                href=parsed.href,
            )
            session.add(chapter)
            session.flush()
            chapters_by_ord[parsed.ord] = chapter
        else:
            chapter.title = parsed.title
            chapter.href = parsed.href
    session.flush()

    existing = {
        segment.stable_key: segment
        for segment in session.exec(
            select(DBSegment).where(DBSegment.project_id == project_id)
        ).all()
    }
    drafts = create_segments(doc, max_chars=max_chars)
    chapter_ord_by_id = {
        chapter.id: chapter.ord
        for chapter in chapters_by_ord.values()
        if chapter.id is not None
    }
    existing_signatures: defaultdict[int, list[tuple[str, str]]] = defaultdict(list)
    for segment in sorted(existing.values(), key=lambda item: (item.ord, item.id or 0)):
        chapter_ord = chapter_ord_by_id.get(segment.chapter_id)
        if chapter_ord is not None:
            existing_signatures[chapter_ord].append(
                (segment.stable_key, segment.src_hash)
            )
    draft_signatures: defaultdict[int, list[tuple[str, str]]] = defaultdict(list)
    for draft in drafts:
        draft_signatures[draft.chapter_ord].append((draft.stable_key, draft.src_hash))
    for chapter_ord in set(existing_signatures) | set(draft_signatures):
        if existing_signatures[chapter_ord] != draft_signatures[chapter_ord]:
            chapter = chapters_by_ord.get(chapter_ord)
            if chapter is not None:
                chapter.summary = None

    rows: list[DBSegment] = []
    current_keys = {draft.stable_key for draft in drafts}
    for draft in drafts:
        chapter_id = chapters_by_ord[draft.chapter_ord].id
        if chapter_id is None:
            raise RuntimeError("Chapter primary key was not generated")
        row = existing.get(draft.stable_key)
        if row is None:
            row = DBSegment(
                project_id=project_id,
                chapter_id=chapter_id,
                ord=draft.ord,
                stable_key=draft.stable_key,
                struct_path=draft.struct_path,
                source_text=draft.source_text,
                src_hash=draft.src_hash,
            )
            session.add(row)
        else:
            row.chapter_id = chapter_id
            row.ord = draft.ord
            row.struct_path = draft.struct_path
            if row.src_hash != draft.src_hash:
                row.source_text = draft.source_text
                row.src_hash = draft.src_hash
                row.target_text = None
                row.status = "pending"
                row.error_msg = None
                row.token_in = None
                row.token_out = None
                row.provider = None
                row.updated_at = utc_now()
        rows.append(row)
    for stable_key, stale in existing.items():
        if stable_key not in current_keys:
            session.delete(stale)
    session.flush()
    current_chapter_ords = {chapter.ord for chapter in doc.chapters}
    for chapter_ord, chapter in chapters_by_ord.items():
        if chapter_ord not in current_chapter_ords:
            session.delete(chapter)
    session.commit()
    for row in rows:
        session.refresh(row)
    return rows


create_segment_ledger = persist_document


def group_by_block(segments: Iterable[Any]) -> list[list[Any]]:
    groups: dict[tuple[Any, ...], list[Any]] = {}
    order: list[tuple[Any, ...]] = []
    for segment in segments:
        path = segment.struct_path
        if isinstance(path, str):
            path = json.loads(path)
        key = (
            path.get("source_type"),
            path.get("file"),
            path.get("xpath"),
            path.get("token_index"),
            path.get("chapter_ord"),
            path.get("block_index"),
        )
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(segment)
    def part_index(item: Any) -> int:
        path = item.struct_path
        if isinstance(path, str):
            path = json.loads(path)
        return int(path.get("part_index", 0))

    return [sorted(groups[key], key=part_index) for key in order]
