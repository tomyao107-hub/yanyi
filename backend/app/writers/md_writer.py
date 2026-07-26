from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from ..models import Project, Segment

_LINK_DESTINATION_RE = re.compile(r"(\[[^\]\n]+\]\()([^\)\n]+)(\))")


def _path(segment: Any) -> dict[str, Any]:
    value = getattr(segment, "struct_path", {})
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


def _completed(segment: Any) -> bool:
    return (
        getattr(segment, "status", None) in {"done", "reviewed"}
        and getattr(segment, "target_text", None) is not None
    )


def _groups(segments: Iterable[Any]) -> list[list[Any]]:
    grouped: dict[tuple[Any, ...], list[Any]] = {}
    for segment in segments:
        path = _path(segment)
        if path.get("source_type") != "md":
            continue
        key = (
            path.get("token_index"),
            path.get("char_start"),
            path.get("char_end"),
            path.get("block_index"),
        )
        grouped.setdefault(key, []).append(segment)
    groups = list(grouped.values())
    for group in groups:
        group.sort(key=lambda item: _path(item).get("part_index", 0))
    groups.sort(
        key=lambda group: int(_path(group[0]).get("char_start", 0))
        + max(0, int(_path(group[0]).get("inline_offset", 0))),
        reverse=True,
    )
    return groups


def _restore_link_destinations(source: str, target: str) -> str:
    source_destinations = [match.group(2) for match in _LINK_DESTINATION_RE.finditer(source)]
    target_matches = list(_LINK_DESTINATION_RE.finditer(target))
    if not source_destinations or len(source_destinations) != len(target_matches):
        return target
    result = target
    for match, destination in zip(
        reversed(target_matches),
        reversed(source_destinations),
        strict=True,
    ):
        result = result[: match.start(2)] + destination + result[match.end(2) :]
    return result


def _translation_for_group(group: list[Any], include_untranslated: bool) -> str | None:
    pieces: list[str] = []
    has_translation = False
    for segment in group:
        if _completed(segment):
            target = str(segment.target_text)
            target = _restore_link_destinations(str(segment.source_text), target)
            pieces.append(target)
            has_translation = True
        elif include_untranslated:
            pieces.append(str(segment.source_text))
        else:
            pieces.append("")
    if not has_translation and not include_untranslated:
        return None
    return "".join(pieces)


def _locate_inline(source: str, path: dict[str, Any]) -> tuple[int, int] | None:
    original = str(
        path.get(
            "inline_raw_source",
            path.get("inline_source", path.get("block_text", "")),
        )
    )
    char_start = int(path.get("char_start", 0))
    char_end = int(path.get("char_end", char_start))
    inline_offset = int(path.get("inline_offset", -1))
    if inline_offset >= 0:
        start = char_start + inline_offset
        end = start + len(original)
        if source[start:end] == original:
            return start, end
    within = source[char_start:char_end]
    relative = within.find(original)
    if relative >= 0:
        return char_start + relative, char_start + relative + len(original)
    return None


def _table_cell_spans(raw_row: str) -> list[tuple[int, int]]:
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


def _unescape_table_pipes(value: str) -> str:
    result: list[str] = []
    for index, character in enumerate(value):
        if character == "\\" and index + 1 < len(value) and value[index + 1] == "|":
            continue
        result.append(character)
    return "".join(result)


