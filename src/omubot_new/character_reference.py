"""Explicit public character metadata file owner; saved packs require reload.

No media scans, embeddings, external registry writes or real-person relations.
Cooperating writers use the one file lock; unrelated file replacement detected
before publication is an error. Runtime immutable packs remain caller-owned.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal

import portalocker

from .character_recognition import (
    CharacterReferencePack,
    ReferenceCharacter,
    load_reference_pack,
    parse_reference_pack,
)
from .registry_files import (
    absolute_directory,
    ensure_directory_stable,
    read_bounded_file,
    safe_directory_status,
    same_file,
)
from .types import OperationError

_MAX_BYTES = 256 * 1024
_FILE_ERROR = "invalid_character_reference_file"


@dataclass(frozen=True, slots=True)
class CharacterReferenceReceipt:
    path: Path
    previous_revision: str | None
    saved_pack: CharacterReferencePack
    backup_path: Path | None
    requires_reload: bool = True


@dataclass(frozen=True, slots=True)
class CharacterReferenceMutationReceipt:
    publication: CharacterReferenceReceipt
    operation: Literal["import", "merge"]
    changed: bool
    selected_ids: tuple[str, ...]
    added_count: int = 0
    existing_count: int = 0
    input_sha256: str | None = None
    series: str | None = None
    metadata_only: Literal[True] = True


@dataclass(frozen=True, slots=True)
class _Snapshot:
    metadata: os.stat_result
    raw: bytes
    pack: CharacterReferencePack


class CharacterReferenceOwner:
    """Synchronous local management; async applications dispatch it off-loop.

    Only explicit management operations write files. Receipts describe saved content, never
    the active runtime version. Restore accepts an exact owner-created backup path.
    Calling applications own admin/public-source authorization. Input hashes here
    identify metadata documents, never images, licenses or installed embeddings.
