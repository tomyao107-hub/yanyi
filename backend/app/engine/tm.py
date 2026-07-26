from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..models import TMEntry, utc_now


class TranslationMemoryProtocol(Protocol):
    def lookup(self, src_hash: str) -> str | None: ...

    def store(self, src_hash: str, source_text: str, target_text: str) -> None: ...


class TranslationMemory:
    def __init__(self, session: Session, source_lang: str, target_lang: str) -> None:
        self.session = session
        self.source_lang = source_lang
        self.target_lang = target_lang

    def _entry(self, src_hash: str) -> TMEntry | None:
        return self.session.exec(
            select(TMEntry).where(
                TMEntry.src_hash == src_hash,
                TMEntry.source_lang == self.source_lang,
                TMEntry.target_lang == self.target_lang,
            )
        ).first()

    def lookup(self, src_hash: str) -> str | None:
        entry = self._entry(src_hash)
        if entry is None:
            return None
        now = utc_now()
        self.session.exec(
            update(TMEntry)
            .where(TMEntry.id == entry.id)
            .values(hit_count=TMEntry.hit_count + 1, updated_at=now)
        )
        self.session.commit()
        return entry.target_text

    def store(self, src_hash: str, source_text: str, target_text: str) -> None:
        values = {
            "src_hash": src_hash,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "source_text": source_text,
            "target_text": target_text,
            "updated_at": utc_now(),
        }
        if self.session.bind is not None and self.session.bind.dialect.name == "sqlite":
            statement = sqlite_insert(TMEntry).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=[
                    TMEntry.src_hash,
                    TMEntry.source_lang,
                    TMEntry.target_lang,
                ],
                set_={
                    "source_text": statement.excluded.source_text,
                    "target_text": statement.excluded.target_text,
                    "updated_at": statement.excluded.updated_at,
                },
            )
            self.session.exec(statement)
            self.session.commit()
            return

        entry = self._entry(src_hash)
        if entry is None:
            entry = TMEntry(**values)
        else:
            entry.source_text = source_text
            entry.target_text = target_text
            entry.updated_at = values["updated_at"]
        self.session.add(entry)
        try:
            self.session.commit()
        except IntegrityError:
            # Another worker inserted the same language/hash tuple. Roll back the
            # failed transaction, then update that winner rather than surfacing a
            # harmless cache race.
            self.session.rollback()
            entry = self._entry(src_hash)
            if entry is None:
                raise
            entry.source_text = source_text
            entry.target_text = target_text
            entry.updated_at = values["updated_at"]
            self.session.add(entry)
            self.session.commit()


@dataclass
class InMemoryTranslationMemory:
    entries: dict[str, str] = field(default_factory=dict)
    hit_count: int = 0

    def lookup(self, src_hash: str) -> str | None:
        result = self.entries.get(src_hash)
        if result is not None:
            self.hit_count += 1
        return result

    def store(self, src_hash: str, source_text: str, target_text: str) -> None:
        del source_text
        self.entries[src_hash] = target_text


def lookup_tm(
    session: Session,
    src_hash: str,
    source_lang: str,
    target_lang: str,
) -> str | None:
    return TranslationMemory(session, source_lang, target_lang).lookup(src_hash)


def upsert_tm(
    session: Session,
    src_hash: str,
    source_lang: str,
    target_lang: str,
    source_text: str,
    target_text: str,
) -> None:
    TranslationMemory(session, source_lang, target_lang).store(
        src_hash,
        source_text,
        target_text,
    )