def _planned_table_locations(
    source: str,
    groups: list[list[Any]],
) -> dict[int, tuple[int, int]]:
    """Locate legacy table-cell paths without ever falling back to a whole row."""

    rows: dict[tuple[int, int], list[list[Any]]] = {}
    for group in groups:
        path = _path(group[0])
        if path.get("block_kind") != "table_cell":
            continue
        key = (
            int(path.get("char_start", 0)),
            int(path.get("char_end", path.get("char_start", 0))),
        )
        rows.setdefault(key, []).append(group)

    planned: dict[int, tuple[int, int]] = {}
    for (row_start, row_end), row_groups in rows.items():
        raw_row = source[row_start:row_end]
        spans = _table_cell_spans(raw_row)
        cursor = 0
        for group in sorted(
            row_groups,
            key=lambda item: int(_path(item[0]).get("token_index", 0)),
        ):
            path = _path(group[0])
            cell_index = path.get("table_cell_index")
            selected: int | None = None
            if isinstance(cell_index, int) and 0 <= cell_index < len(spans):
                selected = cell_index
            else:
                inline_source = str(path.get("inline_source", ""))
                for index in range(cursor, len(spans)):
                    start, end = spans[index]
                    candidate = _unescape_table_pipes(raw_row[start:end])
                    if candidate == inline_source:
                        selected = index
                        break
                if selected is None:
                    selected = next(
                        (
                            index
                            for index in range(cursor, len(spans))
                            if raw_row[spans[index][0] : spans[index][1]].strip()
                        ),
                        None,
                    )
            if selected is None:
                continue
            start, end = spans[selected]
            planned[id(group)] = (row_start + start, row_start + end)
            cursor = selected + 1
    return planned


_CONTAINER_PREFIX_RE = re.compile(
    r"^([ \t]*(?:>[ \t]*)*)(?:(?:[-+*]|\d+[.)])([ \t]+))?"
)


def _container_prefix(line: str) -> str:
    match = _CONTAINER_PREFIX_RE.match(line)
    return match.group(0) if match else ""


def _continuation_prefix(first_prefix: str) -> str:
    match = re.match(
        r"^([ \t]*(?:>[ \t]*)*)((?:[-+*]|\d+[.)])([ \t]+))$",
        first_prefix,
    )
    if match is None:
        return first_prefix
    return match.group(1) + (" " * len(match.group(2)))


def _format_container_target(
    raw: str,
    target: str,
    path: dict[str, Any],
) -> str:
    newline = "\r\n" if "\r\n" in raw else "\n"
    raw_lines = raw.splitlines()
    prefixes = [_container_prefix(line) for line in raw_lines]
    first_prefix = prefixes[0] if prefixes else ""
    if path.get("block_kind") not in {"blockquote", "list_item"} and not first_prefix:
        # Unknown source mapping: retain a block boundary instead of silently
        # flattening Markdown syntax.
        return target + (newline if raw.endswith(("\n", "\r")) else "")
    continuation = _continuation_prefix(first_prefix)
    target_lines = target.splitlines() or [""]
    rendered_lines: list[str] = []
    for index, line in enumerate(target_lines):
        if index < len(prefixes) and prefixes[index]:
            prefix = prefixes[index]
        else:
            prefix = first_prefix if path.get("block_kind") == "blockquote" else continuation
        rendered_lines.append(prefix + line)
    rendered = newline.join(rendered_lines)
    if raw.endswith(("\n", "\r")):
        rendered += newline
    return rendered


def _bilingual_suffix(target: str, path: dict[str, Any]) -> str:
    if path.get("tag") in {"td", "th"} or path.get("block_kind") == "table_cell":
        escaped = _format_table_cell_target(target)
        return f'<br /><span class="translation">{escaped}</span>'
    quoted = "\n".join(f"> {line}" if line else ">" for line in target.splitlines() or [""])
    return f"\n\n{quoted}"


def _format_table_cell_target(target: str) -> str:
    normalized = target.replace("\r\n", "\n").replace("\r", "\n")
    escaped: list[str] = []
    backslashes = 0
    for character in normalized:
        if character == "|":
            if backslashes % 2 == 0:
                escaped.append("\\")
            escaped.append(character)
            backslashes = 0
        else:
            escaped.append(character)
            backslashes = backslashes + 1 if character == "\\" else 0
    return "".join(escaped).replace("\n", "<br />")


def _is_setext_heading(path: dict[str, Any]) -> bool:
    if path.get("tag") not in {"h1", "h2"}:
        return False
    if path.get("markup") in {"=", "-"}:
        return True
    raw = str(path.get("raw_block", ""))
    return bool(re.search(r"(?m)^[ \t]*(?:=+|-+)[ \t]*\r?$", raw))


