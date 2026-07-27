from __future__ import annotations

from collections.abc import Generator

from alembic import command
from alembic.config import Config
from sqlalchemy import JSON, Boolean, Integer, String, Text, event, inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from .config import REPOSITORY_ROOT, get_settings

# Revision an unversioned-but-current database is adopted at. Bump this together
# with every new head revision, otherwise adoption stamps a stale version and
# the next upgrade replays migrations against existing tables.
SCHEMA_HEAD_REVISION = "0003_prompt_templates"


def create_db_engine(database_url: str | None = None, *, echo: bool | None = None) -> Engine:
    settings = get_settings()
    url = database_url or settings.effective_database_url
    connect_args = (
        {
            "check_same_thread": False,
            "timeout": 30.0,
        }
        if url.startswith("sqlite")
        else {}
    )
    return create_engine(
        url,
        echo=settings.debug if echo is None else echo,
        connect_args=connect_args,
        pool_pre_ping=True,
    )


engine = create_db_engine()


_LEGACY_0001_SCHEMA: dict[str, dict[str, tuple[type[object], bool, bool]]] = {
    "project": {
        "id": (Integer, False, True),
        "title": (Text, False, False),
        "source_lang": (String, False, False),
        "target_lang": (String, False, False),
        "source_type": (String, False, False),
        "source_path": (Text, False, False),
        "provider_cfg": (JSON, False, False),
        "status": (String, False, False),
        "created_at": (Text, False, False),
        "updated_at": (Text, False, False),
    },
    "chapter": {
        "id": (Integer, False, True),
        "project_id": (Integer, False, False),
        "ord": (Integer, False, False),
        "title": (Text, True, False),
        "href": (Text, True, False),
        "summary": (Text, True, False),
    },
    "segment": {
        "id": (Integer, False, True),
        "project_id": (Integer, False, False),
        "chapter_id": (Integer, False, False),
        "ord": (Integer, False, False),
        "stable_key": (String, False, False),
        "struct_path": (JSON, False, False),
        "source_text": (Text, False, False),
        "target_text": (Text, True, False),
        "src_hash": (String, False, False),
        "status": (String, False, False),
        "error_msg": (Text, True, False),
        "token_in": (Integer, True, False),
        "token_out": (Integer, True, False),
        "provider": (Text, True, False),
        "updated_at": (Text, False, False),
    },
    "glossary_term": {
        "id": (Integer, False, True),
        "project_id": (Integer, False, False),
        "source_term": (Text, False, False),
        "target_term": (Text, False, False),
        "note": (Text, True, False),
        "case_sensitive": (Boolean, False, False),
        "enabled": (Boolean, False, False),
    },
    "tm_entry": {
        "id": (Integer, False, True),
        "src_hash": (String, False, False),
        "source_lang": (String, False, False),
        "target_lang": (String, False, False),
        "source_text": (Text, False, False),
        "target_text": (Text, False, False),
        "hit_count": (Integer, False, False),
        "updated_at": (Text, False, False),
    },
}


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection: object, connection_record: object) -> None:
    del connection_record
    module_name = type(dbapi_connection).__module__
    if not module_name.startswith("sqlite3"):
        return
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def init_db(db_engine: Engine | None = None) -> None:
    """Create current metadata directly for isolated/custom engines.

    Production startup should call :func:`migrate_db` so Alembic records and
    evolves the schema version.
    """

    # Importing models registers all tables with SQLModel.metadata.
    from . import models  # noqa: F401

    get_settings().ensure_directories()
    SQLModel.metadata.create_all(db_engine or engine)


def _current_schema_spec() -> dict[str, dict[str, tuple[object, bool, bool]]]:
    """Build the adoption spec for the complete current SQLModel metadata."""

    spec: dict[str, dict[str, tuple[object, bool, bool]]] = {}
    for table_name, table in SQLModel.metadata.tables.items():
        spec[table_name] = {
            column.name: (
                column.type._type_affinity,
                bool(column.nullable),
                bool(column.primary_key),
            )
            for column in table.columns
        }
    return spec


