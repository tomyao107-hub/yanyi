from __future__ import annotations

from pathlib import Path

from .base import Block, Chapter, DocModel, DocumentParser
from .epub_parser import EpubParser, parse_epub
from .md_parser import MarkdownParser, parse_markdown


def parse_document(path: str | Path, source_type: str | None = None) -> DocModel:
    source_path = Path(path)
    detected = (source_type or source_path.suffix.lstrip(".")).lower()
    if detected in {"md", "markdown", "mdown"}:
        return parse_markdown(source_path)
    if detected == "epub":
        return parse_epub(source_path)
    raise ValueError(f"Unsupported document type: {detected or '<missing>'}")


__all__ = [
    "Block",
    "Chapter",
    "DocModel",
    "DocumentParser",
    "EpubParser",
    "MarkdownParser",
    "parse_document",
    "parse_epub",
    "parse_markdown",
]
