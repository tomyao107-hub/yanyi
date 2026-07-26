from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(slots=True)
class Block:
    """A translatable document block and the information needed to write it back."""

    kind: str
    text: str | None
    struct_path: dict[str, Any]
    translatable: bool = True


@dataclass(slots=True)
class Chapter:
    ord: int
    title: str | None
    href: str | None
    blocks: list[Block] = field(default_factory=list)


@dataclass(slots=True)
class DocModel:
    source_type: str
    chapters: list[Chapter] = field(default_factory=list)
    source_path: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class DocumentParser(Protocol):
    def parse(self, path: str | Path) -> DocModel: ...


def ensure_readable_file(path: str | Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Document does not exist or is not a file: {resolved}")
    return resolved
