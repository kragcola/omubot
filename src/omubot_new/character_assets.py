"""One CAS pointer to immutable, inspected CCIP artifacts and source approvals.

The application metadata owner still owns aliases and per-bot relationships.
This owner registers finite build output; it never activates an external model.
Consumers receive an exact verified directory, never a scanned resource tree.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import tempfile
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from shutil import rmtree
from typing import Annotated, Literal

import portalocker
from pydantic import Field, JsonValue, TypeAdapter, ValidationError

from .character_pack_client import (
    MAX_ARTIFACT_BYTES,
    MAX_BATCH_CHARACTERS,
    MAX_BATCH_IMAGES,
    CharacterBuildArtifact,
    reference_identity_sha256,
)
from .character_recognition import CharacterReferencePack
from .character_reference import CharacterReferenceOwner
from .registry_files import (
    absolute_directory,
    ensure_directory_stable,
    read_bounded_file,
    safe_directory_status,
    same_file,
)
from .store import drain_on_cancel
from .types import OperationError, StrictModel

_Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
_Slug = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]
_ERROR = "invalid_character_artifact_file"
_MAX_REGISTRATION_BYTES = 4 * 1024 * 1024


class _Pointer(StrictModel):
    schema_version: Literal[1] = 1
    revision: _Hash


class _File(StrictModel):
    path: str = Field(min_length=1, max_length=512)
    sha256: _Hash
    size: int = Field(ge=0, le=MAX_ARTIFACT_BYTES)


class _Registration(StrictModel):
    schema_version: Literal[1] = 1
    pack_name: _Slug
    pack_dir: str
    character_ids: tuple[_Slug, ...] = Field(min_length=1, max_length=MAX_BATCH_CHARACTERS)
    request_sha256: _Hash
    reference_revision: _Hash
    reference_model: str
    reference_registry_version: str
    reference_identity_sha256: _Hash
    provider: str
    artifact_sha256: _Hash
    artifact_model: str | None
    artifact_registry_version: str | None
    runtime_model_match_verified: Literal[False] = False
    license_independently_verified: Literal[False] = False
    dim: int = Field(ge=1, le=4096)
    sources: list[dict[str, JsonValue]] = Field(min_length=1, max_length=MAX_BATCH_IMAGES)
    files: tuple[_File, ...] = Field(min_length=3, max_length=3 + MAX_BATCH_CHARACTERS * 3)


@dataclass(frozen=True, slots=True)
class InstalledCharacterAsset:
    revision: str
    pack_path: Path
    character_ids: tuple[str, ...]
    request_sha256: str
    artifact_sha256: str
    reference_revision: str
    artifact_model: str | None
    artifact_registry_version: str | None
    runtime_model_match_verified: Literal[False] = False


@dataclass(frozen=True, slots=True)
class CharacterAssetReceipt:
    revision: str
    previous_revision: str | None
    installation: InstalledCharacterAsset
    backup_path: Path | None
    changed: bool
    requires_reload: Literal[True] = True


@dataclass(frozen=True, slots=True)
class _Snapshot:
    metadata: os.stat_result
    raw: bytes
    revision: str


class CharacterAssetOwner:
    """Explicit artifact directory, one pack name, lock and atomic registration pointer."""

    def __init__(self, directory: str | Path, *, pack_name: str) -> None:
        self.directory = absolute_directory(directory, error_code=_ERROR)
        try:
            # Validate the one filename identifier at the configuration boundary.
            self.pack_name: str = TypeAdapter[str](_Slug).validate_python(pack_name, strict=True)
        except ValidationError:
            raise OperationError(_ERROR) from None
        self.path = self.directory / (".character-pack-" + self.pack_name + ".json")
        self._lock_path = self.directory / (".character-pack-" + self.pack_name + ".lock")
        self._backup_prefix = self.path.name + ".backup-"

    def _directory(self) -> os.stat_result:
        observed = safe_directory_status(self.directory, error_code=_ERROR)
        if observed is None:
            raise OperationError(_ERROR)
        return observed

    @contextmanager
    def _locked(self) -> Generator[None]:
        descriptor = os.open(self._lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "r+b") as handle:
            metadata = os.fstat(handle.fileno())
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                    or not same_file(metadata, self._lock_path.lstat())):
                raise OperationError(_ERROR)
            try:
                portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
            except portalocker.LockException:
                raise OperationError("character_artifact_busy") from None
            try:
                yield
            finally:
                portalocker.unlock(handle)

    def _snapshot(self, path: Path | None = None) -> _Snapshot | None:
        target = path or self.path
        try:
            metadata = target.lstat()
        except FileNotFoundError:
            return None
        raw = read_bounded_file(target, metadata, max_bytes=1024, error_code=_ERROR)
        try:
            pointer = _Pointer.model_validate_json(raw)
        except ValidationError:
            raise OperationError(_ERROR) from None
        return _Snapshot(metadata, raw, pointer.revision)

    def _version(self, revision: str) -> Path:
        return self.directory / (".character-pack-version-" + revision)

    def _consume(self, revision: str, reference: CharacterReferencePack) -> InstalledCharacterAsset:
        directory = self._version(revision)
        observed = safe_directory_status(directory, error_code=_ERROR)
        if observed is None:
            raise OperationError(_ERROR)
        path = directory / "registration.json"
        raw = read_bounded_file(path, path.lstat(), max_bytes=_MAX_REGISTRATION_BYTES, error_code=_ERROR)
        if hashlib.sha256(raw).hexdigest() != revision:
            raise OperationError("character_artifact_changed")
        try:
            record = _Registration.model_validate_json(raw)
        except ValidationError:
            raise OperationError(_ERROR) from None
        if (record.pack_name != self.pack_name or record.pack_dir != self.pack_name + ".charpack"
                or len(set(record.character_ids)) != len(record.character_ids)
                or record.reference_model != reference.model
                or record.reference_registry_version != reference.registry_version
                or record.reference_identity_sha256 != reference_identity_sha256(
                    reference, record.character_ids)):
            raise OperationError("character_artifact_reference_mismatch")
        names = {entry.path for entry in record.files}
        if (len(names) != len(record.files) or "artifact.zip" not in names
                or sum(entry.size for entry in record.files) > 2 * MAX_ARTIFACT_BYTES):
            raise OperationError(_ERROR)
        for entry in record.files:
            relative = PurePosixPath(entry.path)
            if (relative.is_absolute() or ".." in relative.parts or "\\" in entry.path
                    or str(relative) != entry.path or entry.path != "artifact.zip"
                    and not entry.path.startswith(record.pack_dir + "/")):
                raise OperationError(_ERROR)
            target = directory.joinpath(*relative.parts)
            parent = safe_directory_status(target.parent, error_code=_ERROR)
            if parent is None:
                raise OperationError(_ERROR)
            data = read_bounded_file(target, target.lstat(), max_bytes=entry.size, error_code=_ERROR)
            if len(data) != entry.size or hashlib.sha256(data).hexdigest() != entry.sha256:
                raise OperationError("character_artifact_changed")
            if entry.path == "artifact.zip" and entry.sha256 != record.artifact_sha256:
                raise OperationError("character_artifact_changed")
            ensure_directory_stable(target.parent, parent, error_code=_ERROR)
        ensure_directory_stable(directory, observed, error_code=_ERROR)
        return InstalledCharacterAsset(revision, directory / record.pack_dir, record.character_ids,
            record.request_sha256, record.artifact_sha256, record.reference_revision,
            record.artifact_model, record.artifact_registry_version)

    def read_for_reference(self, reference: CharacterReferencePack | None) -> InstalledCharacterAsset | None:
        """Exact, hash-checked artifact consumer; changed relationships do not change public identity."""
        observed = self._directory()
        snapshot = self._snapshot()
        if snapshot is None:
            return None
        if reference is None:
            raise OperationError("character_reference_pack_unavailable")
        installed = self._consume(snapshot.revision, reference)
        ensure_directory_stable(self.directory, observed, error_code=_ERROR)
        self._assert_current(snapshot)
        return installed

    def _assert_current(self, snapshot: _Snapshot | None) -> None:
        current = self._snapshot()
        if (snapshot is None and current is not None or snapshot is not None
                and (current is None or not same_file(snapshot.metadata, current.metadata)
                     or snapshot.raw != current.raw)):
            raise OperationError("character_artifact_changed")

    def _cas(self, expected_revision: str | None) -> _Snapshot | None:
        current = self._snapshot()
        if (current.revision if current else None) != expected_revision:
            raise OperationError("revision_conflict")
        return current

    def reuse(self, *, request_sha256: str, reference_owner: CharacterReferenceOwner,
              reference_revision: str, expected_revision: str | None) -> CharacterAssetReceipt | None:
        with reference_owner.hold_snapshot(reference_revision) as reference, self._locked():
            snapshot = self._cas(expected_revision)
            if snapshot is None:
                return None
            installed = self._consume(snapshot.revision, reference)
            if installed.request_sha256 != request_sha256:
                return None
            self._assert_current(snapshot)
            return CharacterAssetReceipt(installed.revision, installed.revision, installed, None, False)

    @staticmethod
    def _write(path: Path, raw: bytes) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

    def _publish(self, raw: bytes, snapshot: _Snapshot | None,
                 observed: os.stat_result) -> Path | None:
        descriptor, name = tempfile.mkstemp(prefix=".character-pack-pointer-", dir=self.directory)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            backup = None
            if snapshot is not None:
                backup = self.directory / (self._backup_prefix + uuid.uuid4().hex + ".json")
                self._write(backup, snapshot.raw)
            ensure_directory_stable(self.directory, observed, error_code=_ERROR)
            self._assert_current(snapshot)
            os.replace(temporary, self.path)
            return backup
        finally:
            temporary.unlink(missing_ok=True)

    def install(self, artifact: CharacterBuildArtifact, *, reference_owner: CharacterReferenceOwner,
                expected_revision: str | None) -> CharacterAssetReceipt:
        """Stage finite verified data; publish its pointer once under both existing owners' locks."""
        if artifact.pack_name != self.pack_name:
            raise OperationError("character_build_identity_mismatch")
        observed = self._directory()
        with reference_owner.hold_snapshot(artifact.reference_revision) as reference, self._locked():
            snapshot = self._cas(expected_revision)
            files = (("artifact.zip", artifact.zip_data),
                     *((artifact.pack_dir + "/" + name, data) for name, data in artifact.files))
            record = _Registration(pack_name=artifact.pack_name, pack_dir=artifact.pack_dir,
                character_ids=artifact.character_ids, request_sha256=artifact.request_sha256,
                reference_revision=artifact.reference_revision, reference_model=artifact.reference_model,
                reference_registry_version=artifact.reference_registry_version,
                reference_identity_sha256=artifact.reference_identity_sha256, provider=artifact.provider,
                artifact_sha256=artifact.artifact_sha256, artifact_model=artifact.artifact_model,
                artifact_registry_version=artifact.artifact_registry_version, dim=artifact.dim,
                sources=json.loads(artifact.provenance_json),
                files=tuple(_File(path=name, sha256=hashlib.sha256(data).hexdigest(), size=len(data))
                            for name, data in files))
            raw = json.dumps(record.model_dump(mode="json"), ensure_ascii=False,
                             sort_keys=True, separators=(",", ":")).encode()
            revision = hashlib.sha256(raw).hexdigest()
            version = self._version(revision)
            staging = Path(tempfile.mkdtemp(prefix=".character-pack-stage-", dir=self.directory))
            try:
                if safe_directory_status(version, error_code=_ERROR) is None:
                    for name, data in files:
                        target = staging / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        self._write(target, data)
                    self._write(staging / "registration.json", raw)
                    ensure_directory_stable(self.directory, observed, error_code=_ERROR)
                    os.rename(staging, version)
                installed = self._consume(revision, reference)
                if snapshot is not None and snapshot.revision == revision:
                    self._assert_current(snapshot)
                    return CharacterAssetReceipt(revision, revision, installed, None, False)
                pointer = _Pointer(revision=revision).model_dump_json().encode()
                backup = self._publish(pointer, snapshot, observed)
                self._consume(revision, reference)
                return CharacterAssetReceipt(revision, snapshot.revision if snapshot else None,
                                             installed, backup, True)
            finally:
                if staging.exists():
                    rmtree(staging)

    async def install_async(
        self, artifact: CharacterBuildArtifact, *, reference_owner: CharacterReferenceOwner,
        expected_revision: str | None,
    ) -> CharacterAssetReceipt:
        """Drain the one bounded file transaction before cancellation propagates."""
        return await drain_on_cancel(asyncio.create_task(asyncio.to_thread(
            self.install, artifact, reference_owner=reference_owner, expected_revision=expected_revision)))

    def restore(self, backup_path: Path, *, reference_owner: CharacterReferenceOwner,
                expected_revision: str) -> CharacterAssetReceipt:
        if (backup_path.parent != self.directory or not backup_path.name.startswith(self._backup_prefix)
                or backup_path.suffix != ".json"):
            raise OperationError("invalid_character_artifact_backup")
        observed = self._directory()
        previous = self._snapshot(backup_path)
        reference = reference_owner.read()
        if previous is None or reference is None:
            raise OperationError("invalid_character_artifact_backup")
        with reference_owner.hold_snapshot(reference.revision) as held, self._locked():
            snapshot = self._cas(expected_revision)
            installed = self._consume(previous.revision, held)
            backup = self._publish(previous.raw, snapshot, observed)
            return CharacterAssetReceipt(previous.revision, expected_revision, installed, backup, True)
