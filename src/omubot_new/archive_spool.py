"""Encrypted, short-lived storage for authorized group text awaiting extraction."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import stat
import struct
import tempfile
import time
from bisect import bisect_right
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from typing import Final, cast

import portalocker
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_SOURCE_ID = re.compile(r"src_[0-9a-fA-F]{64}\Z")
_ENTRY_NAME = re.compile(r"(src_[0-9a-fA-F]{64})\.spool\Z")
_TEMP_ENTRY_NAME = re.compile(r"\.spool-tmp-[a-z0-9_]{8}\Z")
_ENTRY_SUFFIX: Final = ".spool"
_CLOCK_NAME: Final = ".clock"
_LOCK_NAME: Final = ".spool.lock"
_CLEANER_LOCK_NAME: Final = ".cleaner.lock"
_CLEANER_STATUS_NAME: Final = ".cleaner.status"
_BINDING_NAME: Final = ".spool.identity"
_BINDING_MAGIC: Final = b"OMUBOT-N6-SPOOL\x01"
_BINDING_ID = re.compile(r"[0-9a-f]{64}\Z")
_CLEANER_STATUS_MAX_BYTES: Final = 512
_CLEANER_HEARTBEAT_SECONDS: Final = 5.0
_CLEANER_MAX_AGE_SECONDS: Final = 15.0
_TEMP_PREFIX: Final = ".spool-tmp-"
_ENTRY_MAGIC: Final = b"OMUBOT-SPOOL\x01"
_DECISION_MAGIC: Final = b"OMUBOT-DECISION\x01"
_CLOCK_MAGIC: Final = b"OMUBOT-CLOCK\x01"
_CLOCK_AAD: Final = b"omubot-archive-spool-clock-v1"
_KEY_BYTES: Final = 32
_NONCE_BYTES: Final = 12
_TAG_BYTES: Final = 16
_MAX_TEXT_BYTES: Final = 16 * 1024
_MAX_DECISION_BYTES: Final = 8 * 1024
_TTL_SECONDS: Final = 24 * 60 * 60
_MAX_SPOOL_ENTRIES: Final = 2048
_MAX_SPOOL_BYTES: Final = 32 * 1024 * 1024
_ENTRY_MAX_BYTES: Final = max(
    len(_ENTRY_MAGIC) + 16 + _NONCE_BYTES + _MAX_TEXT_BYTES + _TAG_BYTES,
    len(_DECISION_MAGIC) + 16 + _NONCE_BYTES + _MAX_DECISION_BYTES + _TAG_BYTES,
)
_CLOCK_FILE_BYTES: Final = len(_CLOCK_MAGIC) + _NONCE_BYTES + 8 + _TAG_BYTES
_NOFOLLOW: Final = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY: Final = getattr(os, "O_DIRECTORY", 0)


class EncryptedTextSpoolError(ValueError):
    """A fail-closed error that does not disclose stored text or key material."""


def _invalid_spool() -> EncryptedTextSpoolError:
    return EncryptedTextSpoolError("invalid_encrypted_text_spool")


def spool_binding_id(
    bot_id: str,
    database_path: Path | str,
    spool_dir: Path | str,
    key_file: Path | str,
) -> str:
    """Stable, public identity for one Bot's dedicated N6 storage paths."""
    if type(bot_id) is not str or not bot_id:
        raise _invalid_spool()
    database = _absolute_path(database_path)
    spool = _absolute_path(spool_dir)
    key = _absolute_path(key_file)
    return hashlib.sha256(
        b"omubot-n6-spool-v1\0"
        + bot_id.encode("utf-8") + b"\0"
        + os.fsencode(database) + b"\0"
        + os.fsencode(spool) + b"\0"
        + os.fsencode(key)
    ).hexdigest()


def _absolute_path(value: Path | str) -> Path:
    try:
        raw = os.fspath(value)
        if type(raw) is not str or not raw:
            raise _invalid_spool()
        return Path(os.path.abspath(raw))
    except (OSError, TypeError, ValueError):
        raise _invalid_spool() from None


def _check_path_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            return
        except OSError:
            raise _invalid_spool() from None
        if stat.S_ISLNK(metadata.st_mode):
            raise _invalid_spool()
        if current != path and not stat.S_ISDIR(metadata.st_mode):
            raise _invalid_spool()


def _validate_owner(metadata: os.stat_result) -> None:
    if hasattr(os, "geteuid") and metadata.st_uid != os.geteuid():
        raise _invalid_spool()