def render_markdown(
    source: str,
    segments: Iterable[Any],
    *,
    mode: str = "bilingual",
    include_untranslated: bool = True,
) -> str:
    if mode not in {"bilingual", "target_only"}:
        raise ValueError("mode must be 'bilingual' or 'target_only'")
    rendered = source
    groups = _groups(segments)
    table_locations = _planned_table_locations(source, groups)
    groups.sort(
        key=lambda group: table_locations.get(
            id(group),
            (
                int(_path(group[0]).get("char_start", 0))
                + max(0, int(_path(group[0]).get("inline_offset", 0))),
                0,
            ),
        )[0],
        reverse=True,
    )
    for group in groups:
        path = _path(group[0])
        has_translation = any(_completed(segment) for segment in group)
        if mode == "bilingual" and not has_translation:
            continue
        target = _translation_for_group(group, include_untranslated)
        if target is None:
            if mode == "target_only":
                location = table_locations.get(id(group)) or _locate_inline(
                    rendered,
                    path,
                )
                if location is not None:
                    start, end = location
                    rendered = rendered[:start] + rendered[end:]
                else:
                    start = int(path.get("char_start", 0))
                    end = int(path.get("char_end", start))
                    replacement = _format_container_target(
                        rendered[start:end],
                        "",
                        path,
                    )
                    rendered = rendered[:start] + replacement + rendered[end:]
            continue
        if mode == "bilingual" and _is_setext_heading(path):
            insertion = int(path.get("block_char_end", path.get("char_end", 0)))
            rendered = (
                rendered[:insertion]
                + _bilingual_suffix(target, path)
                + rendered[insertion:]
            )
            continue
        if mode == "target_only" and (
            path.get("tag") in {"td", "th"}
            or path.get("block_kind") == "table_cell"
        ):
            target = _format_table_cell_target(target)
        if (
            mode == "target_only"
            and path.get("block_kind") in {"blockquote", "list_item"}
            and ("\n" in target or "\r" in target)
        ):
            start = int(path.get("block_char_start", path.get("char_start", 0)))
            end = int(path.get("block_char_end", path.get("char_end", start)))
            replacement = _format_container_target(rendered[start:end], target, path)
            rendered = rendered[:start] + replacement + rendered[end:]
            continue
        location = table_locations.get(id(group)) or _locate_inline(rendered, path)
        if location is None:
            start = int(path.get("char_start", 0))
            end = int(path.get("char_end", start))
            if mode == "target_only":
                replacement = _format_container_target(
                    rendered[start:end],
                    target,
                    path,
                )
                rendered = rendered[:start] + replacement + rendered[end:]
            else:
                rendered = (
                    rendered[:end]
                    + _bilingual_suffix(target, path)
                    + rendered[end:]
                )
            continue
        start, end = location
        original = rendered[start:end]
        replacement = (
            target
            if mode == "target_only"
            else original + _bilingual_suffix(target, path)
        )
        rendered = rendered[:start] + replacement + rendered[end:]
    return rendered


def write_markdown(
    source_path: str | Path,
    segments: Iterable[Any] | None = None,
    output_path: str | Path | None = None,
    *,
    mode: str = "bilingual",
    include_untranslated: bool = True,
    session: Session | None = None,
    project: Project | None = None,
) -> Path:
    if output_path is None:
        raise ValueError("output_path is required")
    if segments is None:
        if session is None or project is None or project.id is None:
            raise ValueError("segments or both session and persisted project are required")
        segments = session.exec(
            select(Segment)
            .where(Segment.project_id == project.id)
            .order_by(Segment.ord)
        ).all()
    source_file = Path(source_path).expanduser().resolve()
    output_file = Path(output_path).expanduser().resolve()
    source = source_file.read_text(encoding="utf-8-sig")
    rendered = render_markdown(
        source,
        segments,
        mode=mode,
        include_untranslated=include_untranslated,
    )
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(rendered, encoding="utf-8", newline="")
    return output_file


write_md = write_markdown
