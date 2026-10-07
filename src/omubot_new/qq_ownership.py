"""Persistent account binding and one executor on this OS user's host."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import BinaryIO

import portalocker

from .types import OperationError


def qq_registry_directory() -> Path:
    """A user-wide location, independent of the checkout or instance directory."""
    if sys.platform == "win32":
        base = Path(os.environ["LOCALAPPDATA"])
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
    return base / "Omubot" / "qq-accounts"


class QQAccountOwnership:
    """Keep the persistent binding after releasing the lifetime execution lock."""

    def __init__(
        self,
        account_id: str,
        instance_id: str,
        database_id: str,
        database_path: Path,
        *,
        registry_dir: Path | None = None,
    ) -> None:
        if any(not value or value != value.strip() for value in (account_id, instance_id, database_id)):
            raise ValueError("account ownership requires exact identities")
        self.account_id = account_id
        self.registry_dir = registry_dir if registry_dir is not None else qq_registry_directory()
        self._binding = {
            "version": 1,
            "account_id": account_id,
            "instance_id": instance_id,
            "database_id": database_id,
            "database_path": str(database_path.resolve()),
        }
        self._key = hashlib.sha256(account_id.encode("utf-8")).hexdigest()
        self._lock: BinaryIO | None = None

    @property
    def owned(self) -> bool:
        return self._lock is not None

    def acquire(self) -> None:
        if self._lock is not None:
            raise RuntimeError("account ownership already acquired")
        self.registry_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.registry_dir.is_symlink():
            raise OperationError("qq_account_registry_invalid")
        lock_path = self.registry_dir / f"{self._key}.lock"
        binding_path = self.registry_dir / f"{self._key}.json"
        if lock_path.is_symlink() or binding_path.is_symlink():
            raise OperationError("qq_account_registry_invalid")
        handle = lock_path.open("a+b")
        try:
            portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
        except portalocker.LockException as exc:
            handle.close()
            raise OperationError("qq_account_owned") from exc
        self._lock = handle
        try:
            if binding_path.exists():
                try:
                    existing: object = json.loads(binding_path.read_text(encoding="utf-8"))
                except (ValueError, UnicodeError) as exc:
                    raise OperationError("qq_account_registry_invalid") from exc
                if existing != self._binding:
                    raise OperationError("qq_account_binding_conflict")
            else:
                # The execution lock serializes initial registration. Atomic replace
                # prevents a crash from leaving a partially readable identity.
                temporary = binding_path.with_suffix(".pending")
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                        json.dump(self._binding, stream, separators=(",", ":"), sort_keys=True)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, binding_path)
                finally:
                    temporary.unlink(missing_ok=True)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        handle = self._lock
        if handle is None:
            return
        self._lock = None
        try:
            portalocker.unlock(handle)
        finally:
            handle.close()
