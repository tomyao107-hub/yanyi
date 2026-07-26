from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token

from .base import Block, Chapter, DocModel, ensure_readable_file

_HEADING_TYPES = {f"h{level}" for level in range(1, 7)}
_SKIPPED_BLOCK_TYPES = {"fence", "code_block", "html_block"}


def _markdown() -> MarkdownIt:
    # "commonmark" deliberately keeps parsing deterministic. Optional GFM-like
    # tables are enabled when supported by the installed markdown-it-py version.
    parser = MarkdownIt("commonmark", {"html": True})
    try:
        parser.enable("table")
    except ValueError:
        pass
    return parser


def _line_offsets(source: str) -> list[int]:
    offsets = [0]
    for match in re.finditer(r"\n", source):
        offsets.append(match.end())
    offsets.append(len(source))
    return offsets


def _table_cell_spans(raw_row: str) -> list[tuple[int, int]]:
    """Return trimmed cell-content spans without splitting escaped pipes."""

    row_end = len(raw_row.rstrip("\r\n"))
    pipe_positions: list[int] = []
    backslashes = 0
    for index, character in enumerate(raw_row[:row_end]):
        if character == "|":
            if backslashes % 2 == 0:
                pipe_positions.append(index)
            backslashes = 0
        elif character == "\\":
            backslashes += 1
        else:
            backslashes = 0
    boundaries = [0, *(position + 1 for position in pipe_positions)]
    ends = [*pipe_positions, row_end]
    spans = list(zip(boundaries, ends, strict=True))
    if pipe_positions and not raw_row[: pipe_positions[0]].strip(" \t"):
        spans = spans[1:]
    if pipe_positions and not raw_row[pipe_positions[-1] + 1 : row_end].strip(" \t"):
        spans = spans[:-1]

    trimmed: list[tuple[int, int]] = []
    for start, end in spans:
        while start < end and raw_row[start] in " \t":
            start += 1
        while end > start and raw_row[end - 1] in " \t":
            end -= 1
        trimmed.append((start, end))
    return trimmed


def _block_kind(tokens: list[Token], index: int) -> str:
    parent = tokens[index - 1].type if index else ""
    if parent == "heading_open":
        return "heading"
    if parent == "paragraph_open":
        # Inspect active containers to distinguish paragraphs, quotes and lists.
        active: list[str] = []
        for previous in tokens[:index]:
            if previous.nesting == 1:
                active.append(previous.type)
            elif previous.nesting == -1:
                opener = previous.type.removesuffix("_close") + "_open"
                for cursor in range(len(active) - 1, -1, -1):
                    if active[cursor] == opener:
                        active.pop(cursor)
                        break
        if "list_item_open" in active:
            return "list_item"
        if "blockquote_open" in active:
            return "blockquote"
        return "para"
    if parent in {"th_open", "td_open"}:
        return "table_cell"
    return "para"


def _chapter_title(token: Token) -> str:
    return token.content.strip() or "Untitled"


def _has_translatable_inline(token: Token) -> bool:
    children = token.children or []
    if not children:
        return bool(token.content.strip())
    return any(
        child.type in {"text", "softbreak", "hardbreak"}
        and bool(child.content.strip() or child.type.endswith("break"))
        for child in children
    )


class MarkdownParser:
    """Parse Markdown into blocks while retaining exact source line locations."""

    def parse(self, path: str | Path) -> DocModel:
        source_path = ensure_readable_file(path)
        source = source_path.read_text(encoding="utf-8-sig")
        parser = _markdown()
        tokens = parser.parse(source)
        offsets = _line_offsets(source)

        chapters: list[Chapter] = []
        preamble = Chapter(ord=0, title=None, href=None)
        current = preamble
        token_to_block: dict[int, tuple[int, int]] = {}
        range_search_cursor: dict[tuple[int, int], int] = {}
        table_cell_cursor: dict[tuple[int, int], int] = {}

        for index, token in enumerate(tokens):
            if token.type in _SKIPPED_BLOCK_TYPES:
                line_map = token.map or [0, 0]
                raw = source[offsets[line_map[0]] : offsets[min(line_map[1], len(offsets) - 1)]]
                current.blocks.append(
                    Block(
                        kind="code" if token.type != "html_block" else "raw",
                        text=token.content or raw,
                        struct_path={
                            "source_type": "md",
                            "token_index": index,
                            "line_start": line_map[0],
                            "line_end": line_map[1],
                            "raw_block": raw,
                        },
                        translatable=False,
                    )
                )
                continue

            if token.type != "inline" or token.map is None:
                continue

            kind = _block_kind(tokens, index)
            is_top_heading = (
                kind == "heading"
                and index > 0
                and tokens[index - 1].tag in {"h1", "h2"}
            )
            if is_top_heading:
                if current.blocks or current.title is not None:
                    chapters.append(current)
                current = Chapter(
                    ord=len(chapters),
                    title=_chapter_title(token),
                    href=f"heading-{index}",
                )

            line_start, line_end = token.map
            start_offset = offsets[min(line_start, len(offsets) - 1)]
            end_offset = offsets[min(line_end, len(offsets) - 1)]
            opening = tokens[index - 1] if index else token
            block_map = opening.map or token.map
            block_start = offsets[min(block_map[0], len(offsets) - 1)]
            block_end = offsets[min(block_map[1], len(offsets) - 1)]
            raw = source[start_offset:end_offset]
            raw_block = source[block_start:block_end]
            range_key = (line_start, line_end)
            inline_raw_source = token.content
            table_cell_index: int | None = None
            if kind == "table_cell":
                table_cell_index = table_cell_cursor.get(range_key, 0)
                table_cell_cursor[range_key] = table_cell_index + 1
                spans = _table_cell_spans(raw)
                if table_cell_index < len(spans):
                    occurrence, raw_end = spans[table_cell_index]
                    inline_raw_source = raw[occurrence:raw_end]
                else:
                    occurrence = -1
            else:
                search_cursor = range_search_cursor.get(range_key, 0)
                occurrence = raw.find(token.content, search_cursor)
                if occurrence < 0:
                    occurrence = raw.find(token.content)
                if occurrence >= 0:
                    range_search_cursor[range_key] = occurrence + len(token.content)
            path_data: dict[str, Any] = {
                "source_type": "md",
                "token_index": index,
                "line_start": line_start,
                "line_end": line_end,
                "char_start": start_offset,
                "char_end": end_offset,
                "raw_block": raw_block,
                "inline_source": token.content,
                "inline_raw_source": inline_raw_source,
                "inline_offset": occurrence,
                "tag": opening.tag,
                "markup": opening.markup,
                "block_char_start": block_start,
                "block_char_end": block_end,
            }
            if table_cell_index is not None:
                path_data["table_cell_index"] = table_cell_index
            current.blocks.append(
                Block(
                    kind=kind,
                    text=token.content,
                    struct_path=path_data,
                    translatable=_has_translatable_inline(token),
                )
            )
            token_to_block[index] = (current.ord, len(current.blocks) - 1)

        if current.blocks or current.title is not None:
            chapters.append(current)
        if not chapters:
            chapters = [preamble]

        # Re-number after omitting an empty preamble.
        for ordinal, chapter in enumerate(chapters):
            chapter.ord = ordinal

        return DocModel(
            source_type="md",
            chapters=chapters,
            source_path=source_path,
            metadata={
                "source": source,
                "encoding": "utf-8",
                "token_count": len(tokens),
                "token_to_block": token_to_block,
            },
        )


def parse_markdown(path: str | Path) -> DocModel:
    return MarkdownParser().parse(path)