def _validate_private_file(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise _invalid_spool()
    _validate_owner(metadata)


def _validate_private_directory(metadata: os.stat_result) -> None:
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise _invalid_spool()
    _validate_owner(metadata)


def _lstat_optional(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        raise _invalid_spool() from None


def _read_descriptor(descriptor: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= limit:
        chunk = os.read(descriptor, min(4096, limit + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    if total > limit:
        raise _invalid_spool()
    return b"".join(chunks)


def _sync_directory(path: Path, *, private: bool = True) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
        metadata = os.fstat(descriptor)
        if private:
            _validate_private_directory(metadata)
        elif not stat.S_ISDIR(metadata.st_mode):
            raise _invalid_spool()
        else:
            _validate_owner(metadata)
        os.fsync(descriptor)
    except EncryptedTextSpoolError:
        raise
    except OSError:
        raise _invalid_spool() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


class EncryptedTextSpool:
    """Store UTF-8 text as authenticated ciphertext for at most 24 hours.

    Authorization is intentionally outside this primitive. The caller must only
    pass text whose source has already passed the applicable policy gate.
    """

    def __init__(
        self,
        ciphertext_dir: Path | str,
        key_file: Path | str,
        *,
        clock: Callable[[], float] | None = None,
        require_cleaner: bool = False,
        binding_id: str | None = None,
    ) -> None:
        self.ciphertext_dir = _absolute_path(ciphertext_dir)
        self.key_file = _absolute_path(key_file)
        if (
            self.ciphertext_dir == self.key_file
            or self.key_file.is_relative_to(self.ciphertext_dir)
            or self.ciphertext_dir.is_relative_to(self.key_file)
        ):
            raise _invalid_spool()
        if type(require_cleaner) is not bool:
            raise _invalid_spool()
        if binding_id is not None and (
            type(binding_id) is not str or _BINDING_ID.fullmatch(binding_id) is None
        ):
            raise _invalid_spool()
        self._clock = time.time if clock is None else clock
        self.require_cleaner = require_cleaner
        self.binding_id = binding_id

    @staticmethod
    def create_key_file(key_file: Path | str) -> None:
        """Create a fresh 32-byte key file with mode 0600 and no overwrite."""
        path = _absolute_path(key_file)
        _check_path_components(path)
        parent = path.parent
        try:
            parent_metadata = os.lstat(parent)
        except OSError:
            raise _invalid_spool() from None
        if not stat.S_ISDIR(parent_metadata.st_mode):
            raise _invalid_spool()
        _validate_owner(parent_metadata)

        descriptor: int | None = None
        created_metadata: os.stat_result | None = None
        succeeded = False
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
                0o600,
            )
            created_metadata = os.fstat(descriptor)
            os.fchmod(descriptor, 0o600)
            created_metadata = os.fstat(descriptor)
            _validate_private_file(created_metadata)
            key_material = os.urandom(_KEY_BYTES)
            remaining = memoryview(key_material)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise _invalid_spool()
                remaining = remaining[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            _sync_directory(parent, private=False)
            succeeded = True
        except EncryptedTextSpoolError:
            raise
        except (OSError, ValueError):
            raise _invalid_spool() from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if created_metadata is not None and not succeeded:
                try:
                    current = os.lstat(path)
                    if (current.st_dev, current.st_ino) == (
                        created_metadata.st_dev,
                        created_metadata.st_ino,
                    ):
                        os.unlink(path)
                except OSError:
                    pass

    def put(self, source_id: str, text: str) -> float:
        """Encrypt a source and return its fixed expiry deadline."""
        self._validate_source_id(source_id)
        if self.require_cleaner and not self.cleaner_available():
            raise _invalid_spool()
        if type(text) is not str:
            raise _invalid_spool()
        try:
            plaintext = text.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise _invalid_spool() from None
        if len(plaintext) > _MAX_TEXT_BYTES:
            raise _invalid_spool()

        with self._locked():
            key = self._read_key()
            now, entries = self._advance_clock(key)
            path = self._entry_path(source_id)
            if source_id in entries or _lstat_optional(path) is not None:
                raise _invalid_spool()
            entry_size = len(_ENTRY_MAGIC) + 16 + _NONCE_BYTES + len(plaintext) + _TAG_BYTES
            if (
                len(entries) >= _MAX_SPOOL_ENTRIES
                or sum(entries.values()) + entry_size > _MAX_SPOOL_BYTES
            ):
                raise _invalid_spool()
            expires_at = now + _TTL_SECONDS
            if not math.isfinite(expires_at):
                raise _invalid_spool()
            timestamp_bytes = struct.pack(">dd", now, expires_at)
            associated_data = self._entry_aad(source_id, timestamp_bytes)
            nonce = os.urandom(_NONCE_BYTES)
            encrypted = AESGCM(key).encrypt(nonce, plaintext, associated_data)
            self._atomic_write(
                path,
                _ENTRY_MAGIC + timestamp_bytes + nonce + encrypted,
                replace=False,
            )
            return expires_at

    def expiry(self, source_id: str) -> float | None:
        """Return the recorded deadline of one currently retained entry."""
        self._validate_source_id(source_id)
        with self._locked():
            key = self._read_key()
            now, entries = self._advance_clock(key)
            if source_id not in entries:
                return None
            _, _, expires_at, _ = self._read_entry(source_id, key)
            return expires_at if expires_at > now else None

    def read(self, source_id: str) -> str | None:
        """Return an unexpired source, or ``None`` when it is absent or expired."""
        self._validate_source_id(source_id)
        with self._locked():
            key = self._read_key()
            now, entries = self._advance_clock(key)
            if source_id not in entries:
                return None
            kind, captured_at, expires_at, plaintext = self._read_entry(source_id, key)
            if kind != "text" or captured_at > now or expires_at <= now:
                return None
            try:
                return plaintext.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                raise _invalid_spool() from None

    def seal_decision(self, source_id: str, decision: str) -> None:
        """Atomically replace raw text with a short encrypted extraction decision.

        The original deadline is retained.  A replay may provide only the exact
        same decision; it cannot replace a committed decision or restore text.
        """
        self._validate_source_id(source_id)
        if type(decision) is not str:
            raise _invalid_spool()
        try:
            plaintext = decision.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise _invalid_spool() from None
        if len(plaintext) > _MAX_DECISION_BYTES:
            raise _invalid_spool()
        with self._locked():
            key = self._read_key()
            now, entries = self._advance_clock(key)
            if source_id not in entries:
                raise _invalid_spool()
            kind, captured_at, expires_at, existing = self._read_entry(source_id, key)
            if captured_at > now or expires_at <= now:
                raise _invalid_spool()
            if kind == "decision":
                if existing != plaintext:
                    raise _invalid_spool()
                return
            timestamp_bytes = struct.pack(">dd", captured_at, expires_at)
            nonce = os.urandom(_NONCE_BYTES)
            encrypted = AESGCM(key).encrypt(
                nonce,
                plaintext,
                self._entry_aad(source_id, timestamp_bytes, _DECISION_MAGIC),
            )
            self._atomic_write(
                self._entry_path(source_id),
                _DECISION_MAGIC + timestamp_bytes + nonce + encrypted,
                replace=True,
            )

    def read_decision(self, source_id: str) -> str | None:
        """Read only an authenticated, unexpired extraction decision."""
        self._validate_source_id(source_id)
        with self._locked():
            key = self._read_key()
            now, entries = self._advance_clock(key)
            if source_id not in entries:
                return None
            kind, captured_at, expires_at, plaintext = self._read_entry(source_id, key)
            if kind != "decision" or captured_at > now or expires_at <= now:
                return None
            try:
                return plaintext.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                raise _invalid_spool() from None

    def delete(self, source_id: str) -> bool:
        """Remove one source early; return whether an entry was present."""
        self._validate_source_id(source_id)
        with self._locked():
            key = self._read_key()
            _, entries = self._advance_clock(key)
            if source_id not in entries:
                return False
            path = self._entry_path(source_id)
            try:
                _validate_private_file(os.lstat(path))
                os.unlink(path)
                _sync_directory(self.ciphertext_dir)
                return True
            except EncryptedTextSpoolError:
                raise
            except OSError:
                raise _invalid_spool() from None

    def delete_raw_text(self, source_id: str) -> bool:
        """Remove an entry only while it is still raw text, preserving decisions."""
        self._validate_source_id(source_id)
        with self._locked():
            key = self._read_key()
            _, entries = self._advance_clock(key)
            if source_id not in entries:
                return False
            kind, _, _, _ = self._read_entry(source_id, key)
            if kind != "text":
                return False
            path = self._entry_path(source_id)
            try:
                _validate_private_file(os.lstat(path))
                os.unlink(path)
                _sync_directory(self.ciphertext_dir)
                return True
            except EncryptedTextSpoolError:
                raise
            except OSError:
                raise _invalid_spool() from None

    def purge_expired(self) -> int:
        """Remove every authenticated entry whose fixed retention window ended."""
        with self._locked():
            key = self._read_key()
            now, entries = self._advance_clock(key)
            expired: list[Path] = []
            for source_id in sorted(entries):
                try:
                    _, _, expires_at, _ = self._read_entry(source_id, key)
                except EncryptedTextSpoolError:
                    # An unauthenticatable entry cannot be returned; discard it and
                    # continue so it cannot block cleanup of other expired entries.
                    expired.append(self._entry_path(source_id))
                    continue
                if expires_at <= now:
                    expired.append(self._entry_path(source_id))
            if not expired:
                return 0
            try:
                for path in expired:
                    _validate_private_file(os.lstat(path))
                    os.unlink(path)
                _sync_directory(self.ciphertext_dir)
            except EncryptedTextSpoolError:
                raise
            except OSError:
                raise _invalid_spool() from None
            return len(expired)

    def clear(self, *, expected_directory_identity: tuple[int, int] | None = None) -> int:
        """Delete strict spool entries without reading the key or recursing.

        This is reserved for a fail-closed capture shutdown. The private spool
        directory and every entry are validated before unlinking any source.
        Clock/lock files are retained; unknown or abnormal entries abort cleanup.
        """
        _check_path_components(self.ciphertext_dir)
        if _lstat_optional(self.ciphertext_dir) is None:
            if expected_directory_identity is not None:
                raise _invalid_spool()
            return 0

        with self._locked():
            descriptor: int | None = None
            try:
                descriptor = os.open(
                    self.ciphertext_dir,
                    os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
                )
                directory = os.fstat(descriptor)
                _validate_private_directory(directory)
                if (
                    expected_directory_identity is not None
                    and (directory.st_dev, directory.st_ino) != expected_directory_identity
                ):
                    raise _invalid_spool()
                source_entries: list[tuple[str, tuple[int, ...]]] = []
                temporary_entries: list[tuple[str, tuple[int, ...]]] = []

                def signature(metadata: os.stat_result) -> tuple[int, ...]:
                    return (
                        metadata.st_dev,
                        metadata.st_ino,
                        metadata.st_mode,
                        metadata.st_uid,
                        metadata.st_nlink,
                        metadata.st_size,
                    )

                with os.scandir(descriptor) as children:
                    for child in children:
                        name = child.name
                        metadata = child.stat(follow_symlinks=False)
                        if _ENTRY_NAME.fullmatch(name) is not None:
                            _validate_private_file(metadata)
                            if metadata.st_size > _ENTRY_MAX_BYTES:
                                raise _invalid_spool()
                            source_entries.append((name, signature(metadata)))
                        elif _TEMP_ENTRY_NAME.fullmatch(name) is not None:
                            _validate_private_file(metadata)
                            if metadata.st_size > _ENTRY_MAX_BYTES:
                                raise _invalid_spool()
                            temporary_entries.append((name, signature(metadata)))
                        elif name in {
                            _CLOCK_NAME, _LOCK_NAME, _CLEANER_LOCK_NAME,
                            _CLEANER_STATUS_NAME, _BINDING_NAME,
                        }:
                            _validate_private_file(metadata)
                            if name == _CLEANER_STATUS_NAME and metadata.st_size > _CLEANER_STATUS_MAX_BYTES:
                                raise _invalid_spool()
                        else:
                            raise _invalid_spool()
                if len(source_entries) + len(temporary_entries) > _MAX_SPOOL_ENTRIES:
                    raise _invalid_spool()

                binding = self._read_binding(descriptor)
                if self.binding_id is None:
                    if binding is not None:
                        raise _invalid_spool()
                elif binding is None:
                    # A never-used historical path is harmless. Do not claim it
                    # or delete even one unbound ciphertext file without proof.
                    if source_entries or temporary_entries:
                        raise _invalid_spool()
                    return 0
                elif binding != self._binding_payload():
                    raise _invalid_spool()

                # Recheck every name before deleting any entry. A concurrent
                # replacement with a symlink, hard link, or unrelated file must
                # abort the whole clear before the first unlink.
                deletable_entries = source_entries + temporary_entries
                for name, expected in deletable_entries:
                    current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                    _validate_private_file(current)
                    if signature(current) != expected:
                        raise _invalid_spool()
                for name, expected in deletable_entries:
                    current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                    _validate_private_file(current)
                    if signature(current) != expected:
                        raise _invalid_spool()
                    os.unlink(name, dir_fd=descriptor)
                if deletable_entries:
                    os.fsync(descriptor)
                return len(deletable_entries)
            except EncryptedTextSpoolError:
                raise
            except OSError:
                raise _invalid_spool() from None
            finally:
                if descriptor is not None:
                    os.close(descriptor)

    def pending_source_ids(
        self, *, limit: int = 32, after_source_id: str | None = None
    ) -> tuple[str, ...]:
        """List a bounded recovery batch after removing expired ciphertext.

        This exposes only source identities.  A caller must still recheck the
        source and its permissions before reading or sending any text. When a
        cursor is supplied, traversal wraps around the ordered source IDs.
        """
        if type(limit) is not int or not 1 <= limit <= 256:
            raise _invalid_spool()
        if after_source_id is not None:
            self._validate_source_id(after_source_id)
        self.purge_expired()
        with self._locked():
            _, entries = self._advance_clock(self._read_key())
            ordered = sorted(entries)
            if after_source_id is None or not ordered:
                return tuple(ordered[:limit])
            start = bisect_right(ordered, after_source_id)
            rotated = ordered[start:] + ordered[:start]
            return tuple(rotated[:limit])

    def next_expiry(self) -> float | None:
        """Return the earliest authenticated original deadline after purging due entries."""
        self.purge_expired()
        with self._locked():
            key = self._read_key()
            _, entries = self._advance_clock(key)
            deadlines = [self._read_entry(source_id, key)[2] for source_id in entries]
            return min(deadlines) if deadlines else None

    def _cleaner_identity(self) -> str:
        paths = f"{self.ciphertext_dir}\0{self.key_file}\0{self.binding_id}".encode()
        return hashlib.sha256(paths).hexdigest()

    def _write_cleaner_status(self, timestamp: float, run_token: str) -> None:
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "identity": self._cleaner_identity(),
                "heartbeat_at": timestamp,
                "run_token": run_token,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > _CLEANER_STATUS_MAX_BYTES:
            raise _invalid_spool()
        with self._locked():
            self._atomic_write(
                self.ciphertext_dir / _CLEANER_STATUS_NAME, payload, replace=True
            )

    def cleaner_available(self) -> bool:
        """Require a fresh heartbeat and a lock held by a separate process.

        The OS supervisor must restart that process if it exits after this
        check. A successful probe alone cannot guarantee a future deadline.
        """
        try:
            with self._locked():
                self._require_binding()
                status_path = self.ciphertext_dir / _CLEANER_STATUS_NAME
                metadata = _lstat_optional(status_path)
                if metadata is None:
                    return False
                _validate_private_file(metadata)
                if metadata.st_size > _CLEANER_STATUS_MAX_BYTES:
                    return False
                descriptor = os.open(status_path, os.O_RDONLY | _NOFOLLOW)
                try:
                    _validate_private_file(os.fstat(descriptor))
                    raw = _read_descriptor(descriptor, _CLEANER_STATUS_MAX_BYTES)
                finally:
                    os.close(descriptor)
            decoded: object = json.loads(raw)
            if not isinstance(decoded, dict):
                return False
            status = cast(dict[str, object], decoded)
            pid = status.get("pid")
            identity = status.get("identity")
            heartbeat_at = status.get("heartbeat_at")
            run_token = status.get("run_token")
            now = self._clock()
            if (
                set(status) != {"pid", "identity", "heartbeat_at", "run_token"}
                or type(pid) is not int
                or pid <= 0
                or pid == os.getpid()
                or identity != self._cleaner_identity()
                or type(run_token) is not str
                or re.fullmatch(r"[0-9a-f]{32}", run_token) is None
                or type(heartbeat_at) not in {int, float}
                or type(now) not in {int, float}
                or not math.isfinite(float(now))
                or not 0 <= float(now) - cast(float, heartbeat_at) <= _CLEANER_MAX_AGE_SECONDS
            ):
                return False
            descriptor = os.open(
                self.ciphertext_dir / _CLEANER_LOCK_NAME, os.O_RDWR | _NOFOLLOW
            )
            with os.fdopen(descriptor, "r+b") as handle:
                _validate_private_file(os.fstat(handle.fileno()))
                held_token = _read_descriptor(handle.fileno(), 64)
                if held_token != run_token.encode():
                    return False
                try:
                    portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
                except portalocker.LockException:
                    return True
                portalocker.unlock(handle)
                return False
        except (EncryptedTextSpoolError, OSError, ValueError, TypeError, OverflowError):
            return False

    def serve_deadline_cleaner(self, stop: Event) -> None:
        """Run an independent cleaner, waking at each original expiry.

        Heartbeats also discover the first entry after an empty spool. Once an
        entry is known, later puts cannot have an earlier deadline because the
        authenticated spool clock does not move backward.
        """
        self._ensure_directory()
        if self.binding_id is not None:
            if self._read_binding() is None:
                # Claim only an empty, key-readable directory. Existing legacy
                # ciphertext without a marker needs explicit offline handling.
                self._read_key()
                self.next_expiry()
            else:
                self._require_binding()
        lock_path = self.ciphertext_dir / _CLEANER_LOCK_NAME
        descriptor: int | None = None
        try:
            try:
                descriptor = os.open(
                    lock_path, os.O_RDWR | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600
                )
                os.fchmod(descriptor, 0o600)
            except FileExistsError:
                descriptor = os.open(lock_path, os.O_RDWR | _NOFOLLOW)
            _validate_private_file(os.fstat(descriptor))
            with os.fdopen(descriptor, "r+b") as handle:
                descriptor = None
                portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
                try:
                    with self._locked():
                        stale_status = self.ciphertext_dir / _CLEANER_STATUS_NAME
                        if _lstat_optional(stale_status) is not None:
                            _validate_private_file(os.lstat(stale_status))
                            os.unlink(stale_status)
                            _sync_directory(self.ciphertext_dir)
                    # This is an explicitly configured, private, dedicated
                    # spool. If its key is already lost on service restart,
                    # delete strict entries early rather than retain them past
                    # an expiry that can no longer be authenticated.
                    directory_before = os.lstat(self.ciphertext_dir)
                    _validate_private_directory(directory_before)
                    directory_identity = (directory_before.st_dev, directory_before.st_ino)
                    try:
                        expected_key = self._read_key()
                        deadline = self.next_expiry()
                    except EncryptedTextSpoolError:
                        self.clear(expected_directory_identity=directory_identity)
                        raise
                    directory_after = os.lstat(self.ciphertext_dir)
                    _validate_private_directory(directory_after)
                    if (directory_after.st_dev, directory_after.st_ino) != directory_identity:
                        raise _invalid_spool()

                    def key_is_current() -> bool:
                        try:
                            return self._read_key() == expected_key
                        except EncryptedTextSpoolError:
                            return False

                    def clear_after_confirmed_key_loss() -> None:
                        # A second check distinguishes a one-off read failure
                        # from a key that remains absent, unreadable, or changed.
                        if key_is_current():
                            return
                        self.clear(expected_directory_identity=directory_identity)
                        raise _invalid_spool()

                    run_token = secrets.token_hex(16)
                    handle.seek(0)
                    handle.truncate()
                    handle.write(run_token.encode())
                    handle.flush()
                    os.fsync(handle.fileno())
                    while not stop.is_set():
                        if not key_is_current():
                            clear_after_confirmed_key_loss()
                        now = self._clock()
                        if type(now) not in {int, float} or not math.isfinite(float(now)):
                            raise _invalid_spool()
                        self._write_cleaner_status(float(now), run_token)
                        delay = _CLEANER_HEARTBEAT_SECONDS
                        if deadline is not None:
                            delay = min(delay, max(0.0, deadline - float(now)))
                        if stop.wait(delay):
                            break
                        now = self._clock()
                        if type(now) not in {int, float} or not math.isfinite(float(now)):
                            raise _invalid_spool()
                        if deadline is None or float(now) >= deadline:
                            try:
                                deadline = self.next_expiry()
                            except EncryptedTextSpoolError:
                                if not key_is_current():
                                    clear_after_confirmed_key_loss()
                                raise
                finally:
                    with self._locked():
                        status_path = self.ciphertext_dir / _CLEANER_STATUS_NAME
                        if _lstat_optional(status_path) is not None:
                            _validate_private_file(os.lstat(status_path))
                            os.unlink(status_path)
                            _sync_directory(self.ciphertext_dir)
                    portalocker.unlock(handle)
        except EncryptedTextSpoolError:
            raise
        except (OSError, ValueError, portalocker.LockException):
            raise _invalid_spool() from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _validate_source_id(source_id: str) -> None:
        if type(source_id) is not str or _SOURCE_ID.fullmatch(source_id) is None:
            raise _invalid_spool()

    def _entry_path(self, source_id: str) -> Path:
        return self.ciphertext_dir / f"{source_id}{_ENTRY_SUFFIX}"

    @staticmethod
    def _entry_aad(
        source_id: str, timestamp_bytes: bytes, magic: bytes = _ENTRY_MAGIC
    ) -> bytes:
        return magic + b"\x00" + source_id.encode("ascii") + b"\x00" + timestamp_bytes

    def _ensure_directory(self) -> None:
        _check_path_components(self.ciphertext_dir)
        previous = _lstat_optional(self.ciphertext_dir)
        try:
            if previous is None:
                self.ciphertext_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
            descriptor = os.open(
                self.ciphertext_dir,
                os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
            )
            try:
                opened = os.fstat(descriptor)
                if previous is None:
                    os.fchmod(descriptor, 0o700)
                    opened = os.fstat(descriptor)
                _validate_private_directory(opened)
            finally:
                os.close(descriptor)
            _check_path_components(self.ciphertext_dir)
        except EncryptedTextSpoolError:
            raise
        except OSError:
            raise _invalid_spool() from None

    def _open_lock(self) -> int:
        path = self.ciphertext_dir / _LOCK_NAME
        descriptor: int | None = None
        try:
            try:
                descriptor = os.open(
                    path,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
                    0o600,
                )
                os.fchmod(descriptor, 0o600)
            except FileExistsError:
                descriptor = os.open(path, os.O_RDWR | _NOFOLLOW)
            _validate_private_file(os.fstat(descriptor))
            result = descriptor
            descriptor = None
            return result
        except EncryptedTextSpoolError:
            raise
        except OSError:
            raise _invalid_spool() from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @contextmanager
    def _locked(self) -> Generator[None, None, None]:
        self._ensure_directory()
        descriptor = self._open_lock()
        handle = os.fdopen(descriptor, "r+b")
        try:
            portalocker.lock(handle, portalocker.LOCK_EX)
            self._ensure_directory()
            yield
        except EncryptedTextSpoolError:
            raise
        except (OSError, portalocker.LockException, ValueError):
            raise _invalid_spool() from None
        finally:
            try:
                portalocker.unlock(handle)
            except (OSError, portalocker.LockException):
                pass
            handle.close()

    def _read_key(self) -> bytes:
        _check_path_components(self.key_file)
        descriptor: int | None = None
        try:
            descriptor = os.open(self.key_file, os.O_RDONLY | _NOFOLLOW)
            _validate_private_file(os.fstat(descriptor))
            key = _read_descriptor(descriptor, _KEY_BYTES)
            if len(key) != _KEY_BYTES:
                raise _invalid_spool()
            return key
        except EncryptedTextSpoolError:
            raise
        except OSError:
            raise _invalid_spool() from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _binding_payload(self) -> bytes:
        if self.binding_id is None:
            raise _invalid_spool()
        return _BINDING_MAGIC + self.binding_id.encode("ascii")

    def _read_binding(self, directory_fd: int | None = None) -> bytes | None:
        descriptor: int | None = None
        try:
            if directory_fd is None:
                descriptor = os.open(
                    self.ciphertext_dir / _BINDING_NAME, os.O_RDONLY | _NOFOLLOW
                )
            else:
                descriptor = os.open(
                    _BINDING_NAME, os.O_RDONLY | _NOFOLLOW, dir_fd=directory_fd
                )
            metadata = os.fstat(descriptor)
            _validate_private_file(metadata)
            expected_size = len(_BINDING_MAGIC) + 64
            if metadata.st_size != expected_size:
                raise _invalid_spool()
            return _read_descriptor(descriptor, expected_size)
        except FileNotFoundError:
            return None
        except EncryptedTextSpoolError:
            raise
        except OSError:
            raise _invalid_spool() from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _require_binding(self, directory_fd: int | None = None) -> None:
        actual = self._read_binding(directory_fd)
        if self.binding_id is None:
            if actual is not None:
                raise _invalid_spool()
        elif actual != self._binding_payload():
            raise _invalid_spool()

    def _scan_entries(self) -> dict[str, int]:
        entries: dict[str, int] = {}
        temporary_paths: list[Path] = []
        clock_present = False
        binding_present = False
        try:
            with os.scandir(self.ciphertext_dir) as children:
                for child in children:
                    path = self.ciphertext_dir / child.name
                    if child.name in {
                        _CLOCK_NAME, _LOCK_NAME, _CLEANER_LOCK_NAME,
                        _CLEANER_STATUS_NAME,
                    }:
                        if child.name == _CLOCK_NAME:
                            clock_present = True
                        self._validate_existing_file(
                            path,
                            max_size=(
                                _CLEANER_STATUS_MAX_BYTES
                                if child.name == _CLEANER_STATUS_NAME else None
                            ),
                        )
                    elif child.name == _BINDING_NAME:
                        self._validate_existing_file(
                            path, max_size=len(_BINDING_MAGIC) + 64
                        )
                        binding_present = True
                    elif _TEMP_ENTRY_NAME.fullmatch(child.name) is not None:
                        self._validate_existing_file(path, max_size=_ENTRY_MAX_BYTES)
                        temporary_paths.append(path)
                    else:
                        match = _ENTRY_NAME.fullmatch(child.name)
                        if match is None:
                            raise _invalid_spool()
                        metadata = self._validate_existing_file(path, max_size=_ENTRY_MAX_BYTES)
                        entries[match.group(1)] = metadata.st_size
        except EncryptedTextSpoolError:
            raise
        except OSError:
            raise _invalid_spool() from None

        if binding_present:
            self._require_binding()
        elif self.binding_id is not None:
            if entries or clock_present or temporary_paths:
                raise _invalid_spool()
            self._atomic_write(
                self.ciphertext_dir / _BINDING_NAME,
                self._binding_payload(),
                replace=False,
            )

        if temporary_paths:
            try:
                for path in temporary_paths:
                    _validate_private_file(os.lstat(path))
                    os.unlink(path)
                _sync_directory(self.ciphertext_dir)
            except EncryptedTextSpoolError:
                raise
            except OSError:
                raise _invalid_spool() from None
        return entries

    @staticmethod
    def _validate_existing_file(path: Path, *, max_size: int | None = None) -> os.stat_result:
        try:
            metadata = os.lstat(path)
            _validate_private_file(metadata)
            if max_size is not None and metadata.st_size > max_size:
                raise _invalid_spool()
            return metadata
        except EncryptedTextSpoolError:
            raise
        except OSError:
            raise _invalid_spool() from None

    def _advance_clock(self, key: bytes) -> tuple[float, dict[str, int]]:
        entries = self._scan_entries()
        try:
            now = self._clock()
        except Exception:
            raise _invalid_spool() from None
        if type(now) not in (int, float) or not math.isfinite(now):
            raise _invalid_spool()
        now = float(now)
        watermark = self._read_watermark(key, has_entries=bool(entries), now=now)
        if now < watermark:
            raise _invalid_spool()
        if now > watermark:
            self._write_watermark(key, now)
        return now, entries

    def _read_watermark(self, key: bytes, *, has_entries: bool, now: float) -> float:
        path = self.ciphertext_dir / _CLOCK_NAME
        if _lstat_optional(path) is None:
            if has_entries:
                raise _invalid_spool()
            self._write_watermark(key, now)
            return now
        descriptor: int | None = None
        try:
            descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
            _validate_private_file(os.fstat(descriptor))
            raw = _read_descriptor(descriptor, _CLOCK_FILE_BYTES)
            if len(raw) != _CLOCK_FILE_BYTES or not raw.startswith(_CLOCK_MAGIC):
                raise _invalid_spool()
            nonce_start = len(_CLOCK_MAGIC)
            nonce = raw[nonce_start : nonce_start + _NONCE_BYTES]
            ciphertext = raw[nonce_start + _NONCE_BYTES :]
            encoded = AESGCM(key).decrypt(nonce, ciphertext, _CLOCK_AAD)
            if len(encoded) != 8:
                raise _invalid_spool()
            watermark = struct.unpack(">d", encoded)[0]
            if not math.isfinite(watermark):
                raise _invalid_spool()
            return watermark
        except EncryptedTextSpoolError:
            raise
        except (InvalidTag, OSError, ValueError):
            raise _invalid_spool() from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _write_watermark(self, key: bytes, watermark: float) -> None:
        nonce = os.urandom(_NONCE_BYTES)
        encrypted = AESGCM(key).encrypt(nonce, struct.pack(">d", watermark), _CLOCK_AAD)
        self._atomic_write(
            self.ciphertext_dir / _CLOCK_NAME,
            _CLOCK_MAGIC + nonce + encrypted,
            replace=True,
        )

    def _read_entry(self, source_id: str, key: bytes) -> tuple[str, float, float, bytes]:
        path = self._entry_path(source_id)
        descriptor: int | None = None
        try:
            descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
            _validate_private_file(os.fstat(descriptor))
            raw = _read_descriptor(descriptor, _ENTRY_MAX_BYTES)
            magic = (
                _ENTRY_MAGIC if raw.startswith(_ENTRY_MAGIC) else
                _DECISION_MAGIC if raw.startswith(_DECISION_MAGIC) else None
            )
            if magic is None:
                raise _invalid_spool()
            header_length = len(magic) + 16
            if len(raw) < header_length + _NONCE_BYTES + _TAG_BYTES:
                raise _invalid_spool()
            timestamp_start = len(magic)
            timestamp_bytes = raw[timestamp_start : timestamp_start + 16]
            captured_at, expires_at = struct.unpack(">dd", timestamp_bytes)
            if (
                not math.isfinite(captured_at)
                or not math.isfinite(expires_at)
                or expires_at != captured_at + _TTL_SECONDS
            ):
                raise _invalid_spool()
            nonce_start = header_length
            nonce = raw[nonce_start : nonce_start + _NONCE_BYTES]
            encrypted = raw[nonce_start + _NONCE_BYTES :]
            plaintext = AESGCM(key).decrypt(
                nonce,
                encrypted,
                self._entry_aad(source_id, timestamp_bytes, magic),
            )
            if len(plaintext) > (_MAX_TEXT_BYTES if magic == _ENTRY_MAGIC else _MAX_DECISION_BYTES):
                raise _invalid_spool()
            plaintext.decode("utf-8", errors="strict")
            return "text" if magic == _ENTRY_MAGIC else "decision", captured_at, expires_at, plaintext
        except EncryptedTextSpoolError:
            raise
        except (InvalidTag, OSError, UnicodeDecodeError, ValueError):
            raise _invalid_spool() from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _atomic_write(self, path: Path, content: bytes, *, replace: bool) -> None:
        descriptor: int | None = None
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(prefix=_TEMP_PREFIX, dir=self.ciphertext_dir)
            temporary_path = Path(temporary_name)
            os.fchmod(descriptor, 0o600)
            _validate_private_file(os.fstat(descriptor))
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = None
                if handle.write(content) != len(content):
                    raise _invalid_spool()
                handle.flush()
                os.fsync(handle.fileno())

            current = _lstat_optional(path)
            if current is not None:
                _validate_private_file(current)
                if not replace:
                    raise _invalid_spool()
            elif replace and path.name == _CLOCK_NAME:
                # The first watermark is created atomically; later writes replace it.
                pass
            if replace:
                os.replace(temporary_path, path)
            else:
                os.rename(temporary_path, path)
            temporary_path = None
            _sync_directory(self.ciphertext_dir)
        except EncryptedTextSpoolError:
            raise
        except OSError:
            raise _invalid_spool() from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass
