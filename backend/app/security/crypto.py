from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MASTER_KEY_ENV = "TRANS_MASTER_KEY_FILE"
MASTER_KEY_BYTES = 32
GCM_NONCE_BYTES = 12
DEV_MASTER_KEY_FILENAME = "credential-master.key"


class CredentialCryptoError(RuntimeError):
    """A safe-to-surface credential cryptography failure."""


@dataclass(frozen=True, slots=True)
class EncryptedCredential:
    ciphertext: bytes
    nonce: bytes
    key_version: int


def read_master_key_file(path: str | os.PathLike[str] | None = None) -> bytes:
    """Read an exact 256-bit key from a file, never from an environment value."""

    configured_path = path or os.environ.get(MASTER_KEY_ENV)
    if not configured_path:
        raise CredentialCryptoError(f"{MASTER_KEY_ENV} must point to a master-key file")
    try:
        key_path = Path(configured_path).expanduser()
        if not key_path.is_file():
            raise CredentialCryptoError("Master-key path is not a regular file")
        key = key_path.read_bytes()
    except CredentialCryptoError:
        raise
    except OSError as exc:
        raise CredentialCryptoError("Could not read the master-key file") from exc
    if len(key) != MASTER_KEY_BYTES:
        raise CredentialCryptoError(
            f"Master-key file must contain exactly {MASTER_KEY_BYTES} bytes"
        )
    return key


def _create_dev_master_key(key_path: Path) -> bytes:
    """Generate a 32-byte development key with owner-only permissions."""

    key = os.urandom(MASTER_KEY_BYTES)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    # O_EXCL so a concurrent worker cannot have its key silently overwritten.
    try:
        descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return read_master_key_file(key_path)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(key)
    except OSError as exc:
        raise CredentialCryptoError("Could not write the development master key") from exc
    return key


def resolve_master_key() -> bytes:
    """Return the credential master key for the current environment.

    Production always requires an explicitly configured key file. Development
    auto-provisions one under the state directory so credentials can be stored
    without manual setup; that key is not intended to protect real secrets.
    """

    if os.environ.get(MASTER_KEY_ENV):
        return read_master_key_file()

    from ..config import get_settings

    settings = get_settings()
    if settings.is_production:
        raise CredentialCryptoError(
            f"{MASTER_KEY_ENV} must be configured in production"
        )
    key_path = settings.resolved_state_dir / DEV_MASTER_KEY_FILENAME
    if key_path.is_file():
        return read_master_key_file(key_path)
    return _create_dev_master_key(key_path)


def master_key_is_ephemeral() -> bool:
    """Report whether credentials are protected by an auto-generated dev key.

    An operator-managed key file is durable and backed up with the deployment;
    the development fallback is neither, so the UI warns that stored keys become
    unreadable if the state directory is discarded.
    """

    return not os.environ.get(MASTER_KEY_ENV)


def credential_aad(credential_id: int, provider: str, key_version: int) -> bytes:
    """Build stable AAD binding a secret to its row, provider, and key version."""

    if credential_id < 1:
        raise CredentialCryptoError("A persisted credential ID is required")
    normalized_provider = provider.strip().lower()
    if not normalized_provider:
        raise CredentialCryptoError("A provider is required")
    if key_version < 1:
        raise CredentialCryptoError("Key version must be at least 1")
    return (
        f"trans/provider-credential/v1\x00{credential_id}\x00"
        f"{normalized_provider}\x00{key_version}"
    ).encode()


def encrypt_credential_secret(
    plaintext: str | bytes,
    *,
    credential_id: int,
    provider: str,
    key_version: int,
    master_key: bytes | None = None,
) -> EncryptedCredential:
    key = master_key if master_key is not None else resolve_master_key()
    if len(key) != MASTER_KEY_BYTES:
        raise CredentialCryptoError("Master key must be exactly 32 bytes")
    secret = plaintext.encode("utf-8") if isinstance(plaintext, str) else bytes(plaintext)
    if not secret:
        raise CredentialCryptoError("Credential secret cannot be empty")
    nonce = os.urandom(GCM_NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(
        nonce,
        secret,
        credential_aad(credential_id, provider, key_version),
    )
    return EncryptedCredential(
        ciphertext=ciphertext,
        nonce=nonce,
        key_version=key_version,
    )


def decrypt_credential_secret(
    ciphertext: bytes,
    nonce: bytes,
    *,
    credential_id: int,
    provider: str,
    key_version: int,
    master_key: bytes | None = None,
) -> str:
    key = master_key if master_key is not None else resolve_master_key()
    if len(key) != MASTER_KEY_BYTES:
        raise CredentialCryptoError("Master key must be exactly 32 bytes")
    if len(nonce) != GCM_NONCE_BYTES:
        raise CredentialCryptoError("Credential nonce is invalid")
    try:
        plaintext = AESGCM(key).decrypt(
            bytes(nonce),
            bytes(ciphertext),
            credential_aad(credential_id, provider, key_version),
        )
        return plaintext.decode("utf-8")
    except (InvalidTag, UnicodeDecodeError, ValueError) as exc:
        raise CredentialCryptoError("Credential could not be decrypted") from exc


# Short aliases for callers that treat this module as a generic secret envelope.
encrypt_secret = encrypt_credential_secret
decrypt_secret = decrypt_credential_secret


__all__ = [
    "CredentialCryptoError",
    "DEV_MASTER_KEY_FILENAME",
    "EncryptedCredential",
    "GCM_NONCE_BYTES",
    "MASTER_KEY_BYTES",
    "MASTER_KEY_ENV",
    "credential_aad",
    "decrypt_credential_secret",
    "decrypt_secret",
    "encrypt_credential_secret",
    "encrypt_secret",
    "master_key_is_ephemeral",
    "read_master_key_file",
    "resolve_master_key",
]
