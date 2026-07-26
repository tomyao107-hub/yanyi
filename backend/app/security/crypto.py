from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MASTER_KEY_ENV = "TRANS_MASTER_KEY_FILE"
MASTER_KEY_BYTES = 32
GCM_NONCE_BYTES = 12


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
    key = master_key if master_key is not None else read_master_key_file()
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
    key = master_key if master_key is not None else read_master_key_file()
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
    "EncryptedCredential",
    "GCM_NONCE_BYTES",
    "MASTER_KEY_BYTES",
    "MASTER_KEY_ENV",
    "credential_aad",
    "decrypt_credential_secret",
    "decrypt_secret",
    "encrypt_credential_secret",
    "encrypt_secret",
    "read_master_key_file",
]
