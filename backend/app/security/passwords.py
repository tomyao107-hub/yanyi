from __future__ import annotations

from dataclasses import dataclass

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Argon2id parameters follow argon2-cffi's current memory-hard interactive default.
_PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)


@dataclass(frozen=True, slots=True)
class PasswordVerification:
    valid: bool
    upgraded_hash: str | None = None


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("password must not be empty")
    return _PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> PasswordVerification:
    """Verify a password and return a stronger replacement hash when required."""

    try:
        valid = _PASSWORD_HASHER.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return PasswordVerification(valid=False)
    if not valid:
        return PasswordVerification(valid=False)
    upgraded = _PASSWORD_HASHER.hash(password) if _PASSWORD_HASHER.check_needs_rehash(
        password_hash
    ) else None
    return PasswordVerification(valid=True, upgraded_hash=upgraded)
