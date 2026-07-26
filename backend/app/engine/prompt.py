from __future__ import annotations

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