"""

    def __init__(self, path: str | Path, *, bot_id: str) -> None:
        self.path = Path(path)
        self._parent = absolute_directory(self.path.parent, error_code=_FILE_ERROR)
        if self.path.suffix != ".json" or not bot_id or bot_id != bot_id.strip() or len(bot_id) > 64:
            raise OperationError(_FILE_ERROR)
        self.bot_id = bot_id
        self._backup_prefix = "." + self.path.name + ".backup-"
        self._lock_path = self.path.with_name("." + self.path.name + ".lock")

    def _directory(self) -> os.stat_result:
        observed = safe_directory_status(self._parent, error_code=_FILE_ERROR)
        if observed is None:
            raise OperationError(_FILE_ERROR)
        return observed

    def _snapshot(self, path: Path, *, missing_ok: bool = False) -> _Snapshot | None:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            if missing_ok:
                return None
            raise OperationError(_FILE_ERROR) from None
        raw = read_bounded_file(path, metadata, max_bytes=_MAX_BYTES, error_code=_FILE_ERROR)
        pack = load_reference_pack(path)
        if pack.bot_id != self.bot_id:
            raise OperationError("character_reference_bot_mismatch")
        if (pack.revision != hashlib.sha256(raw).hexdigest()
                or not same_file(metadata, path.lstat())):
            raise OperationError("character_reference_file_changed")
        return _Snapshot(metadata, raw, pack)

    def read(self) -> CharacterReferencePack | None:
        observed = self._directory()
        snapshot = self._snapshot(self.path, missing_ok=True)
        ensure_directory_stable(self._parent, observed, error_code=_FILE_ERROR)
        return snapshot.pack if snapshot is not None else None

    @contextmanager
    def hold_snapshot(self, expected_revision: str) -> Generator[CharacterReferencePack]:
        """Hold the existing metadata writer lock while a public artifact is registered."""
        observed = self._directory()
        with self._locked():
            snapshot = self._cas_snapshot(expected_revision)
            if snapshot is None:
                raise OperationError("character_reference_pack_unavailable")
            yield snapshot.pack
            ensure_directory_stable(self._parent, observed, error_code=_FILE_ERROR)
            self._assert_unchanged(snapshot)

    @contextmanager
    def _locked(self) -> Generator[None]:
        descriptor = os.open(self._lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "r+b") as handle:
            metadata = os.fstat(handle.fileno())
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                    or not same_file(metadata, self._lock_path.lstat())):
                raise OperationError(_FILE_ERROR)
            try:
                portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
            except portalocker.LockException:
                raise OperationError("character_reference_busy") from None
            try:
                yield
            finally:
                portalocker.unlock(handle)

    def _stage(self, raw: bytes) -> Path:
        descriptor, name = tempfile.mkstemp(prefix=".character-reference-", suffix=".json", dir=self._parent)
        path = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            path.unlink()
            raise
        return path

    def _backup(self, raw: bytes) -> Path:
        path = self.path.with_name(self._backup_prefix + uuid.uuid4().hex + ".json")
        with path.open("xb") as handle:
            os.chmod(path, 0o600)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        return path

    def _assert_unchanged(self, snapshot: _Snapshot | None) -> None:
        current = self._snapshot(self.path, missing_ok=True)
        if snapshot is None:
            if current is not None:
                raise OperationError("character_reference_file_changed")
        elif (current is None or not same_file(snapshot.metadata, current.metadata)
              or snapshot.raw != current.raw):
            raise OperationError("character_reference_file_changed")

    def _publish_locked(
        self, raw: bytes, snapshot: _Snapshot | None, observed: os.stat_result,
    ) -> CharacterReferenceReceipt:
        """Publish through the shared owner while its caller holds the file lock."""
        temporary: Path | None = None
        try:
            temporary = self._stage(raw)
            candidate = self._snapshot(temporary)
            assert candidate is not None
            backup = self._backup(snapshot.raw) if snapshot is not None else None
            ensure_directory_stable(self._parent, observed, error_code=_FILE_ERROR)
            self._assert_unchanged(snapshot)
            os.replace(temporary, self.path)
            temporary = None
            saved = self._snapshot(self.path)
            if saved is None or saved.pack != candidate.pack:
                raise OperationError("character_reference_file_changed")
            previous = snapshot.pack.revision if snapshot is not None else None
            return CharacterReferenceReceipt(self.path, previous, saved.pack, backup)
        except OSError:
            raise OperationError("character_reference_write_failed") from None
        finally:
            if temporary is not None:
                temporary.unlink()

    def _cas_snapshot(self, expected_revision: str | None) -> _Snapshot | None:
        snapshot = self._snapshot(self.path, missing_ok=True)
        previous = snapshot.pack.revision if snapshot is not None else None
        if previous != expected_revision:
            raise OperationError("revision_conflict")
        return snapshot

    def _unchanged_receipt(self, snapshot: _Snapshot, observed: os.stat_result) -> CharacterReferenceReceipt:
        ensure_directory_stable(self._parent, observed, error_code=_FILE_ERROR)
        self._assert_unchanged(snapshot)
        return CharacterReferenceReceipt(self.path, snapshot.pack.revision, snapshot.pack, None)

    @staticmethod
    def _document(pack: CharacterReferencePack) -> bytes:
        value = asdict(pack)
        value.pop("revision")
        return json.dumps({"schema_version": 1, **value}, ensure_ascii=False,
                          sort_keys=True, separators=(",", ":")).encode("utf-8")

    def save(self, raw: bytes, *, expected_revision: str | None) -> CharacterReferenceReceipt:
        """CAS-publish a full document, retaining complete previous bytes before replacement."""
        if type(raw) is not bytes or len(raw) > _MAX_BYTES:
            raise OperationError("invalid_character_reference_pack")
        observed = self._directory()
        with self._locked():
            return self._publish_locked(raw, self._cas_snapshot(expected_revision), observed)

    def import_metadata(
        self, raw: bytes, *, expected_revision: str | None,
    ) -> CharacterReferenceMutationReceipt:
        """Add explicitly supplied identities; identical entries never rewrite the file.

        Existing per-bot relations and identity metadata cannot be overwritten by
        import. Different content for an existing ID requires explicit resolution.
        """
        observed = self._directory()
        with self._locked():
            snapshot = self._cas_snapshot(expected_revision)
            incoming = parse_reference_pack(raw)
            if incoming.bot_id != self.bot_id:
                raise OperationError("character_reference_bot_mismatch")
            selected = tuple(item.character_id for item in incoming.characters)
            if snapshot is None:
                receipt = self._publish_locked(raw, None, observed)
                added_count = len(incoming.characters)
            else:
                current = snapshot.pack
                if ((current.registry_version, current.model, current.threshold)
                        != (incoming.registry_version, incoming.model, incoming.threshold)):
                    raise OperationError("character_reference_contract_mismatch")
                existing = {item.character_id: item for item in current.characters}
                additions: list[ReferenceCharacter] = []
                for item in incoming.characters:
                    previous = existing.get(item.character_id)
                    if previous is None:
                        additions.append(item)
                    elif previous != item:
                        raise OperationError("character_reference_identity_conflict")
                added_count = len(additions)
                if additions:
                    candidate = replace(current, characters=(*current.characters, *additions))
                    receipt = self._publish_locked(self._document(candidate), snapshot, observed)
                else:
                    receipt = self._unchanged_receipt(snapshot, observed)
            return CharacterReferenceMutationReceipt(
                receipt, "import", bool(added_count), selected, added_count,
                len(incoming.characters) - added_count, input_sha256=incoming.revision,
            )

    def merge_metadata(
        self, *, character_ids: tuple[str, ...], series: str, work: str, expected_revision: str,
    ) -> CharacterReferenceMutationReceipt:
        """Group explicit IDs; other members and identity/source/relation fields stay intact."""
        if (len(character_ids) < 2 or len(set(character_ids)) != len(character_ids)
                or not series.strip() or len(series) > 128 or not work.strip() or len(work) > 160):
            raise OperationError("invalid_character_reference_merge")
        observed = self._directory()
        with self._locked():
            snapshot = self._cas_snapshot(expected_revision)
            if snapshot is None:
                raise OperationError("character_reference_pack_unavailable")
            current = snapshot.pack
            selected = set(character_ids)
            if not selected.issubset(item.character_id for item in current.characters):
                raise OperationError("character_reference_character_missing")
            characters = tuple(
                replace(item, series=series, work=work) if item.character_id in selected else item
                for item in current.characters
            )
            changed = characters != current.characters
            receipt = (self._publish_locked(self._document(replace(current, characters=characters)),
                                           snapshot, observed)
                       if changed else self._unchanged_receipt(snapshot, observed))
            return CharacterReferenceMutationReceipt(
                receipt, "merge", changed, character_ids, existing_count=len(character_ids), series=series,
            )

    def restore(self, backup_path: str | Path, *, expected_revision: str,
                backup_revision: str) -> CharacterReferenceReceipt:
        """Restore exact backup bytes through the same CAS and pre-save backup."""
        backup = Path(backup_path)
        if (backup.parent != self._parent or not backup.name.startswith(self._backup_prefix)
                or backup.suffix != ".json"):
            raise OperationError("invalid_character_reference_backup")
        observed = self._directory()
        raw = read_bounded_file(backup, backup.lstat(), max_bytes=_MAX_BYTES, error_code=_FILE_ERROR)
        ensure_directory_stable(self._parent, observed, error_code=_FILE_ERROR)
        if hashlib.sha256(raw).hexdigest() != backup_revision:
            raise OperationError("character_reference_backup_changed")
        return self.save(raw, expected_revision=expected_revision)
