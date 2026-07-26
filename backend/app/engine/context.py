from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from ..providers.litellm_provider import estimate_tokens


def _get(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def glossary_matches(text: str, glossary: Iterable[Any]) -> list[Any]:
    matches: list[Any] = []
    for term in glossary:
        if not bool(_get(term, "enabled", True)):
            continue
        source = str(_get(term, "source_term", ""))
        if not source:
            continue
        case_sensitive = bool(_get(term, "case_sensitive", False))
        haystack = text if case_sensitive else text.casefold()
        needle = source if case_sensitive else source.casefold()
        if needle in haystack:
            matches.append(term)
    return matches


def _glossary_section(terms: Sequence[Any]) -> str:
    if not terms:
        return ""
    lines = ["【术语表（必须遵守）】"]
    for term in terms:
        source = _get(term, "source_term", "")
        target = _get(term, "target_term", "")
        note = _get(term, "note")
        line = f"- {source} → {target}"
        if note:
            line += f"（{note}）"
        lines.append(line)
    return "\n".join(lines)


def _history_section(previous: Sequence[Any]) -> str:
    lines: list[str] = []
    usable = [
        item
        for item in previous
        if _get(item, "target_text") and _get(item, "status") in {"done", "reviewed"}
    ]
    if not usable:
        return ""
    lines.append("【前文对照】")
    for item in usable:
        lines.append(f"原文：{_get(item, 'source_text', '')}")
        lines.append(f"译文：{_get(item, 'target_text', '')}")
    return "\n".join(lines)


def build_context(
    current_text: str,
    *,
    previous: Sequence[Any] = (),
    glossary: Sequence[Any] = (),
    chapter_summary: str | None = None,
    max_tokens: int = 1200,
    model: str | None = None,
) -> str:
    """Build a bounded glossary + history + chapter-summary context window."""

    sections: list[str] = []
    matched_terms = glossary_matches(current_text, glossary)
    glossary_text = _glossary_section(matched_terms)
    if glossary_text:
        sections.append(glossary_text)
    if chapter_summary:
        sections.append(f"【本章摘要】\n{chapter_summary.strip()}")

    # Newest previous segment is most useful. Add candidates one at a time and
    # retain chronological display order.
    selected: list[Any] = []
    for candidate in reversed(previous):
        trial = [candidate, *selected]
        history = _history_section(trial)
        proposed = "\n\n".join([*sections, history] if history else sections)
        if estimate_tokens(proposed, model) > max_tokens:
            continue
        selected = trial
    history = _history_section(selected)
    if history:
        sections.append(history)

    context = "\n\n".join(sections)
    if estimate_tokens(context, model) <= max_tokens:
        return context

    # A long summary or glossary note can independently exceed the budget.
    # Character clipping is only a final guard and intentionally leaves a marker.
    marker = "\n[…上下文已截断…]"
    low, high = 0, len(context)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = context[:middle].rstrip() + marker
        if estimate_tokens(candidate, model) <= max_tokens:
            low = middle
        else:
            high = middle - 1
    return context[:low].rstrip() + marker


@dataclass(slots=True)
class ContextBuilder:
    max_tokens: int = 1200
    previous_count: int = 3

    def build(
        self,
        current: Any,
        all_segments: Sequence[Any],
        *,
        glossary: Sequence[Any] = (),
        chapter_summary: str | None = None,
        model: str | None = None,
    ) -> str:
        current_ord = int(_get(current, "ord", 0))
        eligible_previous = [
            segment
            for segment in all_segments
            if int(_get(segment, "ord", -1)) < current_ord
            and _get(segment, "status") in {"done", "reviewed"}
            and _get(segment, "target_text")
        ]
        previous = (
            eligible_previous[-self.previous_count :] if self.previous_count else []
        )
        return build_context(
            str(_get(current, "source_text", "")),
            previous=previous,
            glossary=glossary,
            chapter_summary=chapter_summary,
            max_tokens=self.max_tokens,
            model=model,
        )
