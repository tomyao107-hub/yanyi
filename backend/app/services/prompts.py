from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlmodel import Session, select

from ..engine.prompt import (
    BUILTIN_PROMPT_TEMPLATES,
    TEMPLATE_PLACEHOLDERS,
    render_system_prompt,
)
from ..models import Project, PromptTemplate, utc_now

MAX_SYSTEM_PROMPT_CHARS = 8000
MAX_USER_PREFIX_CHARS = 2000
MAX_DESCRIPTION_CHARS = 500


class PromptTemplateError(ValueError):
    pass


class PromptTemplateNotFoundError(LookupError):
    pass


def normalize_template_name(name: str) -> tuple[str, str]:
    display = " ".join(str(name).split())
    if not display or len(display) > 150:
        raise PromptTemplateError("Template name must be 1-150 characters")
    return display, display.casefold()


def _clean_body(value: Any, *, field: str, limit: int, required: bool) -> str | None:
    if value is None:
        if required:
            raise PromptTemplateError(f"{field} is required")
        return None
    # Normalize newlines but preserve the admin's intentional line structure.
    text = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        if required:
            raise PromptTemplateError(f"{field} cannot be blank")
        return None
    if len(text) > limit:
        raise PromptTemplateError(f"{field} must be at most {limit} characters")
    return text


def prompt_template_read_dto(template: PromptTemplate) -> dict[str, Any]:
    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "system_prompt": template.system_prompt,
        "user_prefix": template.user_prefix,
        "enabled": template.enabled,
        "is_default": template.is_default,
        "is_builtin": template.is_builtin,
        "created_at": template.created_at,
        "updated_at": template.updated_at,
    }


