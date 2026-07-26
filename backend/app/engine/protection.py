from __future__ import annotations

import re
from dataclasses import dataclass

_CODE_RE = re.compile(r"(`+)(?:(?!\1).)*?\1", re.DOTALL)
_IMAGE_RE = re.compile(r"!\[[^\]\n]*\]\([^\)\n]*\)")
_HTML_RE = re.compile(r"</?[A-Za-z][^>\n]*>|<!--.*?-->", re.DOTALL)
_AUTOLINK_RE = re.compile(r"<(?:https?://|mailto:)[^>\n]+>", re.IGNORECASE)
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^\)\n]+)\)")
_REFERENCE_LINK_RE = re.compile(r"\[([^\]\n]+)\](\[[^\]\n]+\])")
_BARE_URL_RE = re.compile(r"https?://[^\s<>\]\)]+", re.IGNORECASE)
_EMPHASIS_RE = re.compile(r"(?<!\\)(?:\*\*|__|~~|(?<!\*)\*(?!\*)|(?<!_)_(?!_))")
_PLACEHOLDER_RE = re.compile(
    r"\{\{[^{}\n]+\}\}|\{[A-Za-z_][^{}\n]*\}|\$\{[^{}\n]+\}|%\([^)]+\)[a-zA-Z]|%[a-zA-Z]"
)
_INTERNAL_MARKER_RE = re.compile(r"⟦(?:EPUBCODE\d{4}|ES\d{4}[BE])⟧")
_EPUB_SLOT_MARKER_RE = re.compile(r"⟦ES\d{4}[BE]⟧")


class ProtectedContentError(ValueError):
    pass


@dataclass(slots=True)
class ProtectedText:
    text: str
    replacements: dict[str, str]

    def restore(self, translated: str) -> str:
        restored = translated
        for marker, original in self.replacements.items():
            count = restored.count(marker)
            if count != 1:
                raise ProtectedContentError(
                    f"Translation changed protected marker {marker!r} (found {count}, expected 1)"
                )
            restored = restored.replace(marker, original)
        return restored


def protect_markdown(text: str) -> ProtectedText:
    """Mask non-translatable inline Markdown payloads before the LLM call."""

    replacements: dict[str, str] = {}
    protected = text
    marker_index = 0

    def marker(original: str) -> str:
        nonlocal marker_index
        token = f"⟦MDP{marker_index:04d}⟧"
        while token in text or token in replacements:
            marker_index += 1
            token = f"⟦MDP{marker_index:04d}⟧"
        replacements[token] = original
        marker_index += 1
        return token

    def replace_whole(match: re.Match[str]) -> str:
        return marker(match.group(0))

    # Order matters: whole constructs are hidden before destinations/bare URLs.
    for pattern in (_CODE_RE, _IMAGE_RE, _AUTOLINK_RE, _HTML_RE):
        protected = pattern.sub(replace_whole, protected)

    def replace_link(match: re.Match[str]) -> str:
        return f"{marker('[')}{match.group(1)}{marker(f']({match.group(2)})')}"

    def replace_reference_link(match: re.Match[str]) -> str:
        return f"{marker('[')}{match.group(1)}{marker(f']{match.group(2)}')}"

    protected = _LINK_RE.sub(replace_link, protected)
    protected = _REFERENCE_LINK_RE.sub(replace_reference_link, protected)
    protected = _BARE_URL_RE.sub(replace_whole, protected)
    protected = _PLACEHOLDER_RE.sub(replace_whole, protected)
    protected = _EMPHASIS_RE.sub(replace_whole, protected)
    return ProtectedText(protected, replacements)


def protect_plain_text(text: str) -> ProtectedText:
    replacements: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        token = f"⟦VAR{len(replacements):04d}⟧"
        replacements[token] = match.group(0)
        return token

    protected = _INTERNAL_MARKER_RE.sub(replace, text)
    protected = _PLACEHOLDER_RE.sub(replace, protected)
    return ProtectedText(protected, replacements)


def protect_for_segment(text: str, struct_path: object) -> ProtectedText:
    path = struct_path if isinstance(struct_path, dict) else {}
    if path.get("source_type") == "md":
        return protect_markdown(text)
    if (
        path.get("source_type") == "epub"
        and int(path.get("part_count", 1)) == 1
        and path.get("inline_slots")
    ):
        marked = text
        slots = list(path["inline_slots"])
        for index in range(len(slots) - 1, -1, -1):
            start = int(slots[index]["start"])
            end = int(slots[index]["end"])
            if not (0 <= start <= end <= len(marked)):
                continue
            marked = (
                marked[:start]
                + f"⟦ES{index:04d}B⟧"
                + marked[start:end]
                + f"⟦ES{index:04d}E⟧"
                + marked[end:]
            )
        return protect_plain_text(marked)
    return protect_plain_text(text)


def restore_for_segment(
    protected: ProtectedText,
    translated: str,
    struct_path: object,
) -> str:
    restored = protected.restore(translated)
    path = struct_path if isinstance(struct_path, dict) else {}
    if path.get("source_type") == "epub":
        target_slots: list[str] = []
        for index, _slot in enumerate(path.get("inline_slots", ())):
            begin = f"⟦ES{index:04d}B⟧"
            end = f"⟦ES{index:04d}E⟧"
            pattern = re.compile(re.escape(begin) + r"(.*?)" + re.escape(end), re.DOTALL)
            match = pattern.search(restored)
            if match is None:
                target_slots = []
                break
            target_slots.append(match.group(1))
        restored = _EPUB_SLOT_MARKER_RE.sub("", restored)
        restored = _INTERNAL_MARKER_RE.sub("", restored)
        if target_slots and "".join(target_slots) == restored:
            path["target_slots"] = target_slots
            path["target_slots_text"] = restored
    return restored
