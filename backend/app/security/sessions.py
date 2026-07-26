from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..models import AdminSession, AdminUser, utc_now
from .passwords import hash_password, verify_password

SESSION_COOKIE_NAME = "trans_session"
SESSION_IDLE_TTL = timedelta(minutes=30)
SESSION_ABSOLUTE_TTL = timedelta(hours=8)
LOGIN_FAILURE_LIMIT = 5
_MAX_LOCKOUT = timedelta(minutes=15)

# Always perform an Argon2 verification for unknown usernames.
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


def normalize_username(username: str) -> str:
    return unicodedata.normalize("NFKC", username).strip().casefold()


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds")


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_client_ip(client_ip: str | None) -> str | None:
    if not client_ip:
        return None
    return hashlib.sha256(client_ip.encode("utf-8")).hexdigest()


def _password_hash_params() -> dict[str, object]:
    return {
        "algorithm": "argon2id",
        "time_cost": 3,
        "memory_cost_kib": 65536,
        "parallelism": 4,
    }


def initialize_admin(session: Session) -> bool:
    """Create the sole admin from a secret file when the database has none.

    Bootstrap inputs are deliberately ignored once any admin row exists. This
    prevents an old deployment secret from silently resetting an account.
    """

    existing = int(session.exec(select(func.count(AdminUser.id))).one())
    if existing:
        return False

    username = os.environ.get("TRANS_ADMIN_USERNAME", "").strip()
    password_file_value = os.environ.get("TRANS_ADMIN_BOOTSTRAP_PASSWORD_FILE", "").strip()
    if not username or not password_file_value:
        return False
    if len(username) > 150 or not normalize_username(username):
        raise RuntimeError("TRANS_ADMIN_USERNAME must contain 1-150 non-blank characters")

    password_file = Path(password_file_value).expanduser()
    try:
        if password_file.stat().st_size > 16 * 1024:
            raise RuntimeError("admin bootstrap password file is unexpectedly large")
        password = password_file.read_text(encoding="utf-8").rstrip("\r\n")
    except OSError as exc:
        raise RuntimeError("could not read admin bootstrap password file") from exc
    if len(password) < 12 or len(password) > 1024:
        raise RuntimeError("admin bootstrap password must contain 12-1024 characters")

    admin = AdminUser(
        username=username,
        normalized_username=normalize_username(username),
        password_hash=hash_password(password),
        password_hash_algorithm="argon2id",
        password_hash_params=_password_hash_params(),
    )
    session.add(admin)
    try:
        session.commit()
    except IntegrityError:
        # Another startup process may have won the one-time bootstrap race.
        session.rollback()
        if int(session.exec(select(func.count(AdminUser.id))).one()):
            return False
        raise
    return True


@dataclass(frozen=True, slots=True)
class SessionAuthentication:
    admin: AdminUser
    session: AdminSession


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated_at: float


