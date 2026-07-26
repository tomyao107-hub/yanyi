from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ebooklib import ITEM_DOCUMENT, epub
from lxml import etree
from sqlmodel import Session, select

from ..models import Project, Segment

_STYLE_TEXT = """
.translation {
  color: #555;
  margin-top: 0.35em;
  margin-bottom: 0.8em;
}
""".strip()
_INLINE_CODE_MARKER_RE = re.compile(r"⟦EPUBCODE\d{4}⟧")


def _path(segment: Any) -> dict[str, Any]:
    value = getattr(segment, "struct_path", {})
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


def _groups(segments: Iterable[Any]) -> dict[tuple[str, str], list[Any]]:
    grouped: dict[tuple[str, str], list[Any]] = {}
    for segment in segments:
        path = _path(segment)
        if path.get("source_type") != "epub":
            continue
        key = (str(path.get("file", "")), str(path.get("xpath", "")))
        grouped.setdefault(key, []).append(segment)
    for group in grouped.values():
        group.sort(key=lambda item: _path(item).get("part_index", 0))
    return grouped


def _translation(group: list[Any], include_untranslated: bool) -> tuple[str | None, bool]:
    pieces: list[str] = []
    translated = False
    for segment in group:
        status = getattr(segment, "status", None)
        target = getattr(segment, "target_text", None)
        if status in {"done", "reviewed"} and target is not None:
            pieces.append(str(target))
            translated = True
        elif include_untranslated:
            pieces.append(str(segment.source_text))
        else:
            pieces.append("")
    if not translated and not include_untranslated:
        return None, False
    return "".join(pieces), translated


def _parse(content: bytes) -> etree._Element:
    return etree.fromstring(
        content,
        parser=etree.XMLParser(recover=True, resolve_entities=False, no_network=True),
    )


def _text_slots(element: etree._Element) -> list[tuple[etree._Element, str, str]]:
    slots: list[tuple[etree._Element, str, str]] = []
    ignored_tags = {"script", "style", "code", "pre"}
    for node in element.iter():
        if not isinstance(node.tag, str):
            continue
        ancestors = list(node.iterancestors())
        inside_ignored = any(
            isinstance(ancestor.tag, str)
            and etree.QName(ancestor).localname.lower()
            in ignored_tags
            for ancestor in ancestors
        )
        node_ignored = etree.QName(node).localname.lower() in ignored_tags
        if not inside_ignored and not node_ignored and node.text and node.text.strip():
            slots.append((node, "text", node.text))
        if (
            node is not element
            and not inside_ignored
            and node.tail
            and node.tail.strip()
        ):
            slots.append((node, "tail", node.tail))
    return slots


def _replace_visible_text(element: etree._Element, target: str) -> None:
    target = _INLINE_CODE_MARKER_RE.sub("", target)
    slots = _text_slots(element)
    if not slots:
        element.text = target
        return
    weights = [max(1, len(original.strip())) for _, _, original in slots]
    total_weight = sum(weights)
    boundaries = [0]
    cumulative = 0
    for index, weight in enumerate(weights[:-1], start=1):
        cumulative += weight
        boundary = round(len(target) * cumulative / total_weight)
        if len(target) >= len(slots):
            boundary = max(index, min(boundary, len(target) - (len(slots) - index)))
        boundaries.append(max(boundaries[-1], boundary))
    boundaries.append(len(target))

    for index, (node, attribute, original) in enumerate(slots):
        leading = (
            original[: len(original) - len(original.lstrip())] if index == 0 else ""
        )
        trailing = (
            original[len(original.rstrip()) :] if index == len(slots) - 1 else ""
        )
        translated_piece = target[boundaries[index] : boundaries[index + 1]]
        value = f"{leading}{translated_piece}{trailing}"
        setattr(node, attribute, value)


def _apply_target_slots(
    element: etree._Element,
    target: str,
    path: dict[str, Any],
) -> None:
    stored_text = path.get("target_slots_text")
    translated_slots = path.get("target_slots")
    slots = _text_slots(element)
    if (
        stored_text == target
        and isinstance(translated_slots, list)
        and len(translated_slots) == len(slots)
    ):
        for (node, attribute, _original), translated in zip(
            slots,
            translated_slots,
            strict=True,
        ):
            setattr(node, attribute, str(translated))
        return
    _replace_visible_text(element, target)


