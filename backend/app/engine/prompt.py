from __future__ import annotations

import re

LANGUAGE_NAMES = {
    "en": "英文",
    "zh": "中文",
    "zh-cn": "简体中文",
    "zh-tw": "繁体中文",
    "ja": "日文",
    "ko": "韩文",
    "fr": "法文",
    "de": "德文",
    "es": "西班牙文",
}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code.lower(), code)


def build_system_prompt(source_lang: str = "en", target_lang: str = "zh-CN") -> str:
    source = language_name(source_lang)
    target = language_name(target_lang)
    return (
        f"你是一名专业的书籍译者。请把{source}原文翻译成{target}。"
        "译文必须忠实、准确、自然、流畅，并保持原作语气、叙事视角和段落功能。"
        "严格遵守上下文中的术语表，同一人名、地名和概念前后一致。"
        "完整保留原文中的 Markdown 标记、HTML/XML 标记、URL、代码、占位符"
        "（例如 {name}、%s、{{value}}）和形如 ⟦...⟧ 的保护标记。"
        "只输出当前待译原文的译文，不要解释、不要复述上下文、不要添加标题或引号。"
    )


def build_user_prompt(text: str, context: str = "") -> str:
    context_section = context.strip() or "（无额外上下文）"
    return (
        "【上下文，仅供理解，不要翻译或复述】\n"
        f"{context_section}\n\n"
        "【待译原文】\n"
        f"{text}"
    )


DEFAULT_SYSTEM_PROMPT = build_system_prompt()

# Placeholders an admin may use in a stored template. Rendering substitutes only
# these names, so a stray brace in prose can never raise or leak other state.
TEMPLATE_PLACEHOLDERS: tuple[str, ...] = (
    "source_lang",
    "target_lang",
    "source_lang_code",
    "target_lang_code",
)
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def render_system_prompt(
    template: str | None,
    *,
    source_lang: str = "en",
    target_lang: str = "zh-CN",
) -> str:
    """Render a stored template, falling back to the built-in default prompt.

    Unknown placeholders are left verbatim rather than raising, so a template
    saved with a typo still produces a usable prompt instead of failing a run.
    """

    if template is None or not template.strip():
        return build_system_prompt(source_lang, target_lang)
    values = {
        "source_lang": language_name(source_lang),
        "target_lang": language_name(target_lang),
        "source_lang_code": source_lang,
        "target_lang_code": target_lang,
    }
    return _PLACEHOLDER_RE.sub(
        lambda match: values.get(match.group(1), match.group(0)),
        template,
    ).strip()


BUILTIN_PROMPT_TEMPLATES: tuple[dict[str, str], ...] = (
    {
        "name": "文学翻译",
        "description": "适用于小说、散文与传记，优先保持语气与叙事节奏。",
        "system_prompt": (
            "你是一名专业的书籍译者。请把{source_lang}原文翻译成{target_lang}。"
            "译文必须忠实、准确、自然、流畅，并保持原作语气、叙事视角和段落功能。"
            "严格遵守上下文中的术语表，同一人名、地名和概念前后一致。"
            "完整保留原文中的 Markdown 标记、HTML/XML 标记、URL、代码、占位符"
            "（例如 {name}、%s）和形如 ⟦...⟧ 的保护标记。"
            "只输出当前待译原文的译文，不要解释、不要复述上下文、不要添加标题或引号。"
        ),
    },
    {
        "name": "技术文档",
        "description": "适用于手册与 API 文档，术语从严，代码与命令原样保留。",
        "system_prompt": (
            "你是一名技术文档译者。请把{source_lang}原文翻译成{target_lang}。"
            "用词准确、简洁、统一，遵循中文技术写作习惯，不使用夸张表达。"
            "严格遵守上下文中的术语表；公认的英文术语、API 名称、命令、参数、"
            "文件路径和代码一律保留原文，不要翻译。"
            "完整保留 Markdown 标记、HTML/XML 标记、URL、占位符和形如 ⟦...⟧ 的保护标记。"
            "只输出译文，不要解释或添加说明。"
        ),
    },
    {
        "name": "严格直译",
        "description": "逐句贴近原文结构，适合需要对照校核的场合。",
        "system_prompt": (
            "你是一名严谨的译者。请把{source_lang}原文逐句翻译成{target_lang}。"
            "尽可能贴近原文的句式结构与信息顺序，不合并、不拆分、不省略、不添加内容。"
            "严格遵守上下文中的术语表。"
            "完整保留 Markdown 标记、HTML/XML 标记、URL、代码、占位符和形如 ⟦...⟧ 的保护标记。"
            "只输出译文，不要解释。"
        ),
    },
)
