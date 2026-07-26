from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from .config import Settings

_CHUNK_SIZE = 1024 * 1024
_SAFE_SUFFIX_RE = re.compile(r"\.[A-Za-z0-9]{1,16}\Z")


class StorageError(RuntimeError):
    """Base error for managed artifact storage."""


class UnsafeObjectKey(StorageError):
    """Raised when an object key could escape managed storage."""


class UploadTooLarge(StorageError):
    def __init__(self, maximum_bytes: int) -> None:
        self.maximum_bytes = maximum_bytes
        super().__init__(f"upload exceeds {maximum_bytes} bytes")


@dataclass(frozen=True, slots=True)
class StoredObject:
    path: Path
    size_bytes: int
    sha256: str


class StorageService:
    """Filesystem storage for database-registered source and export artifacts.

    Object keys are opaque, random, single-component names. Paths returned by
    this service are internal implementation details and must not be serialized
    by API schemas.
    """

    def __init__(self, settings: Settings) -> None:
        self._roots = {
            "source": settings.resolved_upload_dir,
            "export": settings.resolved_export_dir,
        }
        self.temp_root = settings.resolved_temp_dir
        for root in (*self._roots.values(), self.temp_root):
            root.mkdir(parents=True, exist_ok=True)

    def root_for_kind(self, kind: str) -> Path:
        try:
            return self._roots[kind].resolve(strict=True)
        except KeyError as exc:
            raise StorageError(f"unsupported artifact kind: {kind!r}") from exc

    @staticmethod
    def random_object_key(suffix: str = "") -> str:
        normalized = suffix.lower()
        if normalized and not _SAFE_SUFFIX_RE.fullmatch(normalized):
            raise StorageError("invalid artifact filename suffix")
        return f"{uuid4().hex}{normalized}"

    # Compatibility-friendly alias for callers that describe this as allocation.
    allocate_object_key = random_object_key

    def _candidate(self, kind: str, object_key: str) -> tuple[Path, Path]:
        root = self.root_for_kind(kind)
        if not object_key or "\x00" in object_key:
            raise UnsafeObjectKey("object key is empty or malformed")
        relative = Path(object_key)
        if (
            relative.is_absolute()
            or len(relative.parts) != 1
            or relative.name != object_key
            or object_key in {".", ".."}
        ):
            raise UnsafeObjectKey("object key must be one relative path component")
        return root, root / object_key

    def object_path(
        self,
        kind: str,
        object_key: str,
        *,
        must_exist: bool = False,
    ) -> Path:
        root, candidate = self._candidate(kind, object_key)
        if candidate.is_symlink():
            raise UnsafeObjectKey("artifact path must not be a symbolic link")
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise UnsafeObjectKey("artifact path escapes managed storage") from exc
        if must_exist and not resolved.is_file():
            raise FileNotFoundError(object_key)
        return resolved

    resolve = object_path

    def part_path(self, kind: str, object_key: str) -> Path:
        final = self.object_path(kind, object_key)
        part = final.with_name(f"{final.name}.part")
        # Keep the containment check explicit if naming conventions change.
        try:
            part.resolve(strict=False).relative_to(self.root_for_kind(kind))
        except ValueError as exc:
            raise UnsafeObjectKey("temporary artifact path escapes managed storage") from exc
        if part.is_symlink():
            raise UnsafeObjectKey("temporary artifact path must not be a symbolic link")
        return part

    async def stream_upload(
        self,
        upload: UploadFile,
        *,
        kind: str,
        object_key: str,
        maximum_bytes: int,
    ) -> StoredObject:
        """Hash and durably publish an upload without buffering it in memory."""

        final = self.object_path(kind, object_key)
        part = self.part_path(kind, object_key)
        declared_size = getattr(upload, "size", None)
        try:
            if declared_size is not None and declared_size > maximum_bytes:
                raise UploadTooLarge(maximum_bytes)
            if final.exists() or part.exists():
                raise FileExistsError(object_key)

            digest = hashlib.sha256()
            total = 0
            try:
                with part.open("xb") as target:
                    while chunk := await upload.read(_CHUNK_SIZE):
                        total += len(chunk)
                        if total > maximum_bytes:
                            raise UploadTooLarge(maximum_bytes)
                        digest.update(chunk)
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(part, final)
                self._fsync_directory(final.parent)
            except BaseException:
                part.unlink(missing_ok=True)
                raise
            return StoredObject(path=final, size_bytes=total, sha256=digest.hexdigest())
        finally:
            await upload.close()

    def publish_generated(
        self,
        *,
        kind: str,
        object_key: str,
        generated_path: Path,
    ) -> StoredObject:
        """Hash, fsync, and atomically publish a writer-produced ``.part`` file."""

        final = self.object_path(kind, object_key)
        expected_part = self.part_path(kind, object_key)
        generated = generated_path.resolve(strict=False)
        if generated != expected_part or generated_path.is_symlink():
            raise UnsafeObjectKey("generated artifact is not the allocated temporary file")
        if final.exists():
            raise FileExistsError(object_key)
        if not generated.is_file():
            raise FileNotFoundError(generated_path.name)

        digest = hashlib.sha256()
        total = 0
        try:
            with generated.open("r+b") as source:
                for chunk in iter(lambda: source.read(_CHUNK_SIZE), b""):
                    total += len(chunk)
                    digest.update(chunk)
                os.fsync(source.fileno())
            os.replace(generated, final)
            self._fsync_directory(final.parent)
        except BaseException:
            expected_part.unlink(missing_ok=True)
            raise
        return StoredObject(path=final, size_bytes=total, sha256=digest.hexdigest())

    def discard_part(self, kind: str, object_key: str) -> None:
        self.part_path(kind, object_key).unlink(missing_ok=True)

    def delete_object(self, kind: str, object_key: str) -> None:
        root, candidate = self._candidate(kind, object_key)
        # Unlinking a leaf symlink removes the link, never its target.
        if candidate.is_symlink():
            candidate.unlink(missing_ok=True)
            return
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise UnsafeObjectKey("artifact path escapes managed storage") from exc
        if resolved.exists() and not resolved.is_file():
            raise StorageError("artifact object is not a regular file")
        resolved.unlink(missing_ok=True)
        self.part_path(kind, object_key).unlink(missing_ok=True)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        # Directory fsync is supported on POSIX. Windows guarantees replace
        # atomicity but does not allow opening directories this way.
        try:
            descriptor = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)