def _schema_mismatches(
    inspector: object,
    business_tables: set[str],
    expected: dict[str, dict[str, tuple[object, bool, bool]]],
) -> list[str]:
    """Compare live tables/columns against an expected affinity spec."""

    mismatches: list[str] = []
    expected_names = set(expected)
    if business_tables != expected_names:
        missing = sorted(expected_names - business_tables)
        unexpected = sorted(business_tables - expected_names)
        if missing:
            mismatches.append(f"missing tables: {', '.join(missing)}")
        if unexpected:
            mismatches.append(f"unexpected tables: {', '.join(unexpected)}")
    for table_name in sorted(business_tables & expected_names):
        actual_column_rows = {
            column["name"]: column
            for column in inspector.get_columns(table_name)  # type: ignore[attr-defined]
        }
        expected_columns = expected[table_name]
        actual_names = set(actual_column_rows)
        expected_column_names = set(expected_columns)
        if actual_names != expected_column_names:
            missing = sorted(expected_column_names - actual_names)
            unexpected = sorted(actual_names - expected_column_names)
            detail = []
            if missing:
                detail.append(f"missing {missing}")
            if unexpected:
                detail.append(f"unexpected {unexpected}")
            mismatches.append(f"{table_name} columns: {', '.join(detail)}")
            continue
        for column_name, expected_column in expected_columns.items():
            actual_column = actual_column_rows[column_name]
            expected_affinity, expected_nullable, expected_primary_key = expected_column
            actual_affinity = actual_column["type"]._type_affinity
            if actual_affinity is not expected_affinity:
                mismatches.append(
                    f"{table_name}.{column_name} type "
                    f"{actual_affinity.__name__} != {expected_affinity.__name__}"
                )
            if bool(actual_column["primary_key"]) != expected_primary_key:
                mismatches.append(f"{table_name}.{column_name} primary-key flag differs")
            if bool(actual_column["nullable"]) != expected_nullable:
                mismatches.append(f"{table_name}.{column_name} nullability differs")
    return mismatches


def migrate_db(database_url: str | None = None) -> None:
    """Upgrade the configured database to the latest Alembic revision."""

    settings = get_settings()
    settings.ensure_directories()
    url = database_url or settings.effective_database_url
    config = Config(str(REPOSITORY_ROOT / "backend" / "alembic.ini"))
    config.attributes["database_url_override"] = url

    # Builds produced before migrations were wired into startup used
    # SQLModel.create_all(). Safely adopt only an exact, complete schema;
    # partial or unfamiliar databases must fail instead of being blindly stamped.
    from . import models  # noqa: F401

    probe = create_db_engine(url, echo=False)
    try:
        inspector = inspect(probe)
        all_tables = {
            table
            for table in inspector.get_table_names()
            if not table.startswith("sqlite_")
        }
        has_version = False
        if "alembic_version" in all_tables:
            with probe.connect() as connection:
                has_version = connection.execute(
                    text("SELECT version_num FROM alembic_version LIMIT 1")
                ).first() is not None

        business_tables = all_tables - {"alembic_version"}
        if business_tables and not has_version:
            legacy_expected = {
                table: {
                    column: (column_type()._type_affinity, nullable, primary_key)
                    for column, (column_type, nullable, primary_key) in columns.items()
                }
                for table, columns in _LEGACY_0001_SCHEMA.items()
            }
            current_mismatches = _schema_mismatches(
                inspector, business_tables, _current_schema_spec()
            )
            if not current_mismatches:
                # A complete database created by the current SQLModel metadata
                # (e.g. init_db or an embedded build) is adopted at head.
                command.stamp(config, SCHEMA_HEAD_REVISION)
            else:
                legacy_mismatches = _schema_mismatches(
                    inspector, business_tables, legacy_expected
                )
                if legacy_mismatches:
                    raise RuntimeError(
                        "Database has an unversioned schema that cannot be safely adopted: "
                        + "; ".join(legacy_mismatches)
                    )
                command.stamp(config, "0001_initial")
    finally:
        probe.dispose()

    command.upgrade(config, "head")


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


def session_factory() -> Session:
    """Return a synchronous session for background jobs and the CLI."""

    return Session(engine)


def checkpoint_wal(db_engine: Engine | None = None) -> None:
    """Truncate the SQLite write-ahead log so it cannot grow unbounded."""

    target = db_engine or engine
    if target.url.get_backend_name() != "sqlite":
        return
    with target.connect() as connection:
        connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