class LoginTokenBucket:
    """Small process-local brake in addition to persistent database lockout."""

    def __init__(self, *, capacity: float = 10.0, refill_per_second: float = 1 / 6) -> None:
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def consume(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                self._buckets[key] = _Bucket(self.capacity - 1, now)
                return True
            bucket.tokens = min(
                self.capacity,
                bucket.tokens + (now - bucket.updated_at) * self.refill_per_second,
            )
            bucket.updated_at = now
            if bucket.tokens < 1:
                return False
            bucket.tokens -= 1
            return True


login_token_bucket = LoginTokenBucket()


def verify_login(
    session: Session,
    *,
    username: str,
    password: str,
    client_ip: str | None,
) -> AdminUser | None:
    normalized = normalize_username(username)
    bucket_key = f"{client_ip or '-'}\0{normalized}"
    if not login_token_bucket.consume(bucket_key):
        return None

    admin = session.exec(
        select(AdminUser).where(AdminUser.normalized_username == normalized)
    ).first()
    verification = verify_password(admin.password_hash if admin else _DUMMY_PASSWORD_HASH, password)
    if admin is None:
        return None

    now = datetime.now(UTC)
    locked = bool(admin.locked_until and _parse_timestamp(admin.locked_until) > now)
    if not admin.enabled or locked or not verification.valid:
        if not verification.valid:
            admin.failed_login_count += 1
            admin.last_failed_login_at = _timestamp(now)
            if admin.failed_login_count >= LOGIN_FAILURE_LIMIT:
                exponent = min(admin.failed_login_count - LOGIN_FAILURE_LIMIT, 5)
                delay = min(timedelta(seconds=30 * (2**exponent)), _MAX_LOCKOUT)
                admin.locked_until = _timestamp(now + delay)
            admin.updated_at = _timestamp(now)
            session.add(admin)
            session.commit()
        return None

    if verification.upgraded_hash:
        admin.password_hash = verification.upgraded_hash
        admin.password_hash_algorithm = "argon2id"
        admin.password_hash_params = _password_hash_params()
    admin.failed_login_count = 0
    admin.last_failed_login_at = None
    admin.locked_until = None
    admin.last_login_at = _timestamp(now)
    admin.updated_at = _timestamp(now)
    session.add(admin)
    session.commit()
    session.refresh(admin)
    return admin


def create_admin_session(
    session: Session,
    admin: AdminUser,
    *,
    client_ip: str | None,
    user_agent: str | None,
) -> tuple[str, AdminSession]:
    now = datetime.now(UTC)
    token = secrets.token_urlsafe(32)
    record = AdminSession(
        token_hash=hash_session_token(token),
        user_id=admin.id or 0,
        password_version=admin.password_version,
        expires_at=_timestamp(now + SESSION_ABSOLUTE_TTL),
        idle_expires_at=_timestamp(now + SESSION_IDLE_TTL),
        last_used_at=_timestamp(now),
        client_ip_hash=hash_client_ip(client_ip),
        user_agent=(user_agent or "")[:512] or None,
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return token, record


def authenticate_session(session: Session, token: str | None) -> SessionAuthentication | None:
    if not token:
        return None
    record = session.exec(
        select(AdminSession).where(AdminSession.token_hash == hash_session_token(token))
    ).first()
    if record is None or record.revoked_at is not None:
        return None

    now = datetime.now(UTC)
    admin = session.get(AdminUser, record.user_id)
    reason: str | None = None
    if _parse_timestamp(record.expires_at) <= now:
        reason = "absolute_expiry"
    elif _parse_timestamp(record.idle_expires_at) <= now:
        reason = "idle_expiry"
    elif admin is None or not admin.enabled:
        reason = "admin_disabled"
    elif record.password_version != admin.password_version:
        reason = "password_changed"
    if reason:
        record.revoked_at = _timestamp(now)
        record.revoke_reason = reason
        session.add(record)
        session.commit()
        return None

    record.last_used_at = _timestamp(now)
    record.idle_expires_at = _timestamp(
        min(now + SESSION_IDLE_TTL, _parse_timestamp(record.expires_at))
    )
    session.add(record)
    session.commit()
    return SessionAuthentication(admin=admin, session=record)


def revoke_session(session: Session, token: str | None, *, reason: str = "logout") -> None:
    if not token:
        return
    record = session.exec(
        select(AdminSession).where(AdminSession.token_hash == hash_session_token(token))
    ).first()
    if record is None or record.revoked_at is not None:
        return
    record.revoked_at = utc_now()
    record.revoke_reason = reason[:64]
    session.add(record)
    session.commit()


def set_admin_password(session: Session, admin: AdminUser, new_password: str) -> None:
    """Set an admin password and revoke every extant session (including CLI use)."""

    if len(new_password) < 12 or len(new_password) > 1024:
        raise ValueError("new password must contain 12-1024 characters")
    now = utc_now()
    admin.password_hash = hash_password(new_password)
    admin.password_hash_algorithm = "argon2id"
    admin.password_hash_params = _password_hash_params()
    admin.password_version += 1
    admin.password_changed_at = now
    admin.updated_at = now
    session.add(admin)

    active_sessions = session.exec(
        select(AdminSession).where(
            AdminSession.user_id == (admin.id or 0),
            AdminSession.revoked_at.is_(None),
        )
    ).all()
    for record in active_sessions:
        record.revoked_at = now
        record.revoke_reason = "password_changed"
        session.add(record)
    session.commit()


def change_admin_password(
    session: Session,
    admin: AdminUser,
    *,
    current_password: str,
    new_password: str,
) -> bool:
    verification = verify_password(admin.password_hash, current_password)
    if not verification.valid:
        return False
    if secrets.compare_digest(current_password, new_password):
        raise ValueError("new password must differ from current password")
    set_admin_password(session, admin, new_password)
    return True