def _remove_preserving_tail(element: etree._Element) -> None:
    parent = element.getparent()
    if parent is None:
        return
    tail = element.tail or ""
    previous = element.getprevious()
    if previous is not None:
        previous.tail = (previous.tail or "") + tail
    else:
        parent.text = (parent.text or "") + tail
    parent.remove(element)


def _sanitize_translation_clone(element: etree._Element) -> None:
    media_tags = {
        "audio",
        "embed",
        "iframe",
        "img",
        "object",
        "picture",
        "source",
        "svg",
        "video",
    }
    for node in list(element.iter()):
        if not isinstance(node.tag, str):
            continue
        node.attrib.pop("id", None)
        node.attrib.pop("{http://www.w3.org/XML/1998/namespace}id", None)
        if node is not element and etree.QName(node).localname.lower() in media_tags:
            _remove_preserving_tail(node)


def _add_translation_class(element: etree._Element, target_lang: str) -> None:
    classes = element.get("class", "").split()
    if "translation" not in classes:
        classes.append("translation")
    element.set("class", " ".join(filter(None, classes)))
    element.set("lang", target_lang)


def _inject_style(root: etree._Element) -> None:
    if root.xpath(
        "//*[local-name()='style' and @data-translation-workbench='true']"
    ):
        return
    heads = root.xpath("//*[local-name()='head']")
    if not heads:
        return
    namespace = etree.QName(heads[0]).namespace
    tag = f"{{{namespace}}}style" if namespace else "style"
    style = etree.Element(tag)
    style.set("type", "text/css")
    style.set("data-translation-workbench", "true")
    style.text = _STYLE_TEXT
    heads[0].append(style)


def write_epub(
    source_path: str | Path,
    segments: Iterable[Any] | None = None,
    output_path: str | Path | None = None,
    *,
    mode: str = "bilingual",
    include_untranslated: bool = True,
    target_lang: str | None = None,
    session: Session | None = None,
    project: Project | None = None,
) -> Path:
    if mode not in {"bilingual", "target_only"}:
        raise ValueError("mode must be 'bilingual' or 'target_only'")
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
    output_file.parent.mkdir(parents=True, exist_ok=True)
    translation_lang = target_lang or (
        project.target_lang if project is not None else "zh-CN"
    )
    book = epub.read_epub(str(source_file), options={"ignore_ncx": False})
    grouped = _groups(segments)

    for item in book.get_items_of_type(ITEM_DOCUMENT):
        item_groups = {
            xpath: group
            for (file_name, xpath), group in grouped.items()
            if file_name == item.get_name()
        }
        if not item_groups:
            continue
        root = _parse(bytes(item.get_content()))
        changed = False
        # Reverse document order keeps sibling insertion independent of later paths.
        located: list[tuple[etree._Element, list[Any]]] = []
        for xpath, group in item_groups.items():
            matches = root.xpath(xpath)
            if matches and isinstance(matches[0], etree._Element):
                located.append((matches[0], group))
        for element, group in reversed(located):
            path = _path(group[0])
            target, has_translation = _translation(group, include_untranslated)
            if target is None:
                if mode == "target_only":
                    _replace_visible_text(element, "")
                    changed = True
                continue
            if mode == "target_only":
                _apply_target_slots(element, target, path)
                changed = True
            elif has_translation:
                parent = element.getparent()
                if parent is None:
                    continue
                translated_element = copy.deepcopy(element)
                _apply_target_slots(translated_element, target, path)
                _sanitize_translation_clone(translated_element)
                _add_translation_class(translated_element, translation_lang)
                parent.insert(parent.index(element) + 1, translated_element)
                changed = True
        if changed:
            _inject_style(root)
            item.set_content(
                etree.tostring(
                    root,
                    encoding="utf-8",
                    xml_declaration=True,
                    pretty_print=False,
                )
            )

    epub.write_epub(str(output_file), book, options={"raise_exceptions": True})
    return output_file


write_bilingual_epub = write_epub