class PromptTemplateService:
    """CRUD for reusable translation prompts."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_templates(self) -> list[PromptTemplate]:
        return list(
            self.session.exec(
                select(PromptTemplate).order_by(
                    PromptTemplate.is_default.desc(),
                    PromptTemplate.name,
                    PromptTemplate.id,
                )
            ).all()
        )

    def get_template(self, template_id: int) -> PromptTemplate:
        template = self.session.get(PromptTemplate, template_id)
        if template is None:
            raise PromptTemplateNotFoundError("Prompt template not found")
        return template

    def _require_unique_name(self, normalized: str, *, except_id: int | None = None) -> None:
        existing = self.session.exec(
            select(PromptTemplate.id).where(PromptTemplate.name_normalized == normalized)
        ).all()
        if any(value != except_id for value in existing):
            raise PromptTemplateError("A prompt template with this name already exists")

    def create_template(self, **values: Any) -> PromptTemplate:
        name, normalized = normalize_template_name(values.get("name", ""))
        self._require_unique_name(normalized)
        system_prompt = _clean_body(
            values.get("system_prompt"),
            field="system_prompt",
            limit=MAX_SYSTEM_PROMPT_CHARS,
            required=True,
        )
        user_prefix = _clean_body(
            values.get("user_prefix"),
            field="user_prefix",
            limit=MAX_USER_PREFIX_CHARS,
            required=False,
        )
        description = _clean_body(
            values.get("description"),
            field="description",
            limit=MAX_DESCRIPTION_CHARS,
            required=False,
        )
        enabled = bool(values.get("enabled", True))
        is_default = bool(values.get("is_default", False))
        if is_default and not enabled:
            raise PromptTemplateError("The default template must be enabled")
        if is_default:
            self._clear_default()
        template = PromptTemplate(
            name=name,
            name_normalized=normalized,
            description=description,
            system_prompt=str(system_prompt),
            user_prefix=user_prefix,
            enabled=enabled,
            is_default=is_default,
            is_builtin=bool(values.get("is_builtin", False)),
        )
        self.session.add(template)
        self.session.flush()
        return template

    def update_template(self, template_id: int, changes: Mapping[str, Any]) -> PromptTemplate:
        template = self.get_template(template_id)
        if "name" in changes:
            name, normalized = normalize_template_name(changes["name"])
            self._require_unique_name(normalized, except_id=template.id)
            template.name = name
            template.name_normalized = normalized
        if "system_prompt" in changes:
            template.system_prompt = str(
                _clean_body(
                    changes["system_prompt"],
                    field="system_prompt",
                    limit=MAX_SYSTEM_PROMPT_CHARS,
                    required=True,
                )
            )
        if "user_prefix" in changes:
            template.user_prefix = _clean_body(
                changes["user_prefix"],
                field="user_prefix",
                limit=MAX_USER_PREFIX_CHARS,
                required=False,
            )
        if "description" in changes:
            template.description = _clean_body(
                changes["description"],
                field="description",
                limit=MAX_DESCRIPTION_CHARS,
                required=False,
            )
        enabled = bool(changes.get("enabled", template.enabled))
        is_default = bool(changes.get("is_default", template.is_default))
        if is_default and not enabled:
            raise PromptTemplateError("The default template must be enabled")
        if not enabled and template.is_default and not is_default:
            raise PromptTemplateError("Promote another template before disabling the default")
        if is_default and not template.is_default:
            self._clear_default(except_id=template.id)
        template.enabled = enabled
        template.is_default = is_default
        template.updated_at = utc_now()
        self.session.add(template)
        self.session.flush()
        return template

    def set_default_template(self, template_id: int) -> PromptTemplate:
        template = self.get_template(template_id)
        if not template.enabled:
            raise PromptTemplateError("A disabled template cannot be default")
        self._clear_default(except_id=template.id)
        template.is_default = True
        template.updated_at = utc_now()
        self.session.add(template)
        self.session.flush()
        return template

    def delete_template(self, template_id: int) -> None:
        template = self.get_template(template_id)
        if template.is_default:
            raise PromptTemplateError("The default template cannot be deleted")
        # Projects fall back to the default prompt rather than breaking.
        for project in self.session.exec(
            select(Project).where(Project.prompt_template_id == template_id)
        ).all():
            project.prompt_template_id = None
            project.updated_at = utc_now()
            self.session.add(project)
        self.session.delete(template)
        self.session.flush()

    def _clear_default(self, *, except_id: int | None = None) -> None:
        for current in self.session.exec(
            select(PromptTemplate).where(PromptTemplate.is_default.is_(True))
        ).all():
            if current.id == except_id:
                continue
            current.is_default = False
            current.updated_at = utc_now()
            self.session.add(current)
        self.session.flush()

    def preview(
        self,
        *,
        template_id: int | None = None,
        system_prompt: str | None = None,
        user_prefix: str | None = None,
        source_lang: str = "en",
        target_lang: str = "zh-CN",
    ) -> str:
        """Render exactly what a run would send, for unsaved or stored drafts.

        Draft text wins over ``template_id`` so the settings editor can preview
        edits before they are committed.
        """

        body = system_prompt
        prefix = user_prefix
        if body is None or not body.strip():
            if template_id is not None:
                template = self.get_template(template_id)
                body = template.system_prompt
                if prefix is None:
                    prefix = template.user_prefix
            else:
                body = None
        if body is not None and len(body) > MAX_SYSTEM_PROMPT_CHARS:
            raise PromptTemplateError(
                f"system_prompt must be at most {MAX_SYSTEM_PROMPT_CHARS} characters"
            )
        return compose_prompt(
            body,
            prefix,
            source_lang=source_lang,
            target_lang=target_lang,
        )


def seed_builtin_templates(session: Session) -> int:
    """Insert the built-in templates once, so the UI is never empty.

    Existing rows are left untouched: an admin may freely edit a seeded
    template, and re-seeding must not overwrite their wording.
    """

    created = 0
    service = PromptTemplateService(session)
    has_default = (
        session.exec(
            select(PromptTemplate.id).where(PromptTemplate.is_default.is_(True)).limit(1)
        ).first()
        is not None
    )
    for index, builtin in enumerate(BUILTIN_PROMPT_TEMPLATES):
        _, normalized = normalize_template_name(builtin["name"])
        existing = session.exec(
            select(PromptTemplate.id).where(PromptTemplate.name_normalized == normalized).limit(1)
        ).first()
        if existing is not None:
            continue
        service.create_template(
            **builtin,
            is_builtin=True,
            is_default=index == 0 and not has_default,
        )
        if index == 0 and not has_default:
            has_default = True
        created += 1
    return created


def compose_prompt(
    system_prompt: str | None,
    user_prefix: str | None,
    *,
    source_lang: str = "en",
    target_lang: str = "zh-CN",
) -> str:
    """Render a template body and fold its extra guidance into one system prompt.

    Keeping the prefix in the system message means the provider protocol and
    per-segment user message stay unchanged.
    """

    rendered = render_system_prompt(
        system_prompt,
        source_lang=source_lang,
        target_lang=target_lang,
    )
    prefix = (user_prefix or "").strip()
    if prefix:
        return f"{rendered}\n\n【额外要求】\n{prefix}"
    return rendered


def resolve_project_prompt(session: Session, project: Project) -> str:
    """Return the system prompt a project should translate with.

    Resolution order is the project's own template, then the default template,
    then the built-in prompt, so a project whose template was deleted or
    disabled still translates instead of failing the run.
    """

    template: PromptTemplate | None = None
    if project.prompt_template_id is not None:
        template = session.get(PromptTemplate, project.prompt_template_id)
        if template is not None and not template.enabled:
            template = None
    if template is None:
        template = session.exec(
            select(PromptTemplate).where(
                PromptTemplate.is_default.is_(True),
                PromptTemplate.enabled.is_(True),
            )
        ).first()
    return compose_prompt(
        template.system_prompt if template else None,
        template.user_prefix if template else None,
        source_lang=project.source_lang,
        target_lang=project.target_lang,
    )


__all__ = [
    "MAX_DESCRIPTION_CHARS",
    "MAX_SYSTEM_PROMPT_CHARS",
    "MAX_USER_PREFIX_CHARS",
    "PromptTemplateError",
    "PromptTemplateNotFoundError",
    "PromptTemplateService",
    "TEMPLATE_PLACEHOLDERS",
    "compose_prompt",
    "normalize_template_name",
    "prompt_template_read_dto",
    "resolve_project_prompt",
    "seed_builtin_templates",
]
