from __future__ import annotations

import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from ebooklib import ITEM_DOCUMENT, epub
from lxml import etree

from .base import Block, Chapter, DocModel, ensure_readable_file

_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre"}
_CONTAINER_TAGS = {"li", "blockquote"}


def validate_epub_archive(
    path: str | Path,
    *,
    maximum_uncompressed_bytes: int = 1024 * 1024 * 1024,
    maximum_members: int = 20_000,
    maximum_compression_ratio: float = 200.0,
) -> None:
    """Reject malformed or implausibly expanded EPUB archives before parsing."""

    source = Path(path)
    try:
        with zipfile.ZipFile(source) as archive:
            members = [member for member in archive.infolist() if not member.is_dir()]
            if len(members) > maximum_members:
                raise ValueError(f"EPUB contains more than {maximum_members} files")
            expanded = 0
            for member in members:
                normalized = PurePosixPath(member.filename.replace("\\", "/"))
                if normalized.is_absolute() or ".." in normalized.parts:
                    raise ValueError("EPUB contains an unsafe archive path")
                expanded += member.file_size
                if expanded > maximum_uncompressed_bytes:
                    raise ValueError("EPUB expands beyond the configured safety limit")
                if member.file_size > 1024 * 1024:
                    ratio = member.file_size / max(member.compress_size, 1)
                    if ratio > maximum_compression_ratio:
                        raise ValueError("EPUB contains a suspiciously compressed file")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"invalid EPUB ZIP container: {exc}") from exc


def _local_name(element: etree._Element) -> str:
    return etree.QName(element).localname.lower()


def _visible_text(element: etree._Element) -> str:
    return "".join(element.itertext()).strip()


def _text_with_inline_code_markers(
    element: etree._Element,
) -> tuple[str, dict[str, str], list[dict[str, int]]]:
    protected: dict[str, str] = {}
    parts: list[str] = []
    slots: list[dict[str, int]] = []
    offset = 0

    def append_text(value: str, *, slot: bool) -> None:
        nonlocal offset
        start = offset
        parts.append(value)
        offset += len(value)
        if slot and value.strip():
            slots.append({"start": start, "end": offset})

    def walk(node: etree._Element) -> None:
        if node.text:
            append_text(node.text, slot=True)
        for child in node:
            if not isinstance(child.tag, str):
                if child.tail:
                    append_text(child.tail, slot=True)
                continue
            local = _local_name(child)
            if local in {"code", "pre"}:
                marker = f"⟦EPUBCODE{len(protected):04d}⟧"
                protected[marker] = "".join(child.itertext())
                append_text(marker, slot=False)
            elif local in {"script", "style"}:
                # These subtrees are not visible prose and must never be sent
                # to a translation provider. Their tail can still be visible.
                pass
            else:
                walk(child)
            if child.tail:
                append_text(child.tail, slot=True)

    walk(element)
    raw = "".join(parts)
    left_trim = len(raw) - len(raw.lstrip())
    text = raw.strip()
    adjusted_slots: list[dict[str, int]] = []
    for slot in slots:
        start = max(0, slot["start"] - left_trim)
        end = min(len(text), slot["end"] - left_trim)
        if end > start:
            adjusted_slots.append({"start": start, "end": end})
    return text, protected, adjusted_slots


def _kind_for(tag: str) -> str:
    if tag.startswith("h") and len(tag) == 2 and tag[1].isdigit():
        return "heading"
    if tag == "li":
        return "list_item"
    if tag == "blockquote":
        return "blockquote"
    if tag == "pre":
        return "code"
    return "para"


def _has_selected_ancestor(element: etree._Element) -> bool:
    parent = element.getparent()
    while parent is not None:
        tag = _local_name(parent)
        if tag in _CONTAINER_TAGS:
            return True
        parent = parent.getparent()
    return False


def _parse_xhtml(content: bytes) -> etree._Element:
    parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True)
    return etree.fromstring(content, parser=parser)


def _document_items(book: epub.EpubBook) -> Iterable[epub.EpubHtml]:
    seen: set[str] = set()
    for item_id, _linear in book.spine:
        item = book.get_item_with_id(item_id)
        if item is None or item.get_type() != ITEM_DOCUMENT:
            continue
        if isinstance(item, epub.EpubNav):
            continue
        name = item.get_name()
        if name in seen:
            continue
        seen.add(name)
        yield item


class EpubParser:
    """Parse spine XHTML documents without modifying the original EPUB archive."""

    def parse(self, path: str | Path) -> DocModel:
        source_path = ensure_readable_file(path)
        validate_epub_archive(source_path)
        book = epub.read_epub(str(source_path), options={"ignore_ncx": False})
        chapters: list[Chapter] = []

        for item in _document_items(book):
            content = bytes(item.get_content())
            try:
                root = _parse_xhtml(content)
            except etree.XMLSyntaxError:
                continue
            tree = root.getroottree()
            blocks: list[Block] = []
            title: str | None = None

            for element in root.iter():
                if not isinstance(element.tag, str):
                    continue
                tag = _local_name(element)
                if tag not in _BLOCK_TAGS or _has_selected_ancestor(element):
                    continue
                if tag == "pre":
                    text = _visible_text(element)
                    inline_code: dict[str, str] = {}
                    inline_slots: list[dict[str, int]] = []
                else:
                    text, inline_code, inline_slots = _text_with_inline_code_markers(
                        element
                    )
                xpath = tree.getpath(element)
                translatable = tag != "pre" and bool(inline_slots)
                kind = _kind_for(tag)
                if title is None and kind == "heading" and text:
                    title = text
                blocks.append(
                    Block(
                        kind=kind,
                        text=text,
                        struct_path={
                            "source_type": "epub",
                            "file": item.get_name(),
                            "item_id": item.get_id(),
                            "xpath": xpath,
                            "tag": tag,
                            "inline_code": inline_code,
                            "inline_slots": inline_slots,
                        },
                        translatable=translatable,
                    )
                )

            if blocks:
                chapters.append(
                    Chapter(
                        ord=len(chapters),
                        title=title,
                        href=item.get_name(),
                        blocks=blocks,
                    )
                )

        return DocModel(
            source_type="epub",
            chapters=chapters,
            source_path=source_path,
            metadata={
                "title": book.get_metadata("DC", "title"),
                "language": book.get_metadata("DC", "language"),
            },
        )


def parse_epub(path: str | Path) -> DocModel:
    return EpubParser().parse(path)
